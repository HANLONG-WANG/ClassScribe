"""Deterministic anomaly checks that decide whether a window needs diarization fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from classscribe.config import ClassroomConfig
from classscribe.structure.models import StructureResult
from classscribe.timeline import SAMPLE_RATE, AudioSpan


class StructureAnomaly(StrEnum):
    WORKER_FAILURE = "worker_failure"
    SPEECH_WITHOUT_SEGMENTS = "speech_without_segments"
    LOW_SPEECH_COVERAGE = "low_speech_coverage"
    EXCESS_SPEAKERS = "speaker_count_exceeds_configured_maximum"
    DUPLICATE_SEGMENTS = "duplicate_segments_inside_window"
    MOSTLY_EMPTY_TEXT = "mostly_empty_structure_text"
    OVERLAP_NOT_ALLOWED = "overlap_detected_while_disabled"


@dataclass(frozen=True, slots=True)
class StructureHealthReport:
    needs_fallback: bool
    anomalies: tuple[StructureAnomaly, ...]
    metrics: dict[str, int | float | bool | str]

    @classmethod
    def worker_failure(cls, detail: str) -> StructureHealthReport:
        return cls(
            needs_fallback=True,
            anomalies=(StructureAnomaly.WORKER_FAILURE,),
            metrics={"worker_failure": True, "detail": detail},
        )


def inspect_structure_result(
    result: StructureResult,
    speech_spans: tuple[AudioSpan, ...],
    config: ClassroomConfig,
) -> StructureHealthReport:
    """Flag only structural failures; weak ASR wording is handled by later consensus."""

    window = result.window.span
    relevant_speech = tuple(
        _intersection(span, window) for span in speech_spans if span.overlaps(window)
    )
    relevant_speech = tuple(span for span in relevant_speech if span.duration_samples > 0)
    speech_samples = _union_duration(relevant_speech)
    structure_spans = tuple(item.span for item in result.segments)
    covered_samples = _covered_speech_samples(relevant_speech, structure_spans)
    coverage = covered_samples / speech_samples if speech_samples else 1.0
    speakers = {item.speaker_local for item in result.segments if item.speaker_local is not None}
    duplicate_count = _duplicate_count(result)
    empty_count = sum(not item.text.strip() for item in result.segments)
    overlap_samples = _union_duration(tuple(item.span for item in result.segments if item.overlap))
    anomalies: list[StructureAnomaly] = []
    if speech_samples >= 2 * SAMPLE_RATE and not result.segments:
        anomalies.append(StructureAnomaly.SPEECH_WITHOUT_SEGMENTS)
    if speech_samples >= 5 * SAMPLE_RATE and coverage < 0.35:
        anomalies.append(StructureAnomaly.LOW_SPEECH_COVERAGE)
    if len(speakers) > config.max_speakers:
        anomalies.append(StructureAnomaly.EXCESS_SPEAKERS)
    if result.segments and duplicate_count / len(result.segments) > 0.25:
        anomalies.append(StructureAnomaly.DUPLICATE_SEGMENTS)
    if result.segments and empty_count / len(result.segments) >= 0.8:
        anomalies.append(StructureAnomaly.MOSTLY_EMPTY_TEXT)
    if not config.allow_overlap and overlap_samples:
        anomalies.append(StructureAnomaly.OVERLAP_NOT_ALLOWED)
    metrics: dict[str, int | float | bool | str] = {
        "window_ordinal": result.window.ordinal,
        "speech_samples": speech_samples,
        "covered_speech_samples": covered_samples,
        "speech_coverage": round(coverage, 6),
        "segment_count": len(result.segments),
        "speaker_count": len(speakers),
        "duplicate_count": duplicate_count,
        "empty_text_count": empty_count,
        "overlap_samples": overlap_samples,
    }
    return StructureHealthReport(bool(anomalies), tuple(anomalies), metrics)


def _intersection(left: AudioSpan, right: AudioSpan) -> AudioSpan:
    return AudioSpan(
        max(left.start_sample, right.start_sample), min(left.end_sample, right.end_sample)
    )


def _union_duration(spans: tuple[AudioSpan, ...]) -> int:
    if not spans:
        return 0
    ordered = sorted(spans, key=lambda span: (span.start_sample, span.end_sample))
    total = 0
    start = ordered[0].start_sample
    end = ordered[0].end_sample
    for span in ordered[1:]:
        if span.start_sample <= end:
            end = max(end, span.end_sample)
        else:
            total += end - start
            start, end = span.start_sample, span.end_sample
    return total + end - start


def _covered_speech_samples(
    speech_spans: tuple[AudioSpan, ...], structure_spans: tuple[AudioSpan, ...]
) -> int:
    intersections = tuple(
        _intersection(speech, structure)
        for speech in speech_spans
        for structure in structure_spans
        if speech.overlaps(structure)
    )
    return _union_duration(tuple(span for span in intersections if span.duration_samples))


def _normalized_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)


def _duplicate_count(result: StructureResult) -> int:
    duplicates = 0
    for index, left in enumerate(result.segments):
        normalized = _normalized_text(left.text)
        if not normalized:
            continue
        if any(
            normalized == _normalized_text(right.text) and left.span.overlaps(right.span)
            for right in result.segments[index + 1 :]
        ):
            duplicates += 1
    return duplicates
