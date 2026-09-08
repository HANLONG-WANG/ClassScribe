"""Unified, evidence-preserving contract for language-specific body ASR."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from classscribe_protocol import Priority, RPCRequest, RPCResponse

from classscribe.audio.segmentation import TranscriptChunk
from classscribe.models.registry import ModelEntry
from classscribe.structure.models import STRUCTURE_TEXT_ROLE, StructureSegment
from classscribe.timeline import SAMPLE_RATE, AudioSpan

ASR_REQUEST_VERSION: Final = "body-asr-v1"
LANGUAGE_CODES: Final = frozenset({"zh", "ja", "en"})


class ASRContractError(ValueError):
    """A registry entry, request, or response violated the body-ASR contract."""


def is_structure_model(entry: ModelEntry) -> bool:
    return "diarization" in entry.tasks and "timestamps" in entry.tasks


class CandidateRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary_review"
    TERTIARY = "tertiary_review"
    STRUCTURE = "moss_structure_candidate"
    EXPERIMENTAL = "experimental"
    EXPERT = "course_expert"


class HintCategory(StrEnum):
    COURSE_TERM = "course_term"
    PERSON = "person"
    PLACE = "place"
    ORGANIZATION = "organization"
    PLANT = "plant"
    DRUG = "drug"
    FORMULA_READING = "formula_reading"


@dataclass(frozen=True, slots=True)
class PronunciationHint:
    canonical: str
    reading: str
    category: HintCategory = HintCategory.COURSE_TERM
    language: str | None = None

    def __post_init__(self) -> None:
        if not self.canonical.strip() or not self.reading.strip():
            raise ASRContractError("pronunciation hints require canonical form and reading")
        if self.language is not None and self.language not in LANGUAGE_CODES:
            raise ASRContractError("pronunciation hint language must be zh, ja, or en")

    def as_dict(self) -> dict[str, str]:
        result = {
            "canonical": self.canonical.strip(),
            "reading": self.reading.strip(),
            "category": self.category.value,
        }
        if self.language is not None:
            result["language"] = self.language
        return result


@dataclass(frozen=True, slots=True)
class ASRTokenEvidence:
    text: str
    span: AudioSpan
    confidence_raw: float | None
    source_index: int

    def __post_init__(self) -> None:
        if not self.text:
            raise ASRContractError("ASR token text must not be empty")
        if self.confidence_raw is not None and not math.isfinite(self.confidence_raw):
            raise ASRContractError("ASR token raw confidence must be finite")
        if self.source_index < 0:
            raise ASRContractError("ASR token source index must be non-negative")


@dataclass(frozen=True, slots=True)
class ASRCandidateEvidence:
    model_id: str
    model_revision: str
    role: CandidateRole
    language_requested: str
    language_reported: str | None
    audio_span: AudioSpan
    core_span: AudioSpan
    raw_text: str
    normalized_text: str
    confidence_raw: float | None
    tokens: tuple[ASRTokenEvidence, ...]
    decode: Mapping[str, Any]
    metrics: Mapping[str, int | float | str | bool | None]
    warnings: tuple[str, ...]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.model_id or not re.fullmatch(r"[0-9a-f]{40}", self.model_revision):
            raise ASRContractError("candidate requires model ID and immutable revision")
        if self.language_requested not in LANGUAGE_CODES:
            raise ASRContractError("candidate requested language must be manual zh, ja, or en")
        if not _contains(self.audio_span, self.core_span):
            raise ASRContractError("candidate audio context must contain its unique core span")
        if any(not _contains(self.audio_span, token.span) for token in self.tokens):
            raise ASRContractError("candidate token escaped its requested audio context")
        if self.confidence_raw is not None and not math.isfinite(self.confidence_raw):
            raise ASRContractError("candidate raw confidence must be finite")
        if self.decode.get("batch_size") != 1 or self.decode.get("mixed_length_batch") is not False:
            raise ASRContractError("body ASR must use a single, unmixed audio item")
        if self.decode.get("do_sample") is not False or self.decode.get("temperature") != 0.0:
            raise ASRContractError("body ASR sampling must be deterministic")


def decode_policy(duration_samples: int, *, seed: int = 0) -> dict[str, int | float | bool]:
    """Bound generation by audio duration and forbid risky batching/sampling defaults."""

    if duration_samples <= 0:
        raise ASRContractError("ASR duration must be positive")
    duration_seconds = duration_samples / SAMPLE_RATE
    return {
        "temperature": 0.0,
        "do_sample": False,
        "seed": seed,
        "batch_size": 1,
        "mixed_length_batch": False,
        "max_new_tokens": max(64, min(2048, math.ceil(duration_seconds * 16) + 32)),
        "max_output_characters": max(128, math.ceil(duration_seconds * 40)),
    }


def build_asr_request(
    *,
    request_id: str,
    job_id: str,
    audio_path: Path,
    chunk: TranscriptChunk,
    entry: ModelEntry,
    language: str,
    role: CandidateRole,
    hints: Sequence[PronunciationHint] = (),
    rolling_context: Sequence[str] = (),
    seed: int = 0,
    explicitly_enabled_experimental: bool = False,
    deadline_ms: int = 600_000,
) -> RPCRequest:
    """Create one registry-validated request for one natural body segment."""

    _validate_route(entry, language, role, explicitly_enabled_experimental)
    applicable_hints = tuple(
        hint for hint in hints if hint.language is None or hint.language == language
    )
    context = tuple(item.strip() for item in rolling_context if item.strip())[-3:]
    decode = decode_policy(chunk.audio_span.duration_samples, seed=seed)
    return RPCRequest(
        request_id=request_id,
        job_id=job_id,
        deadline_ms=deadline_ms,
        priority=Priority.CLASSROOM_PRIMARY
        if role is CandidateRole.PRIMARY
        else Priority.CLASSROOM_REVIEW,
        method="transcribe_batch",
        params={
            "audio_path": str(audio_path.resolve()),
            "start_sample": chunk.audio_span.start_sample,
            "end_sample": chunk.audio_span.end_sample,
            "core_start_sample": chunk.core_span.start_sample,
            "core_end_sample": chunk.core_span.end_sample,
            "sample_rate": SAMPLE_RATE,
            "language": language,
            "manual_language": True,
            "candidate_role": role.value,
            "experimental_enabled": explicitly_enabled_experimental,
            "request_contract": ASR_REQUEST_VERSION,
            "hints": [item.as_dict() for item in applicable_hints],
            "rolling_context": list(context),
            "decode": decode,
            "batch_items": 1,
        },
    )


def parse_asr_response(
    response: RPCResponse,
    request: RPCRequest,
    entry: ModelEntry,
    chunk: TranscriptChunk,
    role: CandidateRole,
) -> ASRCandidateEvidence:
    if not response.ok:
        raise ASRContractError(
            f"ASR request failed: {response.error_code}: {response.error_detail or ''}".rstrip()
        )
    if response.model_id != entry.id or response.model_revision != entry.revision:
        raise ASRContractError("ASR response model identity differs from the registry route")
    if (
        request.params.get("start_sample") != chunk.audio_span.start_sample
        or request.params.get("end_sample") != chunk.audio_span.end_sample
    ):
        raise ASRContractError("ASR request no longer matches its natural transcript chunk")
    decode = request.params.get("decode")
    if not isinstance(decode, Mapping):
        raise ASRContractError("ASR request is missing its recorded decode policy")
    max_characters = _positive_integer(decode.get("max_output_characters"), "max_output_characters")
    if len(response.normalized_text) > max_characters:
        raise ASRContractError("ASR output exceeded its audio-duration character limit")
    generated_tokens = response.metrics.get("generated_tokens")
    if generated_tokens is not None and (
        isinstance(generated_tokens, bool)
        or not isinstance(generated_tokens, (int, float))
        or generated_tokens > _positive_integer(decode.get("max_new_tokens"), "max_new_tokens")
    ):
        raise ASRContractError("ASR output exceeded its audio-duration token limit")
    tokens = _response_tokens(response, chunk.audio_span)
    confidence_values = [
        float(item["confidence_raw"])
        for item in response.segments
        if isinstance(item.get("confidence_raw"), (int, float))
        and not isinstance(item.get("confidence_raw"), bool)
    ]
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
    language = str(request.params["language"])
    return ASRCandidateEvidence(
        model_id=response.model_id,
        model_revision=response.model_revision,
        role=role,
        language_requested=language,
        language_reported=response.language,
        audio_span=chunk.audio_span,
        core_span=chunk.core_span,
        raw_text=response.raw_text,
        normalized_text=response.normalized_text,
        confidence_raw=confidence,
        tokens=tokens,
        decode=dict(decode),
        metrics=dict(response.metrics),
        warnings=response.warnings,
        provenance={
            "request_contract": ASR_REQUEST_VERSION,
            "request_id": request.request_id,
            "job_id": request.job_id,
            "model_id": response.model_id,
            "model_revision": response.model_revision,
            "audio_start_sample": chunk.audio_span.start_sample,
            "audio_end_sample": chunk.audio_span.end_sample,
            "core_start_sample": chunk.core_span.start_sample,
            "core_end_sample": chunk.core_span.end_sample,
            "absolute_samples": True,
            "manual_language": True,
            "candidate_role": role.value,
            "raw_confidence_not_cross_model_probability": True,
            "hints_are_bias_only_no_insertion_authority": True,
        },
    )


def moss_structure_candidate(
    segments: Sequence[StructureSegment],
    chunk: TranscriptChunk,
    *,
    language: str,
) -> ASRCandidateEvidence | None:
    """Expose already-deduplicated MOSS text as a coarse candidate without adopting it."""

    relevant = tuple(
        item
        for item in sorted(segments, key=lambda value: value.span.start_sample)
        if item.text.strip() and item.span.overlaps(chunk.audio_span)
    )
    if not relevant:
        return None
    source_models = {
        str(item.provenance.get("source_model", item.source_model)) for item in relevant
    }
    source_revisions = {
        str(item.provenance.get("source_revision", item.source_revision)) for item in relevant
    }
    if len(source_models) != 1 or len(source_revisions) != 1:
        raise ASRContractError("one natural chunk cannot mix MOSS source identities")
    raw = " ".join(item.text.strip() for item in relevant)
    confidence_values = [
        item.confidence_raw for item in relevant if item.confidence_raw is not None
    ]
    decode = decode_policy(chunk.audio_span.duration_samples)
    return ASRCandidateEvidence(
        model_id=source_models.pop(),
        model_revision=source_revisions.pop(),
        role=CandidateRole.STRUCTURE,
        language_requested=language,
        language_reported=None,
        audio_span=chunk.audio_span,
        core_span=chunk.core_span,
        raw_text=raw,
        normalized_text=" ".join(raw.split()),
        confidence_raw=(
            sum(confidence_values) / len(confidence_values) if confidence_values else None
        ),
        tokens=(),
        decode=decode,
        metrics={"source_segment_count": len(relevant)},
        warnings=("coarse MOSS structure candidate; never unconditional final text",),
        provenance={
            "text_role": STRUCTURE_TEXT_ROLE,
            "adopted_as_final": False,
            "absolute_samples": True,
            "source_windows": sorted({item.window_ordinal for item in relevant}),
            "candidate_role": CandidateRole.STRUCTURE.value,
        },
    )


def _validate_route(
    entry: ModelEntry,
    language: str,
    role: CandidateRole,
    explicitly_enabled_experimental: bool,
) -> None:
    if language not in LANGUAGE_CODES:
        raise ASRContractError("manual body-ASR language must be zh, ja, or en")
    if "asr" not in entry.tasks or "batch" not in entry.modes:
        raise ASRContractError(f"registry model cannot run batch ASR: {entry.id}")
    if entry.worker == "firered" and language == "ja":
        raise ASRContractError("FireRedASR2 is forbidden for Japanese body transcription")
    if language not in entry.languages and "auto" not in entry.languages:
        raise ASRContractError(f"registry model does not support {language}: {entry.id}")
    if (entry.experimental or not entry.enabled) and not explicitly_enabled_experimental:
        raise ASRContractError("experimental or disabled candidate requires explicit enablement")
    if role in {CandidateRole.EXPERIMENTAL, CandidateRole.EXPERT} and not (
        explicitly_enabled_experimental
    ):
        raise ASRContractError(
            "experimental or course expert candidate requires explicit enablement"
        )


def _response_tokens(
    response: RPCResponse, requested_span: AudioSpan
) -> tuple[ASRTokenEvidence, ...]:
    tokens: list[ASRTokenEvidence] = []
    source_index = 0
    for segment in response.segments:
        segment_span = _mapping_span(segment, requested_span, "response segment")
        words = segment.get("words", ())
        if words is None:
            words = ()
        if not isinstance(words, (list, tuple)):
            raise ASRContractError("ASR words must be a list")
        for word in words:
            if not isinstance(word, Mapping):
                raise ASRContractError("ASR word entries must be objects")
            word_span = _mapping_span(word, segment_span, "response word")
            text = word.get("text", word.get("word"))
            if not isinstance(text, str) or not text:
                raise ASRContractError("ASR word requires non-empty text")
            confidence = _optional_finite(word.get("confidence_raw"), "word confidence")
            tokens.append(ASRTokenEvidence(text, word_span, confidence, source_index))
            source_index += 1
    if [item.span.start_sample for item in tokens] != sorted(
        item.span.start_sample for item in tokens
    ):
        raise ASRContractError("ASR word times are not monotonic")
    return tuple(tokens)


def _mapping_span(raw: Mapping[str, Any], container: AudioSpan, label: str) -> AudioSpan:
    start = raw.get("start_sample")
    end = raw.get("end_sample")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or end <= start
    ):
        raise ASRContractError(f"{label} requires ordered integer samples")
    span = AudioSpan(start, end)
    if not _contains(container, span):
        raise ASRContractError(f"{label} escaped its containing absolute sample range")
    return span


def _optional_finite(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ASRContractError(f"{label} must be numeric or null")
    result = float(value)
    if not math.isfinite(result):
        raise ASRContractError(f"{label} must be finite")
    return result


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ASRContractError(f"{label} must be a positive integer")
    return int(value)


def _contains(container: AudioSpan, candidate: AudioSpan) -> bool:
    return (
        container.start_sample <= candidate.start_sample
        and candidate.end_sample <= container.end_sample
    )
