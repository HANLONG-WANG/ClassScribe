"""Safety gate for short-segment Qwen forced alignment."""

from __future__ import annotations

from dataclasses import dataclass

from classscribe.quality.models import QualityIssue
from classscribe.timeline import SAMPLE_RATE, AudioSpan

_UNSAFE_ISSUES = frozenset(
    {
        QualityIssue.EMPTY_ON_SPEECH,
        QualityIssue.REPEATED_NGRAM,
        QualityIssue.SHORTEST_LOOP,
        QualityIssue.OUTPUT_TOO_DENSE,
        QualityIssue.PREFIX_STAGNATION,
        QualityIssue.REPLACEMENT_CHARACTER,
        QualityIssue.DOUBLE_QUESTION,
        QualityIssue.ILLEGAL_CHARACTER,
        QualityIssue.SCRIPT_MISMATCH,
        QualityIssue.LENGTH_VOICE_MISMATCH,
        QualityIssue.LATE_SPEECH_UNCOVERED,
    }
)


@dataclass(frozen=True, slots=True)
class AlignmentGateInput:
    canonical_span: AudioSpan
    final_text: str
    language: str
    acoustic_language: str
    voiced_samples: int
    transcript_coverage: float
    quality_issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class AlignmentGateDecision:
    allowed: bool
    reasons: tuple[str, ...]
    safe_max_seconds: float
    quality_gate: dict[str, bool | float]
    rule_version: str = "forced-alignment-gate-v1"


def evaluate_alignment_gate(
    value: AlignmentGateInput, *, registry_safe_seconds: float
) -> AlignmentGateDecision:
    safe_max = min(30.0, registry_safe_seconds)
    duration = value.canonical_span.duration_samples / SAMPLE_RATE
    voiced_seconds = value.voiced_samples / SAMPLE_RATE
    issues = set(value.quality_issues)
    reasons: list[str] = []
    no_loop = not bool(
        issues
        & {
            QualityIssue.REPEATED_NGRAM,
            QualityIssue.SHORTEST_LOOP,
            QualityIssue.PREFIX_STAGNATION,
        }
    )
    no_missing = not bool(
        issues & {QualityIssue.EMPTY_ON_SPEECH, QualityIssue.LATE_SPEECH_UNCOVERED}
    )
    normal_chars = not bool(issues & _UNSAFE_ISSUES) and voiced_seconds > 0
    language_matches = value.language == value.acoustic_language and value.language in {
        "zh",
        "ja",
        "en",
    }
    if duration >= safe_max:
        reasons.append("segment_reaches_safe_duration_limit")
    if not 0.5 <= value.transcript_coverage <= 1.0:
        reasons.append("transcript_coverage_unreasonable")
    if not no_loop:
        reasons.append("decode_loop_present")
    if not no_missing:
        reasons.append("missing_text_present")
    if not normal_chars:
        reasons.append("abnormal_character_or_quality_signal")
    if not language_matches:
        reasons.append("language_mismatch")
    ratio = len("".join(value.final_text.split())) / voiced_seconds if voiced_seconds else 0.0
    if not 0.2 <= ratio <= 24:
        reasons.append("text_to_voiced_duration_ratio_abnormal")
    if not value.final_text.strip():
        reasons.append("empty_final_text")
    return AlignmentGateDecision(
        not reasons,
        tuple(reasons),
        safe_max,
        {
            "no_decode_loop": no_loop,
            "no_missing_text": no_missing,
            "normal_character_rate": normal_chars and 0.2 <= ratio <= 24,
            "language_matches": language_matches,
            "coverage_ratio": value.transcript_coverage,
            "voiced_seconds": voiced_seconds,
        },
    )
