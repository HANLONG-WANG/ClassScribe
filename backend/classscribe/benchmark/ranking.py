"""Constraint-first composite ranking, kept separate by language and product scenario."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    body_weight: float = 1.0
    terminology_weight: float = 0.5
    omission_weight: float = 2.0
    hallucination_weight: float = 3.0
    timeline_weight: float = 1.0
    punctuation_weight: float = 0.25
    performance_weight: float = 0.1
    vram_weight: float = 0.00001
    max_classroom_rtf: float = 1.0
    max_ibus_interim_p50_ms: float = 600.0
    max_ibus_commit_p50_ms: float = 1200.0
    max_ibus_commit_p95_ms: float = 2500.0


def rank_candidates(
    candidates: tuple[dict[str, Any], ...],
    *,
    language: str,
    scenario: str,
    policy: RankingPolicy | None = None,
) -> tuple[dict[str, Any], ...]:
    if language not in {"zh", "ja", "en"} or scenario not in {"classroom", "ibus"}:
        raise ValueError("ranking requires a concrete language and product scenario")
    active_policy = policy or RankingPolicy()
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("language") != language or candidate.get("scenario") != scenario:
            raise ValueError("ranking candidates cannot mix language or scenario")
        required = {"model_id", "model_revision", "metrics", "calibration"}
        if missing := required - candidate.keys():
            raise ValueError(f"ranking candidate is missing fields: {sorted(missing)}")
        metrics = candidate["metrics"]
        calibration = candidate["calibration"]
        if not isinstance(metrics, dict) or not isinstance(calibration, dict):
            raise ValueError("ranking metrics/calibration must be objects")
        disqualified = _violations(metrics, scenario, active_policy)
        if (
            calibration.get("model_revision") != candidate["model_revision"]
            or calibration.get("method") not in {"isotonic", "platt"}
            or not calibration.get("manifest_sha256")
        ):
            disqualified.append("stale_or_missing_calibration")
        body_key = "normalized_wer" if language == "en" else "normalized_cer"
        term = _nested(metrics, "term", "f1", default=0.0)
        punctuation = _nested(metrics, "punctuation", "per", default=1.0)
        score = (
            active_policy.body_weight * _number(metrics.get(body_key, 1.0))
            + active_policy.terminology_weight * (1 - term)
            + active_policy.omission_weight * _number(metrics.get("omission_rate", 0.0))
            + active_policy.hallucination_weight
            * (
                _number(metrics.get("silence_hallucinations_per_hour", 0.0))
                + _number(metrics.get("loop_triggers_per_hour", 0.0))
            )
            + active_policy.timeline_weight
            * (
                _number(metrics.get("word_boundary_mae_ms", 0.0)) / 1000
                + _number(metrics.get("der", 0.0))
                + _number(metrics.get("jer", 0.0))
            )
            + active_policy.punctuation_weight * punctuation
            + active_policy.performance_weight * _number(metrics.get("rtf", 0.0))
            + active_policy.vram_weight * _number(metrics.get("peak_vram_mb", 0.0))
        )
        scored.append(
            {
                "model_id": str(candidate["model_id"]),
                "model_revision": str(candidate["model_revision"]),
                "score": score,
                "eligible": not disqualified,
                "violations": disqualified,
                "calibration": calibration,
                "metrics": metrics,
            }
        )
    return tuple(
        sorted(
            scored,
            key=lambda item: (
                not bool(item["eligible"]),
                float(item["score"]),
                str(item["model_id"]),
            ),
        )
    )


def _violations(metrics: dict[str, Any], scenario: str, policy: RankingPolicy) -> list[str]:
    result: list[str] = []
    if _number(metrics.get("structural_errors", 0)):
        result.append("timeline_structural_errors")
    if _number(metrics.get("loop_triggers_per_hour", 0)):
        result.append("loop_output")
    if _number(metrics.get("silence_hallucinations_per_hour", 0)):
        result.append("silence_hallucination")
    if _number(metrics.get("provenance_coverage", 0)) != 1.0:
        result.append("incomplete_provenance")
    if _number(metrics.get("runtime_observation_coverage", 0)) != 1.0:
        result.append("incomplete_runtime_observations")
    if scenario == "classroom" and _number(metrics.get("rtf", 0)) >= policy.max_classroom_rtf:
        result.append("classroom_rtf")
    if scenario == "ibus":
        for key in ("interim_observation_coverage", "commit_observation_coverage"):
            if _number(metrics.get(key, 0)) != 1.0:
                result.append(key)
        limits = (
            ("interim_latency_p50_ms", policy.max_ibus_interim_p50_ms),
            ("commit_latency_p50_ms", policy.max_ibus_commit_p50_ms),
            ("commit_latency_p95_ms", policy.max_ibus_commit_p95_ms),
        )
        for key, limit in limits:
            value = metrics.get(key)
            if value is None or _number(value) >= limit:
                result.append(key)
    return result


def _nested(value: dict[str, Any], outer: str, inner: str, *, default: float) -> float:
    nested = value.get(outer)
    if not isinstance(nested, dict):
        return default
    return _number(nested.get(inner, default))


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("ranking metric must be numeric")
    return float(value)


def assess_automatic_quality(
    automatic: dict[str, Any],
    best_single_model: dict[str, Any],
    *,
    language: str,
    maximum_body_degradation: float = 0.03,
) -> dict[str, object]:
    """Gate an automatic pipeline against the best local single-model test result."""

    body_key = "normalized_wer" if language == "en" else "normalized_cer"
    checks = {
        "body_not_significantly_worse": _number(automatic.get(body_key, 1.0))
        <= _number(best_single_model.get(body_key, 1.0)) + maximum_body_degradation,
        "terminology_not_worse": _nested(automatic, "term", "f1", default=0.0)
        >= _nested(best_single_model, "term", "f1", default=0.0),
        "omission_not_worse": _number(automatic.get("omission_rate", 1.0))
        <= _number(best_single_model.get("omission_rate", 1.0)),
        "hallucination_not_worse": _number(automatic.get("silence_hallucinations_per_hour", 1.0))
        <= _number(best_single_model.get("silence_hallucinations_per_hour", 1.0)),
        "no_loops": _number(automatic.get("loop_triggers_per_hour", 1.0)) == 0,
        "full_provenance": _number(automatic.get("provenance_coverage", 0.0)) == 1.0,
    }
    return {"passed": all(checks.values()), "checks": checks}
