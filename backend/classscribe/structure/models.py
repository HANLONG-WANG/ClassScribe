"""Typed, absolute-sample contract for coarse structural transcription."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from classscribe_protocol import Priority, RPCRequest, RPCResponse

from classscribe.audio.segmentation import StructureWindow
from classscribe.config import ClassroomConfig
from classscribe.timeline import SAMPLE_RATE, AudioSpan

STRUCTURE_TEXT_ROLE: Final = "coarse_timeline_consensus_candidate_boundary_reference"
PARSER_VERSION: Final = "moss-structure-v1"


class StructureContractError(ValueError):
    """Raised when a structural worker violates the version-one response contract."""


@dataclass(frozen=True, slots=True)
class StructureSegment:
    """A coarse model segment; ``text`` is explicitly not an adopted final transcript."""

    window_ordinal: int
    span: AudioSpan
    speaker_local: str | None
    text: str
    acoustic_events: tuple[str, ...]
    source_model: str
    source_revision: str
    confidence_raw: float | None = None
    selection_score: float = 0.5
    overlap: bool = False
    exclusive: bool = True
    fallback: bool = False
    speaker_global: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    text_role: str = STRUCTURE_TEXT_ROLE

    def __post_init__(self) -> None:
        if self.window_ordinal < 0:
            raise StructureContractError("window ordinal must be non-negative")
        if self.span.duration_samples <= 0:
            raise StructureContractError("structure segment must have positive duration")
        if self.speaker_local is not None and not self.speaker_local.strip():
            raise StructureContractError("speaker_local must be non-empty when present")
        if not self.source_model or len(self.source_revision) != 40:
            raise StructureContractError("structure source requires model ID and commit revision")
        if self.confidence_raw is not None and not math.isfinite(self.confidence_raw):
            raise StructureContractError("raw confidence must be finite")
        if not math.isfinite(self.selection_score) or not 0 <= self.selection_score <= 1:
            raise StructureContractError("selection score must be within [0, 1]")
        if self.text_role != STRUCTURE_TEXT_ROLE:
            raise StructureContractError("MOSS text cannot be promoted to a final-text role")
        if self.overlap and self.exclusive:
            raise StructureContractError("a true overlap cannot be an exclusive speaker span")

    def assign_speaker(self, global_id: str | None) -> StructureSegment:
        if global_id is not None and not global_id:
            raise StructureContractError("global speaker ID must be non-empty")
        return replace(self, speaker_global=global_id)

    def with_provenance(self, **entries: Any) -> StructureSegment:
        return replace(self, provenance={**self.provenance, **entries})


@dataclass(frozen=True, slots=True)
class StructureResult:
    window: StructureWindow
    segments: tuple[StructureSegment, ...]
    metrics: Mapping[str, int | float | str | bool | None]
    warnings: tuple[str, ...]
    source_model: str
    source_revision: str


def speaker_policy_payload(config: ClassroomConfig) -> dict[str, object]:
    """Serialize the ordinary-class/group-discussion speaker prior without hiding bounds."""

    return {
        "context": config.speaker_context,
        "expected_speakers": config.expected_speakers,
        "prior_min": config.prior_min,
        "prior_typical": config.prior_typical,
        "max_speakers": config.max_speakers,
        "allow_overlap": config.allow_overlap,
    }


def build_moss_structure_request(
    *,
    request_id: str,
    job_id: str,
    audio_path: Path,
    window: StructureWindow,
    config: ClassroomConfig,
    language: str = "auto",
    hotwords: Sequence[str] = (),
    deadline_ms: int = 3_600_000,
) -> RPCRequest:
    """Build an auditable MOSS request using only absolute canonical sample positions."""

    normalized_hotwords = [item.strip() for item in hotwords if item.strip()]
    return RPCRequest(
        request_id=request_id,
        job_id=job_id,
        deadline_ms=deadline_ms,
        priority=Priority.CLASSROOM_PRIMARY,
        method="transcribe_batch",
        params={
            "audio_path": str(audio_path.resolve()),
            "start_sample": window.span.start_sample,
            "end_sample": window.span.end_sample,
            "sample_rate": SAMPLE_RATE,
            "language": language,
            "hotwords": normalized_hotwords,
            "task": "structure_transcription",
            "window_ordinal": window.ordinal,
            "speaker_policy": speaker_policy_payload(config),
            "text_role": STRUCTURE_TEXT_ROLE,
            "decode": {
                "do_sample": False,
                "temperature": 0.0,
                "attention": "sdpa",
            },
        },
    )


def parse_moss_structure_response(
    response: RPCResponse,
    window: StructureWindow,
) -> StructureResult:
    """Validate MOSS output and attach immutable, segment-level provenance."""

    if not response.ok:
        raise StructureContractError(
            f"MOSS request failed: {response.error_code}: {response.error_detail or ''}".rstrip()
        )
    segments: list[StructureSegment] = []
    for index, raw in enumerate(response.segments):
        start = _integer(raw, "start_sample")
        end = _integer(raw, "end_sample")
        if start < window.span.start_sample or end > window.span.end_sample:
            raise StructureContractError("MOSS segment escaped its requested structure window")
        local_value = raw.get("speaker_local", raw.get("speaker"))
        if not isinstance(local_value, str) or not local_value.strip():
            raise StructureContractError("MOSS segment requires speaker_local")
        text_value = raw.get("text", "")
        if not isinstance(text_value, str):
            raise StructureContractError("MOSS segment text must be a string")
        confidence = _optional_number(raw.get("confidence_raw"), "confidence_raw")
        selection_score = _selection_score(raw, text_value)
        events = _acoustic_events(raw.get("acoustic_events", ()))
        segments.append(
            StructureSegment(
                window_ordinal=window.ordinal,
                span=AudioSpan(start, end),
                speaker_local=local_value.strip(),
                text=text_value,
                acoustic_events=events,
                source_model=response.model_id,
                source_revision=response.model_revision,
                confidence_raw=confidence,
                selection_score=selection_score,
                overlap=bool(raw.get("overlap", False)),
                exclusive=not bool(raw.get("overlap", False)),
                provenance={
                    "parser_version": PARSER_VERSION,
                    "request_id": response.request_id,
                    "job_id": response.job_id,
                    "window_ordinal": window.ordinal,
                    "source_segment_index": index,
                    "source_model": response.model_id,
                    "source_revision": response.model_revision,
                    "absolute_samples": True,
                    "text_role": STRUCTURE_TEXT_ROLE,
                    "adopted_as_final": False,
                },
            )
        )
    if [item.span.start_sample for item in segments] != sorted(
        item.span.start_sample for item in segments
    ):
        raise StructureContractError("MOSS structure segments are not ordered")
    return StructureResult(
        window=window,
        segments=tuple(segments),
        metrics=dict(response.metrics),
        warnings=response.warnings,
        source_model=response.model_id,
        source_revision=response.model_revision,
    )


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StructureContractError(f"{key} must be an integer sample index")
    return value


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StructureContractError(f"{field_name} must be numeric or null")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise StructureContractError(f"{field_name} must be finite")
    return numeric


def _selection_score(raw: Mapping[str, Any], text_value: str) -> float:
    value = raw.get("structure_score", 0.55 if text_value.strip() else 0.35)
    numeric = _optional_number(value, "structure_score")
    assert numeric is not None
    if not 0 <= numeric <= 1:
        raise StructureContractError("structure_score must be within [0, 1]")
    return numeric


def _acoustic_events(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise StructureContractError("acoustic_events must be a list")
    events: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            events.append(item.strip())
        elif isinstance(item, Mapping) and isinstance(item.get("label"), str):
            label = str(item["label"]).strip()
            if label:
                events.append(label)
        else:
            raise StructureContractError("acoustic event entries require a non-empty label")
    return tuple(events)
