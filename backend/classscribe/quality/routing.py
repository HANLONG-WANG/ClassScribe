"""Explainable second- and third-model review triggers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from classscribe.quality.language import CandidateComparison
from classscribe.quality.models import QualityIssue, QualityReport, RetryDirective


class SecondaryTrigger(StrEnum):
    LOW_UNIFIED_QUALITY = "low_unified_quality"
    REPETITION_OR_HALLUCINATION = "repetition_or_hallucination"
    SCRIPT_ANOMALY = "script_anomaly"
    INSUFFICIENT_TIME_COVERAGE = "insufficient_time_coverage"
    SUSPECTED_TERMINOLOGY_ERROR = "suspected_terminology_error"
    STRUCTURE_TEXT_DIVERGENCE = "structure_text_divergence"
    LOW_SNR_OR_FAR_FIELD = "low_snr_or_far_field"


class TertiaryTrigger(StrEnum):
    HIGH_VALUE_TOKEN_CONFLICT = "high_value_token_conflict"
    BOTH_CANDIDATES_LOW_QUALITY = "both_candidates_low_quality"
    HOMOPHONE_SPELLING_CONFLICT = "homophone_spelling_conflict"
    CONTENT_PRESENCE_CONFLICT = "content_or_silence_conflict"


@dataclass(frozen=True, slots=True)
class SecondaryReviewDecision:
    run_secondary: bool
    triggers: tuple[SecondaryTrigger, ...]
    retry: RetryDirective | None
    rule_version: str = "secondary-review-router-v1"


@dataclass(frozen=True, slots=True)
class TertiaryReviewDecision:
    run_tertiary: bool
    triggers: tuple[TertiaryTrigger, ...]
    high_value_conflicts: tuple[str, ...]
    rule_version: str = "tertiary-review-router-v1"


class ReviewRouter:
    def __init__(
        self,
        *,
        second_model_threshold: float = 0.82,
        third_model_threshold: float = 0.62,
    ) -> None:
        if not 0 < third_model_threshold <= second_model_threshold < 1:
            raise ValueError("review thresholds must satisfy 0 < third <= second < 1")
        self.second_model_threshold = second_model_threshold
        self.third_model_threshold = third_model_threshold

    def secondary(self, report: QualityReport) -> SecondaryReviewDecision:
        issues = set(report.issues)
        triggers: list[SecondaryTrigger] = []
        if report.quality_gate_score < self.second_model_threshold:
            triggers.append(SecondaryTrigger.LOW_UNIFIED_QUALITY)
        if issues & {
            QualityIssue.SILENCE_HALLUCINATION,
            QualityIssue.REPEATED_NGRAM,
            QualityIssue.SHORTEST_LOOP,
            QualityIssue.PREFIX_STAGNATION,
            QualityIssue.REPETITION_COMPRESSION,
            QualityIssue.REPEATED_SENTENCE,
        }:
            triggers.append(SecondaryTrigger.REPETITION_OR_HALLUCINATION)
        if QualityIssue.SCRIPT_MISMATCH in issues:
            triggers.append(SecondaryTrigger.SCRIPT_ANOMALY)
        if issues & {
            QualityIssue.LOW_TIMESTAMP_COVERAGE,
            QualityIssue.LATE_SPEECH_UNCOVERED,
            QualityIssue.NON_MONOTONIC_TIME,
            QualityIssue.TIME_OUT_OF_RANGE,
            QualityIssue.EXCESSIVE_TIME_OVERLAP,
        }:
            triggers.append(SecondaryTrigger.INSUFFICIENT_TIME_COVERAGE)
        if QualityIssue.SUSPECTED_TERMINOLOGY_ERROR in issues:
            triggers.append(SecondaryTrigger.SUSPECTED_TERMINOLOGY_ERROR)
        if QualityIssue.STRUCTURE_DIVERGENCE in issues:
            triggers.append(SecondaryTrigger.STRUCTURE_TEXT_DIVERGENCE)
        if issues & {QualityIssue.LOW_SNR, QualityIssue.FAR_FIELD}:
            triggers.append(SecondaryTrigger.LOW_SNR_OR_FAR_FIELD)
        return SecondaryReviewDecision(bool(triggers), tuple(triggers), report.retry)

    def tertiary(
        self,
        primary: QualityReport,
        secondary: QualityReport,
        comparison: CandidateComparison,
    ) -> TertiaryReviewDecision:
        triggers: list[TertiaryTrigger] = []
        if comparison.high_value_conflicts or comparison.proper_noun_position_conflicts:
            triggers.append(TertiaryTrigger.HIGH_VALUE_TOKEN_CONFLICT)
        if (
            primary.quality_gate_score < self.third_model_threshold
            and secondary.quality_gate_score < self.third_model_threshold
        ):
            triggers.append(TertiaryTrigger.BOTH_CANDIDATES_LOW_QUALITY)
        if comparison.homophone_spelling_conflict:
            triggers.append(TertiaryTrigger.HOMOPHONE_SPELLING_CONFLICT)
        if comparison.content_presence_conflict:
            triggers.append(TertiaryTrigger.CONTENT_PRESENCE_CONFLICT)
        return TertiaryReviewDecision(
            bool(triggers),
            tuple(triggers),
            (
                *comparison.high_value_conflicts,
                *(f"position:{item}" for item in comparison.proper_noun_position_conflicts),
            ),
        )
