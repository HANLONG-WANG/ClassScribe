"""Deterministic offline scorer/runner for already-pinned model predictions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from classscribe.benchmark.calibration import CalibrationSample, fit_calibration
from classscribe.benchmark.metrics import (
    PerformanceObservation,
    aggregate_performance,
    score_safety,
    score_speakers,
    score_text,
    score_timeline,
)
from classscribe.benchmark.models import BenchmarkPrediction, GoldCoverage, GoldRecord
from classscribe.benchmark.ranking import RankingPolicy, rank_candidates


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    schema_version: int
    manifest_version: str
    manifest_sha256: str
    status: str
    hardware: dict[str, object]
    parameters: dict[str, object]
    coverage: dict[str, object]
    items: tuple[dict[str, object], ...]
    calibrations: tuple[dict[str, object], ...]
    rankings: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def write(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.as_dict(), handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class BenchmarkRunner:
    def __init__(
        self,
        records: tuple[GoldRecord, ...],
        *,
        manifest_version: str,
        manifest_sha256: str | None = None,
        hardware: dict[str, object] | None = None,
        parameters: dict[str, object] | None = None,
        production_gold: bool = True,
        ranking_policy: RankingPolicy | None = None,
    ) -> None:
        if not manifest_version:
            raise ValueError("manifest version must be non-empty")
        self.records = records
        self.coverage = GoldCoverage.inspect(records)
        self.coverage.require(production=production_gold)
        self.manifest_version = manifest_version
        self.manifest_sha256 = manifest_sha256 or _records_hash(records)
        self.hardware = hardware or {}
        self.parameters = {**(parameters or {}), "production_gold": production_gold}
        self.ranking_policy = ranking_policy or RankingPolicy()

    async def execute(
        self,
        models: tuple[tuple[str, str], ...],
        predictor: Callable[[GoldRecord, str, str], Awaitable[BenchmarkPrediction]],
    ) -> BenchmarkReport:
        """Invoke one isolated provider contract for every pinned model and gold item."""

        if not models or len(models) != len(set(models)):
            raise ValueError("benchmark model/revision routes must be non-empty and unique")
        predictions: list[BenchmarkPrediction] = []
        for model_id, revision in models:
            if not model_id or not revision:
                raise ValueError("benchmark model routes require model ID and revision")
            for record in self.records:
                prediction = await predictor(record, model_id, revision)
                if (
                    prediction.item_id != record.item_id
                    or prediction.model_id != model_id
                    or prediction.model_revision != revision
                ):
                    raise ValueError("benchmark predictor returned a mismatched identity")
                predictions.append(prediction)
        return self.run(tuple(predictions))

    def run(self, predictions: tuple[BenchmarkPrediction, ...]) -> BenchmarkReport:
        gold_by_id = {item.item_id: item for item in self.records}
        prediction_identities = [
            (item.item_id, item.model_id, item.model_revision) for item in predictions
        ]
        if len(prediction_identities) != len(set(prediction_identities)):
            raise ValueError("prediction item/model/revision rows must be unique")
        prediction_keys = {(item.model_id, item.model_revision) for item in predictions}
        if not prediction_keys:
            raise ValueError("benchmark requires at least one model prediction set")
        scored: list[dict[str, object]] = []
        candidate_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        calibrations: list[dict[str, object]] = []
        for model_id, revision in sorted(prediction_keys):
            model_predictions = tuple(
                item
                for item in predictions
                if item.model_id == model_id and item.model_revision == revision
            )
            by_id = {item.item_id: item for item in model_predictions}
            if extra := sorted(set(by_id) - set(gold_by_id)):
                raise ValueError(f"predictions contain unknown gold IDs for {model_id}: {extra}")
            covered_groups = {
                (gold_by_id[item_id].language, gold_by_id[item_id].scenario) for item_id in by_id
            }
            for language in ("zh", "ja", "en"):
                for scenario in ("classroom", "ibus"):
                    if (language, scenario) not in covered_groups:
                        continue
                    group_records = tuple(
                        item
                        for item in self.records
                        if item.language == language and item.scenario == scenario
                    )
                    if not group_records:
                        continue
                    expected_ids = {item.item_id for item in group_records}
                    supplied_ids = expected_ids & set(by_id)
                    if supplied_ids != expected_ids:
                        missing = sorted(expected_ids - supplied_ids)
                        raise ValueError(
                            f"prediction coverage is incomplete for "
                            f"{model_id}/{language}/{scenario}: {missing}"
                        )
                    group_scored: list[dict[str, object]] = []
                    calibration_samples: list[CalibrationSample] = []
                    for gold in group_records:
                        prediction = by_id[gold.item_id]
                        item_metrics = _score_item(gold, prediction)
                        entry: dict[str, object] = {
                            "item_id": gold.item_id,
                            "split": gold.split,
                            "scenario": scenario,
                            "language": language,
                            "model_id": model_id,
                            "model_revision": revision,
                            "gold": asdict(gold),
                            "prediction": asdict(prediction),
                            "metrics": item_metrics,
                        }
                        scored.append(entry)
                        group_scored.append(entry)
                        if prediction.confidence is None:
                            raise ValueError(
                                f"calibration confidence missing for {model_id}/{gold.item_id}"
                            )
                        body_key = "normalized_wer" if language == "en" else "normalized_cer"
                        calibration_samples.append(
                            CalibrationSample(
                                gold.split,
                                prediction.confidence,
                                _metric_number(item_metrics, body_key),
                                prediction.quality_features,
                            )
                        )
                    calibration = fit_calibration(
                        tuple(calibration_samples),
                        model_id=model_id,
                        model_revision=revision,
                        language=language,
                        scenario=scenario,
                        manifest_sha256=self.manifest_sha256,
                        error_threshold=0.2,
                    )
                    calibration_value = calibration.as_dict()
                    calibrations.append(calibration_value)
                    test_entries = tuple(
                        entry for entry in group_scored if entry["split"] == "test"
                    )
                    test_metrics = _aggregate_metrics(
                        tuple(
                            entry["metrics"]
                            for entry in test_entries
                            if isinstance(entry["metrics"], dict)
                        )
                    )
                    test_predictions = tuple(by_id[str(entry["item_id"])] for entry in test_entries)
                    test_gold = tuple(gold_by_id[str(entry["item_id"])] for entry in test_entries)
                    test_metrics.update(
                        aggregate_performance(
                            tuple(
                                PerformanceObservation(
                                    gold.duration_seconds,
                                    prediction.runtime_seconds,
                                    prediction.peak_vram_mb,
                                    prediction.peak_ram_mb,
                                    prediction.interim_latency_ms,
                                    prediction.commit_latency_ms,
                                    prediction.load_seconds,
                                    prediction.preemption_resume_ms,
                                )
                                for gold, prediction in zip(
                                    test_gold, test_predictions, strict=True
                                )
                            )
                        )
                    )
                    candidate_groups[(language, scenario)].append(
                        {
                            "model_id": model_id,
                            "model_revision": revision,
                            "language": language,
                            "scenario": scenario,
                            "metrics": test_metrics,
                            "calibration": calibration_value,
                        }
                    )
        rankings: list[dict[str, object]] = []
        for (language, scenario), candidates in sorted(candidate_groups.items()):
            ranked = rank_candidates(
                tuple(candidates),
                language=language,
                scenario=scenario,
                policy=self.ranking_policy,
            )
            eligible = [str(item["model_id"]) for item in ranked if item["eligible"]]
            rankings.append(
                {
                    "language": language,
                    "scenario": scenario,
                    "models": eligible,
                    "candidates": list(ranked),
                    "passed": bool(eligible),
                }
            )
        expected_groups = {(item.language, item.scenario) for item in self.records}
        ranked_groups = {(str(item["language"]), str(item["scenario"])) for item in rankings}
        status = (
            "completed"
            if ranked_groups == expected_groups and all(item["passed"] for item in rankings)
            else "failed"
        )
        return BenchmarkReport(
            schema_version=1,
            manifest_version=self.manifest_version,
            manifest_sha256=self.manifest_sha256,
            status=status,
            hardware=self.hardware,
            parameters=self.parameters,
            coverage=asdict(self.coverage),
            items=tuple(scored),
            calibrations=tuple(calibrations),
            rankings=tuple(rankings),
        )


def _score_item(gold: GoldRecord, prediction: BenchmarkPrediction) -> dict[str, object]:
    text = score_text(
        gold.text,
        prediction.text,
        gold.language,
        terms=gold.terms,
        term_vocabulary=gold.term_vocabulary,
        entities=gold.entities,
    )
    safety = score_safety(
        gold.text,
        prediction.text,
        duration_seconds=gold.duration_seconds,
        tags=gold.tags,
        voiced_spans=gold.voiced_spans,
        predicted_spans=prediction.voiced_spans,
    )
    timeline = score_timeline(
        gold.words,
        prediction.words,
        reference_sentence_boundaries=gold.sentence_boundaries,
        predicted_sentence_boundaries=prediction.sentence_boundaries,
        start_sample=gold.start_sample,
        duration_samples=gold.end_sample,
    )
    speakers = score_speakers(
        gold.speaker_spans,
        prediction.speaker_spans,
        reference_words=gold.words,
        predicted_words=prediction.words,
        language=gold.language,
        cross_window_speakers=prediction.cross_window_speakers,
    )
    performance = aggregate_performance(
        (
            PerformanceObservation(
                gold.duration_seconds,
                prediction.runtime_seconds,
                prediction.peak_vram_mb,
                prediction.peak_ram_mb,
                prediction.interim_latency_ms,
                prediction.commit_latency_ms,
                prediction.load_seconds,
                prediction.preemption_resume_ms,
            ),
        )
    )
    return {
        **text,
        **safety,
        **timeline,
        **speakers,
        **performance,
        "provenance_coverage": prediction.provenance_coverage,
    }


def _aggregate_metrics(values: tuple[dict[str, object], ...]) -> dict[str, object]:
    if not values:
        raise ValueError("test split is empty for a ranking group")
    keys = set.intersection(*(set(item) for item in values))
    result: dict[str, object] = {}
    for key in sorted(keys):
        present = tuple(item[key] for item in values)
        numeric = tuple(
            float(item)
            for item in present
            if isinstance(item, (int, float)) and not isinstance(item, bool)
        )
        if len(numeric) == len(present):
            result[key] = sum(numeric) / len(numeric)
        elif all(isinstance(item, dict) for item in present):
            result[key] = _aggregate_metrics(
                tuple(item for item in present if isinstance(item, dict))
            )
        elif all(item == present[0] for item in present):
            result[key] = present[0]
    return result


def _records_hash(records: tuple[GoldRecord, ...]) -> str:
    payload = [asdict(item) for item in records]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _metric_number(metrics: dict[str, object], key: str) -> float:
    value = metrics.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"benchmark metric {key} is missing or non-numeric")
    return float(value)
