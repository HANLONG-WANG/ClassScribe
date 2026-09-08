"""Typed evidence and decisions for stage-eight body-ASR quality gating."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Any

from classscribe.timeline import AudioSpan


class QualityIssue(StrEnum):
    EMPTY_ON_SPEECH = "empty_text_while_speech_present"
    SILENCE_HALLUCINATION = "long_text_during_silence"
    REPEATED_NGRAM = "repeated_3_to_10_token_ngram"
    SHORTEST_LOOP = "shortest_loop_period_detected"
    OUTPUT_TOO_DENSE = "characters_per_voiced_second_exceeded"
    PREFIX_STAGNATION = "decode_prefix_novelty_stalled"
    REPETITION_COMPRESSION = "abnormally_compressible_repetition"
    REPEATED_SENTENCE = "repeated_sentence"
    REPLACEMENT_CHARACTER = "unicode_replacement_character"
    DOUBLE_QUESTION = "consecutive_question_placeholders"
    ILLEGAL_CHARACTER = "illegal_control_character"
    SCRIPT_MISMATCH = "manual_language_script_mismatch"
    PUNCTUATION_DENSITY = "abnormal_punctuation_density"
    UNPAIRED_PUNCTUATION = "unpaired_parenthesis_or_quote"
    MISSING_SENTENCE_END = "missing_sentence_end_punctuation"
    NUMERIC_UNIT_ANOMALY = "numeric_or_unit_anomaly"
    LOW_TIMESTAMP_COVERAGE = "low_word_timestamp_coverage"
    NON_MONOTONIC_TIME = "non_monotonic_word_timestamps"
    TIME_OUT_OF_RANGE = "word_timestamp_outside_request"
    EXCESSIVE_TIME_OVERLAP = "excessive_adjacent_word_overlap"
    LATE_SPEECH_UNCOVERED = "speech_continues_after_text_timestamps_end"
    LENGTH_VOICE_MISMATCH = "text_length_and_voiced_duration_mismatch"
    SUSPECTED_TERMINOLOGY_ERROR = "suspected_course_terminology_error"
    STRUCTURE_DIVERGENCE = "primary_and_structure_text_diverge"
    LOW_SNR = "low_snr"
    FAR_FIELD = "far_field_audio"
    LOW_CALIBRATED_QUALITY = "low_calibrated_quality"


REPETITION_ISSUES = frozenset(
    {
        QualityIssue.REPEATED_NGRAM,
        QualityIssue.SHORTEST_LOOP,
        QualityIssue.PREFIX_STAGNATION,
        QualityIssue.REPETITION_COMPRESSION,
        QualityIssue.REPEATED_SENTENCE,
    }
)

HARD_REJECTION_ISSUES = frozenset(
    {
        QualityIssue.SILENCE_HALLUCINATION,
        QualityIssue.OUTPUT_TOO_DENSE,
        QualityIssue.REPLACEMENT_CHARACTER,
        QualityIssue.DOUBLE_QUESTION,
        QualityIssue.ILLEGAL_CHARACTER,
        QualityIssue.SCRIPT_MISMATCH,
        QualityIssue.NON_MONOTONIC_TIME,
        QualityIssue.TIME_OUT_OF_RANGE,
        QualityIssue.EXCESSIVE_TIME_OVERLAP,
    }
)


def blocking_issues(issues: tuple[QualityIssue, ...]) -> frozenset[QualityIssue]:
    blocked = HARD_REJECTION_ISSUES.intersection(issues)
    # A decode loop itself can inflate the output rate. Keep it as review evidence.
    if REPETITION_ISSUES.intersection(issues):
        blocked = blocked - {QualityIssue.OUTPUT_TOO_DENSE}
    return blocked


@dataclass(frozen=True, slots=True)
class QualityContext:
    requested_span: AudioSpan
    voiced_spans: tuple[AudioSpan, ...]
    vad_speech_ratio: float
    no_speech_probability: float | None = None
    snr_db: float | None = None
    clipping_ratio: float = 0.0
    volume_rms: float = 0.0
    far_field: bool = False
    token_logprobs: tuple[float, ...] = ()
    ctc_posteriors: tuple[float, ...] = ()
    rnnt_posteriors: tuple[float, ...] = ()
    prefix_snapshots: tuple[str, ...] = ()
    structure_text: str = ""
    suspected_term_error: bool = False
    calibrated_candidate_quality: float | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("vad_speech_ratio", self.vad_speech_ratio),
            ("clipping_ratio", self.clipping_ratio),
            ("volume_rms", self.volume_rms),
        ):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{label} must be finite and within [0, 1]")
        for optional_label, optional_value in (
            ("no_speech_probability", self.no_speech_probability),
            ("calibrated_candidate_quality", self.calibrated_candidate_quality),
        ):
            if optional_value is not None and (
                not math.isfinite(optional_value) or not 0 <= optional_value <= 1
            ):
                raise ValueError(f"{optional_label} must be finite and within [0, 1]")
        if self.snr_db is not None and not math.isfinite(self.snr_db):
            raise ValueError("snr_db must be finite or null")
        if any(not _contains(self.requested_span, span) for span in self.voiced_spans):
            raise ValueError("voiced spans must be inside the canonical request")
        for values in (self.token_logprobs, self.ctc_posteriors, self.rnnt_posteriors):
            if any(not math.isfinite(value) for value in values):
                raise ValueError("model signal arrays must contain finite values")

    @property
    def voiced_samples(self) -> int:
        return _union_duration(self.voiced_spans)


@dataclass(frozen=True, slots=True)
class RetryDirective:
    retry_spans: tuple[AudioSpan, ...]
    switch_model: bool
    reject_candidate: bool
    reason: str

    def __post_init__(self) -> None:
        if len(self.retry_spans) < 2:
            raise ValueError("loop retry must split the rejected interval into shorter spans")
        if any(left.end_sample != right.start_sample for left, right in pairwise(self.retry_spans)):
            raise ValueError("loop retry spans must preserve contiguous audio coverage")


@dataclass(frozen=True, slots=True)
class QualityReport:
    candidate_id: str
    model_id: str
    valid_for_consensus: bool
    quality_gate_score: float
    score_is_calibrated_probability: bool
    issues: tuple[QualityIssue, ...]
    acoustic_model_features: dict[str, int | float | bool | str | None]
    text_features: dict[str, int | float | bool | str | None]
    timing_features: dict[str, int | float | bool | str | None]
    multi_model_features: dict[str, int | float | bool | str | None]
    retry: RetryDirective | None
    rule_version: str = "quality-gate-v2"

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "model_id": self.model_id,
            "valid_for_consensus": self.valid_for_consensus,
            "quality_gate_score": self.quality_gate_score,
            "score_is_calibrated_probability": self.score_is_calibrated_probability,
            "issues": [issue.value for issue in self.issues],
            "acoustic_model_features": dict(self.acoustic_model_features),
            "text_features": dict(self.text_features),
            "timing_features": dict(self.timing_features),
            "multi_model_features": dict(self.multi_model_features),
            "retry": asdict(self.retry) if self.retry is not None else None,
            "rule_version": self.rule_version,
        }


def _contains(container: AudioSpan, candidate: AudioSpan) -> bool:
    return (
        container.start_sample <= candidate.start_sample
        and candidate.end_sample <= container.end_sample
    )


def _union_duration(spans: tuple[AudioSpan, ...]) -> int:
    if not spans:
        return 0
    ordered = sorted(spans, key=lambda span: (span.start_sample, span.end_sample))
    start, end = ordered[0].start_sample, ordered[0].end_sample
    total = 0
    for span in ordered[1:]:
        if span.start_sample <= end:
            end = max(end, span.end_sample)
        else:
            total += end - start
            start, end = span.start_sample, span.end_sample
    return total + end - start
