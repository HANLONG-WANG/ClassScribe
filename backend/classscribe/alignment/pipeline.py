"""Deterministic timing-source priority with explicit VAD coarse fallback."""

from __future__ import annotations

from collections.abc import Callable

from classscribe.alignment.gate import AlignmentGateDecision
from classscribe.alignment.models import CanonicalTiming, TimingEvidence, TimingSource
from classscribe.alignment.validation import validate_timing
from classscribe.timeline import AudioSpan

ForcedAligner = Callable[[], TimingEvidence]


def select_canonical_timing(
    *,
    final_text: str,
    canonical_span: AudioSpan,
    voiced_spans: tuple[AudioSpan, ...],
    native: TimingEvidence | None,
    moss_structure: TimingEvidence | None,
    alignment_gate: AlignmentGateDecision,
    run_forced_aligner: ForcedAligner | None,
    vad_coarse: TimingEvidence,
) -> CanonicalTiming:
    """Apply native -> MOSS -> gated Qwen -> VAD priority without forcing bad text."""

    fallback_reasons: list[str] = []
    for expected, evidence in (
        (TimingSource.NATIVE_WORD, native),
        (TimingSource.MOSS_STRUCTURE, moss_structure),
    ):
        selected = _acceptable(
            evidence, expected, final_text, canonical_span, voiced_spans, fallback_reasons
        )
        if selected is not None:
            return selected
    if alignment_gate.allowed and run_forced_aligner is not None:
        try:
            aligned = run_forced_aligner()
        except Exception as exc:
            fallback_reasons.append(f"forced_aligner_failed:{type(exc).__name__}")
        else:
            selected = _acceptable(
                aligned,
                TimingSource.QWEN_FORCED,
                final_text,
                canonical_span,
                voiced_spans,
                fallback_reasons,
            )
            if selected is not None:
                return selected
    else:
        fallback_reasons.extend(alignment_gate.reasons or ("forced_aligner_not_available",))
    fallback = validate_timing(vad_coarse, voiced_spans, minimum_speech_coverage=0.0)
    if (
        vad_coarse.source is not TimingSource.VAD_COARSE
        or vad_coarse.canonical_span != canonical_span
        or vad_coarse.final_text != final_text
        or not fallback.valid
    ):
        raise ValueError("VAD coarse fallback is invalid for the final text/canonical range")
    return CanonicalTiming(
        vad_coarse.source,
        canonical_span,
        final_text,
        vad_coarse.tokens,
        coarse_timing=True,
        speech_coverage=fallback.speech_coverage,
        fallback_reasons=tuple(fallback_reasons),
    )


def _acceptable(
    evidence: TimingEvidence | None,
    expected: TimingSource,
    final_text: str,
    canonical_span: AudioSpan,
    voiced_spans: tuple[AudioSpan, ...],
    reasons: list[str],
) -> CanonicalTiming | None:
    if evidence is None:
        reasons.append(f"{expected.value}:unavailable")
        return None
    if (
        evidence.source is not expected
        or evidence.canonical_span != canonical_span
        or evidence.final_text != final_text
        or not evidence.reliable
    ):
        reasons.append(f"{expected.value}:unreliable_or_wrong_identity")
        return None
    validation = validate_timing(evidence, voiced_spans)
    if not validation.valid:
        reasons.extend(f"{expected.value}:{reason}" for reason in validation.reasons)
        return None
    return CanonicalTiming(
        evidence.source,
        canonical_span,
        final_text,
        evidence.tokens,
        coarse_timing=False,
        speech_coverage=validation.speech_coverage,
        fallback_reasons=tuple(reasons),
    )
