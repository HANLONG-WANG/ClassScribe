"""Pyannote Community-1 fallback with exclusive and true-overlap tracks."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

from classscribe_protocol import Priority, RPCRequest, RPCResponse

from classscribe.audio.segmentation import StructureWindow
from classscribe.config import ClassroomConfig
from classscribe.structure.models import (
    STRUCTURE_TEXT_ROLE,
    StructureContractError,
    StructureSegment,
)
from classscribe.structure.stitching import EmbeddingObservation
from classscribe.timeline import SAMPLE_RATE, AudioSpan

PYANNOTE_PARSER_VERSION: Final = "pyannote-community-1-v1"


@dataclass(frozen=True, slots=True)
class DiarizationSpan:
    span: AudioSpan
    speaker_local: str
    overlap: bool
    exclusive: bool
    confidence_raw: float | None
    source_model: str
    source_revision: str
    provenance: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TrueOverlap:
    span: AudioSpan
    speaker_local_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PyannoteResult:
    window: StructureWindow
    regular_spans: tuple[DiarizationSpan, ...]
    exclusive_spans: tuple[DiarizationSpan, ...]
    overlap_spans: tuple[DiarizationSpan, ...]
    true_overlaps: tuple[TrueOverlap, ...]
    embeddings: tuple[EmbeddingObservation, ...]
    source_model: str
    source_revision: str
    metrics: Mapping[str, int | float | str | bool | None]
    warnings: tuple[str, ...]

    @property
    def persistent_speaker_spans(self) -> tuple[DiarizationSpan, ...]:
        """Exclusive primary track plus explicit per-speaker true-overlap evidence."""

        exclusive_without_overlap: list[DiarizationSpan] = []
        exclusions = tuple(item.span for item in self.true_overlaps)
        for item in self.exclusive_spans:
            exclusive_without_overlap.extend(
                replace(
                    item,
                    span=span,
                    provenance={
                        **item.provenance,
                        "true_overlap_subtracted": bool(exclusions),
                    },
                )
                for span in _subtract_spans(item.span, exclusions)
            )
        return tuple(exclusive_without_overlap) + self.overlap_spans


def build_pyannote_request(
    *,
    request_id: str,
    job_id: str,
    audio_path: Path,
    window: StructureWindow,
    config: ClassroomConfig,
    deadline_ms: int = 1_800_000,
) -> RPCRequest:
    params: dict[str, object] = {
        "audio_path": str(audio_path.resolve()),
        "start_sample": window.span.start_sample,
        "end_sample": window.span.end_sample,
        "sample_rate": SAMPLE_RATE,
        "window_ordinal": window.ordinal,
        "min_speakers": config.prior_min,
        "max_speakers": config.max_speakers,
        "return_exclusive": True,
        "return_embeddings": True,
        "detect_overlap": config.allow_overlap,
    }
    if isinstance(config.expected_speakers, int):
        params["num_speakers"] = config.expected_speakers
    return RPCRequest(
        request_id=request_id,
        job_id=job_id,
        deadline_ms=deadline_ms,
        priority=Priority.CLASSROOM_PRIMARY,
        method="diarize",
        params=params,
    )


def parse_pyannote_response(response: RPCResponse, window: StructureWindow) -> PyannoteResult:
    if not response.ok:
        detail = response.error_detail or ""
        raise StructureContractError(
            f"pyannote request failed: {response.error_code}: {detail}".rstrip()
        )
    regular = tuple(
        _parse_span(raw, response, window, index=index, exclusive=False)
        for index, raw in enumerate(response.segments)
    )
    exclusive_raw = response.result.get("exclusive_segments")
    if not isinstance(exclusive_raw, list):
        raise StructureContractError("pyannote response requires exclusive_segments")
    exclusive = tuple(
        _parse_span(raw, response, window, index=index, exclusive=True)
        for index, raw in enumerate(_mapping_list(exclusive_raw, "exclusive_segments"))
    )
    overlaps = _find_true_overlaps(regular)
    overlap_spans = tuple(
        DiarizationSpan(
            span=overlap.span,
            speaker_local=speaker,
            overlap=True,
            exclusive=False,
            confidence_raw=None,
            source_model=response.model_id,
            source_revision=response.model_revision,
            provenance={
                "parser_version": PYANNOTE_PARSER_VERSION,
                "request_id": response.request_id,
                "job_id": response.job_id,
                "window_ordinal": window.ordinal,
                "track": "regular_true_overlap",
                "overlap_speakers": list(overlap.speaker_local_ids),
                "absolute_samples": True,
            },
        )
        for overlap in overlaps
        for speaker in overlap.speaker_local_ids
    )
    embeddings = _parse_embeddings(response, window)
    return PyannoteResult(
        window=window,
        regular_spans=regular,
        exclusive_spans=exclusive,
        overlap_spans=overlap_spans,
        true_overlaps=overlaps,
        embeddings=embeddings,
        source_model=response.model_id,
        source_revision=response.model_revision,
        metrics=dict(response.metrics),
        warnings=response.warnings,
    )


def apply_pyannote_fallback(
    coarse_segments: tuple[StructureSegment, ...],
    diarization: PyannoteResult,
) -> tuple[StructureSegment, ...]:
    """Retain coarse text but replace anomalous speaker attribution with pyannote evidence."""

    if not coarse_segments:
        segments = [
            StructureSegment(
                window_ordinal=diarization.window.ordinal,
                span=item.span,
                speaker_local=item.speaker_local,
                text="",
                acoustic_events=(),
                source_model=diarization.source_model,
                source_revision=diarization.source_revision,
                confidence_raw=item.confidence_raw,
                selection_score=0.4,
                fallback=True,
                provenance={
                    **item.provenance,
                    "fallback": "pyannote_community_1",
                    "text_role": STRUCTURE_TEXT_ROLE,
                    "adopted_as_final": False,
                },
            )
            for item in diarization.persistent_speaker_spans
            if not item.overlap
        ]
        segments.extend(
            StructureSegment(
                window_ordinal=diarization.window.ordinal,
                span=item.span,
                speaker_local=None,
                text="",
                acoustic_events=(),
                source_model=diarization.source_model,
                source_revision=diarization.source_revision,
                confidence_raw=None,
                selection_score=0.35,
                overlap=True,
                exclusive=False,
                fallback=True,
                provenance={
                    "parser_version": PYANNOTE_PARSER_VERSION,
                    "window_ordinal": diarization.window.ordinal,
                    "fallback": "pyannote_community_1",
                    "true_overlap_speakers": list(item.speaker_local_ids),
                    "absolute_samples": True,
                    "text_role": STRUCTURE_TEXT_ROLE,
                    "adopted_as_final": False,
                },
            )
            for item in diarization.true_overlaps
        )
        return tuple(sorted(segments, key=lambda item: (item.span.start_sample, item.overlap)))
    mapped: list[StructureSegment] = []
    for segment in coarse_segments:
        overlap_speakers = _overlap_speakers(segment.span, diarization.true_overlaps)
        speaker = _dominant_exclusive_speaker(segment.span, diarization.exclusive_spans)
        true_overlap = bool(overlap_speakers)
        mapped.append(
            replace(
                segment,
                speaker_local=None if true_overlap else speaker,
                source_model=diarization.source_model,
                source_revision=diarization.source_revision,
                confidence_raw=None,
                selection_score=min(segment.selection_score, 0.5),
                overlap=true_overlap,
                exclusive=not true_overlap,
                fallback=True,
                provenance={
                    **segment.provenance,
                    "fallback": "pyannote_community_1",
                    "fallback_source_model": diarization.source_model,
                    "fallback_source_revision": diarization.source_revision,
                    "exclusive_speaker": speaker,
                    "true_overlap_speakers": list(overlap_speakers),
                    "moss_text_retained_as_coarse_only": True,
                    "adopted_as_final": False,
                },
            )
        )
    return tuple(mapped)


def _parse_span(
    raw: Mapping[str, Any],
    response: RPCResponse,
    window: StructureWindow,
    *,
    index: int,
    exclusive: bool,
) -> DiarizationSpan:
    start = _integer(raw, "start_sample")
    end = _integer(raw, "end_sample")
    if start < window.span.start_sample or end > window.span.end_sample or end <= start:
        raise StructureContractError("pyannote span escaped its requested window")
    speaker_value = raw.get("speaker_local", raw.get("speaker"))
    if not isinstance(speaker_value, str) or not speaker_value.strip():
        raise StructureContractError("pyannote span requires speaker_local")
    confidence = _optional_number(raw.get("confidence_raw"), "confidence_raw")
    return DiarizationSpan(
        span=AudioSpan(start, end),
        speaker_local=speaker_value.strip(),
        overlap=False,
        exclusive=exclusive,
        confidence_raw=confidence,
        source_model=response.model_id,
        source_revision=response.model_revision,
        provenance={
            "parser_version": PYANNOTE_PARSER_VERSION,
            "request_id": response.request_id,
            "job_id": response.job_id,
            "window_ordinal": window.ordinal,
            "source_segment_index": index,
            "track": "exclusive" if exclusive else "regular",
            "absolute_samples": True,
        },
    )


def _parse_embeddings(
    response: RPCResponse, window: StructureWindow
) -> tuple[EmbeddingObservation, ...]:
    raw_embeddings = response.result.get("embeddings", [])
    if not isinstance(raw_embeddings, list):
        raise StructureContractError("pyannote embeddings must be a list")
    observations: list[EmbeddingObservation] = []
    for raw in _mapping_list(raw_embeddings, "embeddings"):
        speaker = raw.get("speaker_local", raw.get("speaker"))
        vector = raw.get("vector")
        if not isinstance(speaker, str) or not speaker or not isinstance(vector, list):
            raise StructureContractError("embedding requires speaker_local and vector")
        if not vector or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector
        ):
            raise StructureContractError("embedding vector must contain numbers")
        start = _integer(raw, "start_sample")
        end = _integer(raw, "end_sample")
        quality = _optional_number(raw.get("signal_quality", 1.0), "signal_quality")
        assert quality is not None
        if (
            start < window.span.start_sample
            or end > window.span.end_sample
            or not 0 <= quality <= 1
        ):
            raise StructureContractError("embedding lies outside its window or has invalid quality")
        observations.append(
            EmbeddingObservation(
                window_ordinal=window.ordinal,
                speaker_local=speaker,
                span=AudioSpan(start, end),
                vector=tuple(float(value) for value in vector),
                signal_quality=quality,
                overlap=bool(raw.get("overlap", False)),
                support_count=_positive_integer(raw.get("support_count", 1), "support_count"),
            )
        )
    return tuple(observations)


def _find_true_overlaps(regular: tuple[DiarizationSpan, ...]) -> tuple[TrueOverlap, ...]:
    boundaries = sorted(
        {value for item in regular for value in (item.span.start_sample, item.span.end_sample)}
    )
    overlaps: list[TrueOverlap] = []
    for start, end in pairwise(boundaries):
        speakers = tuple(
            sorted(
                {
                    item.speaker_local
                    for item in regular
                    if item.span.start_sample < end and start < item.span.end_sample
                }
            )
        )
        if len(speakers) < 2 or end <= start:
            continue
        if (
            overlaps
            and overlaps[-1].speaker_local_ids == speakers
            and overlaps[-1].span.end_sample == start
        ):
            previous = overlaps[-1]
            overlaps[-1] = TrueOverlap(AudioSpan(previous.span.start_sample, end), speakers)
        else:
            overlaps.append(TrueOverlap(AudioSpan(start, end), speakers))
    return tuple(overlaps)


def _overlap_speakers(span: AudioSpan, overlaps: tuple[TrueOverlap, ...]) -> tuple[str, ...]:
    speakers = {
        speaker
        for overlap in overlaps
        if overlap.span.overlaps(span)
        and _intersection_samples(overlap.span, span)
        >= min(span.duration_samples, SAMPLE_RATE // 4)
        for speaker in overlap.speaker_local_ids
    }
    return tuple(sorted(speakers))


def _dominant_exclusive_speaker(
    span: AudioSpan, exclusive: tuple[DiarizationSpan, ...]
) -> str | None:
    durations: dict[str, int] = {}
    for item in exclusive:
        duration = _intersection_samples(span, item.span)
        durations[item.speaker_local] = durations.get(item.speaker_local, 0) + duration
    return max(durations, key=lambda speaker: (durations[speaker], speaker)) if durations else None


def _mapping_list(value: list[Any], field_name: str) -> tuple[Mapping[str, Any], ...]:
    if any(not isinstance(item, Mapping) for item in value):
        raise StructureContractError(f"{field_name} entries must be objects")
    return tuple(item for item in value if isinstance(item, Mapping))


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StructureContractError(f"{key} must be an integer sample index")
    return value


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StructureContractError(f"{field_name} must be a positive integer")
    return int(value)


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StructureContractError(f"{field_name} must be numeric or null")
    number = float(value)
    if not math.isfinite(number):
        raise StructureContractError(f"{field_name} must be finite")
    return number


def _intersection_samples(left: AudioSpan, right: AudioSpan) -> int:
    return max(
        0, min(left.end_sample, right.end_sample) - max(left.start_sample, right.start_sample)
    )


def _subtract_spans(span: AudioSpan, exclusions: tuple[AudioSpan, ...]) -> tuple[AudioSpan, ...]:
    remaining: tuple[AudioSpan, ...] = (span,)
    for exclusion in exclusions:
        next_remaining: list[AudioSpan] = []
        for current in remaining:
            if not current.overlaps(exclusion):
                next_remaining.append(current)
                continue
            if current.start_sample < exclusion.start_sample:
                next_remaining.append(AudioSpan(current.start_sample, exclusion.start_sample))
            if exclusion.end_sample < current.end_sample:
                next_remaining.append(AudioSpan(exclusion.end_sample, current.end_sample))
        remaining = tuple(next_remaining)
    return remaining
