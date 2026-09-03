"""Strict version-one worker RPC messages and payload invariants."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

from classscribe_protocol.envelope import ProtocolError
from classscribe_protocol.version import PROTOCOL_VERSION

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
STANDARD_METHODS = (
    "health",
    "capabilities",
    "load",
    "unload",
    "transcribe_batch",
    "transcribe_pcm",
    "stream_open",
    "stream_push",
    "stream_flush",
    "stream_close",
    "align",
    "punctuate",
    "vad",
    "lid",
    "lid_pcm",
    "diarize",
    "cancel",
)
BODY_ASR_CONTRACT = "body-asr-v1"
PUNCTUATION_CONTRACT = "strict-punctuation-v1"
ALIGNMENT_CONTRACT = "final-align-v1"
BODY_ASR_LANGUAGES = frozenset({"zh", "ja", "en"})
BODY_ASR_ROLES = frozenset(
    {
        "primary",
        "secondary_review",
        "tertiary_review",
        "moss_structure_candidate",
        "experimental",
        "course_expert",
    }
)
BODY_ASR_HINT_CATEGORIES = frozenset(
    {
        "course_term",
        "person",
        "place",
        "organization",
        "plant",
        "drug",
        "formula_reading",
    }
)


class Priority(IntEnum):
    DICTATION = 0
    INTERACTIVE = 10
    CLASSROOM_PRIMARY = 20
    CLASSROOM_REVIEW = 30
    BACKGROUND = 40


class RPCErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    INCOMPATIBLE_PROTOCOL = "incompatible_protocol"
    METHOD_NOT_SUPPORTED = "method_not_supported"
    MODEL_NOT_LOADED = "model_not_loaded"
    MODEL_LOAD_FAILED = "model_load_failed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"
    INTERNAL = "internal_error"
    TRANSPORT = "transport_error"


@dataclass(frozen=True, slots=True)
class RPCRequest:
    request_id: str
    job_id: str
    deadline_ms: int
    priority: int
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version: {self.protocol_version}")
        if not self.request_id or not self.job_id:
            raise ProtocolError("request_id and job_id must not be empty")
        if self.deadline_ms <= 0 or self.deadline_ms > 86_400_000:
            raise ProtocolError("deadline_ms must be in 1..86400000")
        if self.priority not in {item.value for item in Priority}:
            raise ProtocolError(f"unsupported priority: {self.priority}")
        if self.method not in STANDARD_METHODS:
            raise ProtocolError(f"unsupported RPC method: {self.method}")
        _reject_quality_probability(self.params)
        validate_method_params(self.method, self.params)


@dataclass(frozen=True, slots=True)
class RPCResponse:
    request_id: str
    job_id: str
    ok: bool
    model_id: str
    model_revision: str
    raw_text: str = ""
    normalized_text: str = ""
    language: str | None = None
    segments: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, int | float | str | bool | None] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version: {self.protocol_version}")
        if not self.request_id or not self.job_id:
            raise ProtocolError("response IDs must not be empty")
        if not self.model_id or not COMMIT_RE.fullmatch(self.model_revision):
            raise ProtocolError("response must identify a model and full immutable revision")
        if self.ok and (self.error_code is not None or self.error_detail is not None):
            raise ProtocolError("successful response may not contain an error")
        if not self.ok and not self.error_code:
            raise ProtocolError("failed response must contain error_code")
        _reject_quality_probability(self.result)
        _reject_quality_probability(self.metrics)
        for segment in self.segments:
            _validate_segment(segment)
        _validate_result_extensions(self.result)


def validate_method_params(method: str, params: dict[str, Any]) -> None:
    """Validate invariants shared by every implementation of an RPC method."""

    if method in {"transcribe_batch", "align", "vad", "lid", "diarize"}:
        required = {"audio_path", "start_sample", "end_sample", "sample_rate"}
        missing = required - params.keys()
        if missing:
            raise ProtocolError(f"{method} missing fields: {sorted(missing)}")
        path = params["audio_path"]
        start = params["start_sample"]
        end = params["end_sample"]
        sample_rate = params["sample_rate"]
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ProtocolError("audio_path must be an absolute path")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise ProtocolError("sample range must be ordered non-negative integers")
        if sample_rate != 16_000:
            raise ProtocolError("worker audio sample_rate must be 16000")
        if method == "transcribe_batch" and params.get("request_contract") == BODY_ASR_CONTRACT:
            _validate_body_asr_params(params, start=start, end=end)
        if method == "align" and params.get("alignment_contract") == ALIGNMENT_CONTRACT:
            _validate_alignment_params(params, start=start, end=end)
    elif method == "transcribe_pcm":
        pcm = params.get("pcm_s16le")
        start = params.get("absolute_start_sample")
        core_start = params.get("core_start_sample")
        end = params.get("end_sample")
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise ProtocolError("transcribe_pcm requires non-empty whole PCM S16LE samples")
        if len(pcm) > 16_000 * 2 * 30:
            raise ProtocolError("transcribe_pcm is limited to 30 seconds")
        if any(
            isinstance(item, bool) or not isinstance(item, int) for item in (start, core_start, end)
        ):
            raise ProtocolError("transcribe_pcm sample indices must be integers")
        assert isinstance(start, int) and isinstance(core_start, int) and isinstance(end, int)
        if start < 0 or not start <= core_start < end or end - start != len(pcm) // 2:
            raise ProtocolError("transcribe_pcm sample range does not match its PCM payload")
        if params.get("sample_rate") != 16_000 or params.get("channels", 1) != 1:
            raise ProtocolError("transcribe_pcm audio must be 16 kHz mono")
        if params.get("language") not in BODY_ASR_LANGUAGES:
            raise ProtocolError("transcribe_pcm requires a concrete zh, ja, or en language")
        context = params.get("rolling_context", [])
        if (
            not isinstance(context, list)
            or len(context) > 3
            or any(not isinstance(item, str) or not item.strip() for item in context)
            or sum(len(item) for item in context) > 2400
        ):
            raise ProtocolError("transcribe_pcm rolling context is invalid")
    elif method == "punctuate":
        if params.get("punctuation_contract") != PUNCTUATION_CONTRACT:
            raise ProtocolError("punctuate requires strict-punctuation-v1")
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProtocolError("punctuate text must not be empty")
        if params.get("manual_language") is not True or params.get("language") not in {"zh", "en"}:
            raise ProtocolError("punctuate requires a manual zh or en language")
    elif method == "load":
        required = {"model_id", "model_revision", "model_path"}
        missing = required - params.keys()
        if missing:
            raise ProtocolError(f"load missing fields: {sorted(missing)}")
        if not isinstance(params["model_id"], str) or not params["model_id"]:
            raise ProtocolError("model_id must not be empty")
        if not isinstance(params["model_revision"], str) or not COMMIT_RE.fullmatch(
            params["model_revision"]
        ):
            raise ProtocolError("model_revision must be a full commit SHA")
        if (
            not isinstance(params["model_path"], str)
            or not Path(params["model_path"]).is_absolute()
        ):
            raise ProtocolError("model_path must be absolute")
    elif method == "stream_open":
        if params.get("sample_rate") != 16_000 or params.get("channels", 1) != 1:
            raise ProtocolError("stream audio must be 16 kHz mono")
        if not isinstance(params.get("stream_id"), str) or not params["stream_id"]:
            raise ProtocolError("stream_id must not be empty")
    elif method == "stream_push":
        pcm = params.get("pcm_s16le")
        if not isinstance(pcm, bytes) or len(pcm) == 0 or len(pcm) % 2:
            raise ProtocolError("pcm_s16le must be non-empty, whole int16 samples")
        if len(pcm) > 32_000:
            raise ProtocolError("a real-time frame may contain at most one second of PCM")
        if not isinstance(params.get("stream_id"), str) or not params["stream_id"]:
            raise ProtocolError("stream_id must not be empty")
    elif method == "lid_pcm":
        pcm = params.get("pcm_s16le")
        start = params.get("absolute_start_sample", 0)
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise ProtocolError("lid_pcm requires non-empty whole PCM S16LE samples")
        if len(pcm) > 16_000 * 2 * 30:
            raise ProtocolError("lid_pcm is limited to 30 seconds")
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ProtocolError("lid_pcm absolute_start_sample must be non-negative")
        if params.get("sample_rate") != 16_000:
            raise ProtocolError("lid_pcm sample_rate must be 16000")
    elif method in {"stream_flush", "stream_close"}:
        if not isinstance(params.get("stream_id"), str) or not params["stream_id"]:
            raise ProtocolError("stream_id must not be empty")
    elif method == "cancel":
        target = params.get("target_request_id")
        if not isinstance(target, str) or not target:
            raise ProtocolError("cancel requires target_request_id")


def _validate_alignment_params(params: dict[str, Any], *, start: int, end: int) -> None:
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError("final alignment text must not be empty")
    if params.get("manual_language") is not True or params.get("language") not in {
        "zh",
        "ja",
        "en",
    }:
        raise ProtocolError("final alignment requires a manual zh, ja, or en language")
    gate = params.get("quality_gate")
    required = {
        "no_decode_loop",
        "no_missing_text",
        "normal_character_rate",
        "language_matches",
        "coverage_ratio",
        "voiced_seconds",
    }
    if not isinstance(gate, dict) or required - gate.keys():
        raise ProtocolError("final alignment requires a complete quality_gate")
    for name in (
        "no_decode_loop",
        "no_missing_text",
        "normal_character_rate",
        "language_matches",
    ):
        if gate.get(name) is not True:
            raise ProtocolError(f"final alignment quality gate failed: {name}")
    coverage = gate.get("coverage_ratio")
    voiced = gate.get("voiced_seconds")
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or not 0.5 <= float(coverage) <= 1.0
    ):
        raise ProtocolError("final alignment coverage_ratio must be in 0.5..1")
    if (
        isinstance(voiced, bool)
        or not isinstance(voiced, (int, float))
        or not math.isfinite(float(voiced))
        or float(voiced) <= 0
    ):
        raise ProtocolError("final alignment voiced_seconds must be positive")
    duration = (end - start) / 16_000
    maximum = params.get("safe_max_seconds", 30.0)
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, (int, float))
        or not math.isfinite(float(maximum))
        or not 0 < float(maximum) <= 30.0
        or duration >= float(maximum)
    ):
        raise ProtocolError("final alignment must be shorter than its safe maximum (at most 30s)")
    ratio = len("".join(text.split())) / float(voiced)
    if ratio <= 0 or ratio > 24:
        raise ProtocolError("final alignment text/voiced-duration ratio is abnormal")


def validate_readonly_audio_path(path: str, allowed_roots: tuple[Path, ...]) -> Path:
    """Resolve a batch path below a declared data root and reject mutable/special files."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ProtocolError("audio_path must name an existing regular non-symlink file")
    resolved = candidate.resolve(strict=True)
    roots = tuple(root.resolve(strict=True) for root in allowed_roots)
    if not roots or not any(resolved.is_relative_to(root) for root in roots):
        raise ProtocolError("audio_path is outside the worker's allowed data roots")
    if resolved.stat().st_mode & 0o222:
        raise ProtocolError("batch audio must be read-only")
    return resolved


def _validate_segment(segment: dict[str, Any]) -> None:
    start = segment.get("start_sample")
    end = segment.get("end_sample")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        raise ProtocolError("response segments require ordered absolute sample indices")
    speaker = segment.get("speaker_local")
    if speaker is not None and (not isinstance(speaker, str) or not speaker):
        raise ProtocolError("speaker_local must be a non-empty string or null")
    if "overlap" in segment and not isinstance(segment["overlap"], bool):
        raise ProtocolError("segment overlap must be boolean")
    events = segment.get("acoustic_events")
    if events is not None and (
        not isinstance(events, (list, tuple))
        or any(
            not (
                (isinstance(event, str) and event)
                or (
                    isinstance(event, dict)
                    and isinstance(event.get("label"), str)
                    and bool(event["label"])
                )
            )
            for event in events
        )
    ):
        raise ProtocolError("acoustic_events require non-empty strings or label objects")
    confidence = segment.get("confidence_raw")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
    ):
        raise ProtocolError("confidence_raw must be finite or null")
    timing_kind = segment.get("timing_kind")
    if timing_kind is not None and timing_kind not in {
        "native",
        "request_span_not_model_native",
        "qwen_forced_alignment",
    }:
        raise ProtocolError("segment timing_kind is invalid")
    words = segment.get("words")
    if words is not None:
        if not isinstance(words, (list, tuple)) or any(
            not isinstance(word, dict) for word in words
        ):
            raise ProtocolError("segment words must be a list of objects")
        for word in words:
            _validate_word(word, segment_start=start, segment_end=end)
    _reject_quality_probability(segment)


def _validate_body_asr_params(params: dict[str, Any], *, start: int, end: int) -> None:
    required = {
        "core_start_sample",
        "core_end_sample",
        "language",
        "manual_language",
        "candidate_role",
        "experimental_enabled",
        "hints",
        "decode",
        "batch_items",
    }
    missing = required - params.keys()
    if missing:
        raise ProtocolError(f"body ASR missing fields: {sorted(missing)}")
    core_start = params["core_start_sample"]
    core_end = params["core_end_sample"]
    if (
        isinstance(core_start, bool)
        or not isinstance(core_start, int)
        or isinstance(core_end, bool)
        or not isinstance(core_end, int)
        or not start <= core_start < core_end <= end
    ):
        raise ProtocolError("body ASR core range must be contained by its audio range")
    if params["manual_language"] is not True or params["language"] not in BODY_ASR_LANGUAGES:
        raise ProtocolError("body ASR requires a manual zh, ja, or en language")
    if params["candidate_role"] not in BODY_ASR_ROLES:
        raise ProtocolError("body ASR candidate_role is invalid")
    if not isinstance(params["experimental_enabled"], bool):
        raise ProtocolError("body ASR experimental_enabled must be boolean")
    if params["batch_items"] != 1 or isinstance(params["batch_items"], bool):
        raise ProtocolError("body ASR accepts exactly one audio item")
    context = params.get("rolling_context", [])
    if (
        not isinstance(context, list)
        or len(context) > 3
        or any(not isinstance(item, str) or not item.strip() for item in context)
        or sum(len(item) for item in context) > 2400
    ):
        raise ProtocolError("body ASR rolling context must contain at most three bounded strings")
    hints = params["hints"]
    if not isinstance(hints, (list, tuple)) or any(not isinstance(hint, dict) for hint in hints):
        raise ProtocolError("body ASR hints must be a list of objects")
    for hint in hints:
        canonical = hint.get("canonical")
        reading = hint.get("reading")
        category = hint.get("category")
        language = hint.get("language")
        if not isinstance(canonical, str) or not canonical.strip():
            raise ProtocolError("body ASR hint canonical form is invalid")
        if not isinstance(reading, str) or not reading.strip():
            raise ProtocolError("body ASR hint reading is invalid")
        if category not in BODY_ASR_HINT_CATEGORIES:
            raise ProtocolError("body ASR hint category is invalid")
        if language is not None and language not in BODY_ASR_LANGUAGES:
            raise ProtocolError("body ASR hint language is invalid")
    decode = params["decode"]
    if not isinstance(decode, dict):
        raise ProtocolError("body ASR requires a decode object")
    _validate_body_asr_decode(decode)


def _validate_body_asr_decode(decode: dict[str, Any]) -> None:
    if (
        isinstance(decode.get("temperature"), bool)
        or decode.get("temperature") != 0.0
        or decode.get("do_sample") is not False
        or decode.get("batch_size") != 1
        or isinstance(decode.get("batch_size"), bool)
        or decode.get("mixed_length_batch") is not False
    ):
        raise ProtocolError(
            "body ASR decode requires temperature=0, do_sample=false, "
            "batch_size=1, and no mixed batch"
        )
    for field_name, allow_zero in (
        ("seed", True),
        ("max_new_tokens", False),
        ("max_output_characters", False),
    ):
        value = decode.get(field_name)
        minimum = 0 if allow_zero else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ProtocolError(f"body ASR decode {field_name} is invalid")


def _validate_word(word: dict[str, Any], *, segment_start: int, segment_end: int) -> None:
    start = word.get("start_sample")
    end = word.get("end_sample")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or not segment_start <= start < end <= segment_end
    ):
        raise ProtocolError("word timing must be absolute and contained by its segment")
    text = word.get("text")
    if not isinstance(text, str) or not text:
        raise ProtocolError("word text must be non-empty")
    confidence = word.get("confidence_raw")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
    ):
        raise ProtocolError("word confidence_raw must be finite or null")


def _validate_result_extensions(result: dict[str, Any]) -> None:
    role = result.get("text_role")
    adopted = result.get("adopted_as_final")
    if role is not None and (not isinstance(role, str) or not role):
        raise ProtocolError("text_role must be a non-empty string")
    if adopted is not None and not isinstance(adopted, bool):
        raise ProtocolError("adopted_as_final must be boolean")
    if role == "coarse_timeline_consensus_candidate_boundary_reference" and adopted is not False:
        raise ProtocolError("coarse structure text may not be adopted as final")

    exclusive = result.get("exclusive_segments")
    if exclusive is not None:
        if not isinstance(exclusive, (list, tuple)) or any(
            not isinstance(segment, dict) for segment in exclusive
        ):
            raise ProtocolError("exclusive_segments must be a list of segment objects")
        for segment in exclusive:
            _validate_segment(segment)

    embeddings = result.get("embeddings")
    if embeddings is None:
        return
    if not isinstance(embeddings, (list, tuple)) or any(
        not isinstance(embedding, dict) for embedding in embeddings
    ):
        raise ProtocolError("embeddings must be a list of objects")
    for embedding in embeddings:
        _validate_segment(embedding)
        speaker = embedding.get("speaker_local")
        vector = embedding.get("vector")
        quality = embedding.get("signal_quality")
        support_count = embedding.get("support_count", 1)
        if not isinstance(speaker, str) or not speaker:
            raise ProtocolError("embedding speaker_local must be a non-empty string")
        if (
            not isinstance(vector, (list, tuple))
            or not vector
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in vector
            )
        ):
            raise ProtocolError("embedding vector must contain finite numbers")
        if (
            isinstance(quality, bool)
            or not isinstance(quality, (int, float))
            or not math.isfinite(float(quality))
            or not 0 <= float(quality) <= 1
        ):
            raise ProtocolError("embedding signal_quality must be within [0, 1]")
        if (
            isinstance(support_count, bool)
            or not isinstance(support_count, int)
            or support_count < 1
        ):
            raise ProtocolError("embedding support_count must be positive")


def _reject_quality_probability(value: Any) -> None:
    if isinstance(value, dict):
        if "quality_probability" in value:
            raise ProtocolError("quality_probability is unavailable before local calibration")
        for item in value.values():
            _reject_quality_probability(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_quality_probability(item)
