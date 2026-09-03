"""Split-safe isotonic and Platt calibration without heavyweight ML dependencies."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, cast

CalibrationSplit = Literal["train", "validation", "test"]


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    split: CalibrationSplit
    raw_score: float
    error_rate: float
    features: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    schema_version: int
    model_id: str
    model_revision: str
    language: str
    scenario: str
    manifest_sha256: str
    method: Literal["isotonic", "platt"]
    error_threshold: float
    parameters: dict[str, object]
    validation_brier: float
    test_brier: float
    training_sha256: str
    created_at: str

    def predict(self, raw_score: float, features: tuple[float, ...] = ()) -> float:
        if self.method == "isotonic":
            thresholds = _float_values(self.parameters, "thresholds")
            values = _float_values(self.parameters, "values")
            for threshold, value in zip(thresholds, values, strict=True):
                if raw_score <= threshold:
                    return value
            return values[-1]
        coefficients = _float_values(self.parameters, "coefficients")
        inputs = (1.0, raw_score, *features)
        if len(inputs) != len(coefficients):
            raise ValueError("calibration feature count differs from the fitted artifact")
        return _sigmoid(
            sum(
                coefficient * value for coefficient, value in zip(coefficients, inputs, strict=True)
            )
        )

    def valid_for(self, *, model_revision: str, manifest_sha256: str) -> bool:
        return self.model_revision == model_revision and self.manifest_sha256 == manifest_sha256

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def fit_calibration(
    samples: tuple[CalibrationSample, ...],
    *,
    model_id: str,
    model_revision: str,
    language: str,
    scenario: str,
    manifest_sha256: str,
    error_threshold: float,
    method: Literal["auto", "isotonic", "platt"] = "auto",
) -> CalibrationArtifact:
    if not 0 <= error_threshold <= 1:
        raise ValueError("calibration error threshold must be between zero and one")
    if language not in {"zh", "ja", "en"} or scenario not in {"classroom", "ibus"}:
        raise ValueError("calibration requires a concrete language and scenario")
    partitions = {
        split: tuple(item for item in samples if item.split == split)
        for split in ("train", "validation", "test")
    }
    if any(not values for values in partitions.values()):
        raise ValueError("calibration requires non-empty train, validation, and test splits")
    feature_count = len(samples[0].features)
    if any(len(item.features) != feature_count for item in samples):
        raise ValueError("calibration samples must have a stable feature vector length")

    def labels(values: tuple[CalibrationSample, ...]) -> tuple[float, ...]:
        return tuple(1.0 if item.error_rate <= error_threshold else 0.0 for item in values)

    candidates: list[tuple[str, dict[str, object], float]] = []
    if method in {"auto", "isotonic"}:
        isotonic = _fit_isotonic(partitions["train"], labels(partitions["train"]))
        candidates.append(
            (
                "isotonic",
                isotonic,
                _brier(
                    _predict_isotonic(isotonic, partitions["validation"]),
                    labels(partitions["validation"]),
                ),
            )
        )
    if method in {"auto", "platt"}:
        platt = _fit_platt(partitions["train"], labels(partitions["train"]))
        candidates.append(
            (
                "platt",
                platt,
                _brier(
                    _predict_platt(platt, partitions["validation"]),
                    labels(partitions["validation"]),
                ),
            )
        )
    selected_method, parameters, validation_brier = min(
        candidates, key=lambda item: (item[2], item[0])
    )
    test_predictions = (
        _predict_isotonic(parameters, partitions["test"])
        if selected_method == "isotonic"
        else _predict_platt(parameters, partitions["test"])
    )
    training_payload = [asdict(item) for item in partitions["train"]]
    training_sha256 = hashlib.sha256(
        json.dumps(training_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CalibrationArtifact(
        schema_version=1,
        model_id=model_id,
        model_revision=model_revision,
        language=language,
        scenario=scenario,
        manifest_sha256=manifest_sha256,
        method=cast(Literal["isotonic", "platt"], selected_method),
        error_threshold=error_threshold,
        parameters=parameters,
        validation_brier=validation_brier,
        test_brier=_brier(test_predictions, labels(partitions["test"])),
        training_sha256=training_sha256,
        created_at=datetime.now(UTC).isoformat(),
    )


def _fit_isotonic(
    samples: tuple[CalibrationSample, ...], labels: tuple[float, ...]
) -> dict[str, object]:
    ordered = sorted(zip(samples, labels, strict=True), key=lambda item: item[0].raw_score)
    blocks: list[list[float]] = []
    for sample, label in ordered:
        blocks.append([sample.raw_score, sample.raw_score, label, 1.0])
        while len(blocks) >= 2 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])
    return {
        "thresholds": [block[1] for block in blocks],
        "values": [block[2] / block[3] for block in blocks],
    }


def _predict_isotonic(
    parameters: dict[str, object], samples: tuple[CalibrationSample, ...]
) -> tuple[float, ...]:
    thresholds = _float_values(parameters, "thresholds")
    values = _float_values(parameters, "values")
    result: list[float] = []
    for sample in samples:
        value = values[-1]
        for threshold, candidate in zip(thresholds, values, strict=True):
            if sample.raw_score <= threshold:
                value = candidate
                break
        result.append(value)
    return tuple(result)


def _fit_platt(
    samples: tuple[CalibrationSample, ...], labels: tuple[float, ...]
) -> dict[str, object]:
    coefficients = [0.0] * (2 + len(samples[0].features))
    learning_rate = 0.2
    for iteration in range(800):
        gradients = [0.0] * len(coefficients)
        for sample, label in zip(samples, labels, strict=True):
            inputs = (1.0, sample.raw_score, *sample.features)
            error = _sigmoid(sum(a * b for a, b in zip(coefficients, inputs, strict=True))) - label
            for index, value in enumerate(inputs):
                gradients[index] += error * value
        scale = learning_rate / len(samples) / math.sqrt(1 + iteration / 100)
        for index, gradient in enumerate(gradients):
            regularization = 0.001 * coefficients[index] if index else 0.0
            coefficients[index] -= scale * (gradient + regularization)
    return {"coefficients": coefficients, "feature_count": len(samples[0].features)}


def _predict_platt(
    parameters: dict[str, object], samples: tuple[CalibrationSample, ...]
) -> tuple[float, ...]:
    coefficients = _float_values(parameters, "coefficients")
    return tuple(
        _sigmoid(
            sum(
                coefficient * value
                for coefficient, value in zip(
                    coefficients, (1.0, sample.raw_score, *sample.features), strict=True
                )
            )
        )
        for sample in samples
    )


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = math.exp(-value)
        return 1 / (1 + factor)
    factor = math.exp(value)
    return factor / (1 + factor)


def _brier(predictions: tuple[float, ...], labels: tuple[float, ...]) -> float:
    return sum(
        (prediction - label) ** 2 for prediction, label in zip(predictions, labels, strict=True)
    ) / len(labels)


def _float_values(parameters: dict[str, object], key: str) -> tuple[float, ...]:
    value = parameters.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"calibration parameter {key} must be a non-empty array")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value):
        raise ValueError(f"calibration parameter {key} must contain numbers")
    return tuple(float(item) for item in value)
