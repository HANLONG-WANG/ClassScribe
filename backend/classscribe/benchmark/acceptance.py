"""Fail-closed Phase 12 release acceptance over real local evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from classscribe.benchmark.evidence import validate_test_evidence
from classscribe.benchmark.models import GoldCoverage, GoldRecord


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    status: str
    checks: dict[str, bool]
    details: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "checks": self.checks, "details": self.details}


def validate_phase12_acceptance(
    records: tuple[GoldRecord, ...],
    benchmark_report: dict[str, Any],
    desktop_report: dict[str, Any],
    test_evidence: dict[str, Any],
    *,
    manifest_sha256: str,
) -> AcceptanceResult:
    coverage = GoldCoverage.inspect(records)
    try:
        coverage.require(production=True)
        coverage_passed = True
    except ValueError:
        coverage_passed = False
    audio_ranges: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    speakers: dict[str, set[str]] = defaultdict(set)
    tags: set[str] = set()
    for item in records:
        audio_ranges[(item.scenario, item.audio)].append((item.start_sample, item.end_sample))
        speakers[item.audio].update(span.speaker for span in item.speaker_spans)
        if item.speaker:
            speakers[item.audio].add(item.speaker)
        tags.update(item.tags)
    classroom_durations = tuple(
        max(end for _start, end in spans) - min(start for start, _end in spans)
        for (scenario, _audio), spans in audio_ranges.items()
        if scenario == "classroom"
    )
    ibus_durations = tuple(
        max(end for _start, end in spans) - min(start for start, _end in spans)
        for (scenario, _audio), spans in audio_ranges.items()
        if scenario == "ibus"
    )
    speaker_counts = {len(values) for values in speakers.values()}
    rankings = benchmark_report.get("rankings", [])
    expected_groups = {
        (language, scenario)
        for language in ("zh", "ja", "en")
        for scenario in ("classroom", "ibus")
    }
    passed_groups = (
        {
            (item.get("language"), item.get("scenario"))
            for item in rankings
            if isinstance(item, dict) and item.get("passed") is True and item.get("models")
        }
        if isinstance(rankings, list)
        else set()
    )
    report_parameters = benchmark_report.get("parameters", {})
    real_execution = (
        isinstance(report_parameters, dict)
        and report_parameters.get("real_model_execution") is True
        and report_parameters.get("synthetic_gold") is False
    )
    report_items = benchmark_report.get("items", [])
    safety_passed = isinstance(report_items, list) and bool(report_items)
    if safety_passed:
        for item in report_items:
            metrics = item.get("metrics", {}) if isinstance(item, dict) else {}
            if not isinstance(metrics, dict) or any(
                _number(metrics.get(key, 1)) != expected
                for key, expected in (
                    ("structural_errors", 0),
                    ("loop_triggers_per_hour", 0),
                    ("silence_hallucinations_per_hour", 0),
                    ("provenance_coverage", 1),
                )
            ):
                safety_passed = False
                break
    tests, test_reasons = validate_test_evidence(test_evidence)
    checks = {
        "production_gold_coverage": coverage_passed,
        "real_90m_classroom": any(value >= 5_400 * 16_000 for value in classroom_durations),
        "real_5m_ibus": any(value >= 300 * 16_000 for value in ibus_durations),
        "speaker_counts_1_2_5plus": 1 in speaker_counts
        and 2 in speaker_counts
        and any(value >= 5 for value in speaker_counts),
        "language_noise_interruption_tags": {
            "code_switch",
            "silence",
            "background_music",
            "interrupted",
            "overlap",
        }.issubset(tags),
        "benchmark_completed": benchmark_report.get("status") == "completed",
        "manifest_hash_matches": benchmark_report.get("manifest_sha256") == manifest_sha256,
        "all_product_language_rankings": passed_groups == expected_groups,
        "real_model_execution": real_execution,
        "zero_structural_loop_hallucination_and_full_provenance": safety_passed,
        "desktop_matrix": desktop_report.get("status") == "passed",
        "all_test_evidence": all(tests.values()),
    }
    return AcceptanceResult(
        "passed" if all(checks.values()) else "incomplete",
        checks,
        {
            "coverage": {
                "classroom_seconds": coverage.classroom_seconds,
                "ibus_items": coverage.ibus_items,
                "ibus_long_items": coverage.ibus_long_items,
            },
            "maximum_classroom_seconds": max(classroom_durations, default=0) / 16_000,
            "maximum_ibus_seconds": max(ibus_durations, default=0) / 16_000,
            "speaker_counts": sorted(speaker_counts),
            "present_tags": sorted(tags),
            "tests": tests,
            "test_evidence_reasons": test_reasons,
        },
    )


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 1.0
