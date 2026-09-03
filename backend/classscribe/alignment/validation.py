"""Validation of final token timing against canonical audio and VAD speech."""

from __future__ import annotations

from classscribe.alignment.models import TimingEvidence, TimingValidation
from classscribe.punctuation.guard import strip_punctuation_and_spacing
from classscribe.timeline import AudioSpan


def validate_timing(
    evidence: TimingEvidence,
    voiced_spans: tuple[AudioSpan, ...],
    *,
    minimum_speech_coverage: float = 0.6,
    maximum_alignment_cost: float = 0.45,
    maximum_vad_gap_error_ms: float = 500.0,
) -> TimingValidation:
    reasons: list[str] = []
    previous_end = evidence.canonical_span.start_sample
    for token in evidence.tokens:
        if (
            token.span.start_sample < previous_end
            or token.span.start_sample < evidence.canonical_span.start_sample
            or token.span.end_sample > evidence.canonical_span.end_sample
            or token.span.duration_samples <= 0
        ):
            reasons.append("non_monotonic_or_out_of_range_token")
            break
        previous_end = token.span.end_sample
    token_text = "".join(token.text for token in evidence.tokens)
    if not evidence.text_unchanged or strip_punctuation_and_spacing(
        token_text
    ) != strip_punctuation_and_spacing(evidence.final_text):
        reasons.append("aligned_tokens_changed_or_omitted_final_text")
    coverage = _speech_coverage(tuple(token.span for token in evidence.tokens), voiced_spans)
    if voiced_spans and coverage < minimum_speech_coverage:
        reasons.append("insufficient_vad_speech_coverage")
    if evidence.alignment_cost is not None and evidence.alignment_cost > maximum_alignment_cost:
        reasons.append("alignment_cost_too_high")
    if (
        evidence.vad_gap_error_ms is not None
        and evidence.vad_gap_error_ms > maximum_vad_gap_error_ms
    ):
        reasons.append("sentence_gap_disagrees_with_vad")
    if not evidence.tokens:
        reasons.append("no_timed_tokens")
    return TimingValidation(not reasons, coverage, tuple(reasons))


def _speech_coverage(tokens: tuple[AudioSpan, ...], voiced: tuple[AudioSpan, ...]) -> float:
    voiced_total = sum(span.duration_samples for span in _merge(voiced))
    if voiced_total <= 0:
        return 1.0 if tokens else 0.0
    covered = 0
    for speech in _merge(voiced):
        intersections = tuple(
            AudioSpan(
                max(speech.start_sample, token.start_sample),
                min(speech.end_sample, token.end_sample),
            )
            for token in tokens
            if token.start_sample < speech.end_sample and speech.start_sample < token.end_sample
        )
        covered += sum(span.duration_samples for span in _merge(intersections))
    return min(1.0, covered / voiced_total)


def _merge(spans: tuple[AudioSpan, ...]) -> tuple[AudioSpan, ...]:
    if not spans:
        return ()
    ordered = sorted(spans, key=lambda value: (value.start_sample, value.end_sample))
    merged: list[AudioSpan] = [ordered[0]]
    for span in ordered[1:]:
        previous = merged[-1]
        if span.start_sample <= previous.end_sample:
            merged[-1] = AudioSpan(previous.start_sample, max(previous.end_sample, span.end_sample))
        else:
            merged.append(span)
    return tuple(merged)
