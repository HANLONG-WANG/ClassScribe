"""Dependency-light helpers shared by isolated batch-ASR adapters."""

from __future__ import annotations

import tempfile
import time
import wave
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from classscribe_protocol.adapter import AdapterError
from classscribe_protocol.messages import RPCErrorCode

SAMPLE_RATE = 16_000


@dataclass(frozen=True, slots=True)
class DeterministicDecode:
    max_new_tokens: int
    max_output_characters: int
    seed: int


def deterministic_decode(params: Mapping[str, Any]) -> DeterministicDecode:
    raw = params.get("decode")
    if not isinstance(raw, Mapping):
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "body ASR requires a decode object")
    if (
        raw.get("temperature") != 0.0
        or raw.get("do_sample") is not False
        or raw.get("batch_size") != 1
        or raw.get("mixed_length_batch") is not False
    ):
        raise AdapterError(
            RPCErrorCode.INVALID_REQUEST,
            "body ASR requires temperature=0, do_sample=false, batch_size=1, and no mixed batch",
        )
    max_tokens = raw.get("max_new_tokens")
    max_characters = raw.get("max_output_characters")
    seed = raw.get("seed")
    invalid_scalar = any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (max_tokens, max_characters, seed)
    )
    if invalid_scalar or max_tokens is None or max_characters is None or seed is None:
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "decode bounds and seed are invalid")
    if max_tokens < 1 or max_characters < 1:
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "decode bounds must be positive")
    if params.get("batch_items") != 1:
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "body ASR accepts exactly one audio item")
    return DeterministicDecode(int(max_tokens), int(max_characters), int(seed))


def manual_language(params: Mapping[str, Any], allowed: frozenset[str]) -> str:
    """Require an explicit language; body ASR is never allowed to auto-detect."""

    language = params.get("language")
    if params.get("manual_language") is not True or not isinstance(language, str):
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "body ASR requires manual_language=true")
    if language not in allowed:
        raise AdapterError(
            RPCErrorCode.INVALID_REQUEST,
            f"worker does not support requested body-ASR language: {language}",
        )
    return language


def pronunciation_context(params: Mapping[str, Any]) -> str:
    """Render bounded biasing context that explicitly forbids unsupported insertion."""

    rolling = params.get("rolling_context", ())
    if (
        not isinstance(rolling, (list, tuple))
        or len(rolling) > 3
        or any(not isinstance(item, str) or not item.strip() for item in rolling)
        or sum(len(item) for item in rolling) > 2400
    ):
        raise AdapterError(
            RPCErrorCode.INVALID_REQUEST,
            "rolling_context must contain at most three bounded strings",
        )
    hints = params.get("hints", ())
    if not isinstance(hints, (list, tuple)):
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "hints must be a list")
    rendered: list[str] = []
    for item in hints:
        if not isinstance(item, Mapping):
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "each hint must be an object")
        canonical = item.get("canonical")
        reading = item.get("reading")
        if not isinstance(canonical, str) or not canonical.strip():
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "hint canonical form is invalid")
        if not isinstance(reading, str) or not reading.strip():
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "hint reading is invalid")
        rendered.append(f"{canonical.strip()} ({reading.strip()})")
    parts: list[str] = []
    if rolling:
        parts.append(
            "Recently user-confirmed transcript for limited context only: "
            + " | ".join(item.strip() for item in rolling)
            + ". Never copy text that is not supported by the current audio."
        )
    if rendered:
        parts.append(
            "Possible spoken terms and pronunciations: "
            + "; ".join(rendered)
            + ". Use a term only when supported by the audio; never insert it merely because "
            "it is listed."
        )
    return " ".join(parts)


def bounded_text(text: Any, decode: DeterministicDecode) -> str:
    if not isinstance(text, str):
        raise AdapterError(RPCErrorCode.INTERNAL, "ASR backend did not return text")
    if len(text) > decode.max_output_characters:
        raise AdapterError(RPCErrorCode.INTERNAL, "ASR output exceeded duration character bound")
    return text


def response_payload(
    params: Mapping[str, Any],
    *,
    text: str,
    language: str,
    backend: str,
    started: float,
    generated_tokens: int | None = None,
    confidence_raw: float | None = None,
    words: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    extra_metrics: Mapping[str, int | float | str | bool | None] | None = None,
) -> dict[str, Any]:
    duration_seconds = (int(params["end_sample"]) - int(params["start_sample"])) / SAMPLE_RATE
    elapsed_ms = (time.perf_counter() - started) * 1000
    metrics: dict[str, int | float | str | bool | None] = {
        "inference_ms": round(elapsed_ms, 3),
        "peak_vram_mb": 0.0,
        "rtf": round(elapsed_ms / 1000 / duration_seconds, 6),
        "backend": backend,
        "window_samples": int(params["end_sample"]) - int(params["start_sample"]),
    }
    if generated_tokens is not None:
        metrics["generated_tokens"] = generated_tokens
    if extra_metrics:
        metrics.update(extra_metrics)
    return {
        "language": language,
        "raw_text": text,
        "normalized_text": " ".join(text.split()),
        "segments": [
            request_span_segment(
                params,
                text,
                confidence_raw=confidence_raw,
                words=words,
            )
        ],
        "metrics": metrics,
        "warnings": warnings or [],
        "decode": dict(params["decode"]),
    }


@contextmanager
def canonical_window(params: Mapping[str, Any], *, prefix: str) -> Iterator[Path]:
    start = int(params["start_sample"])
    end = int(params["end_sample"])
    if end <= start:
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "body ASR window is empty")
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        destination = Path(temporary) / "segment.wav"
        _write_clip(Path(str(params["audio_path"])), destination, start, end)
        yield destination


def request_span_segment(
    params: Mapping[str, Any],
    text: str,
    *,
    confidence_raw: float | None = None,
    words: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "start_sample": int(params["start_sample"]),
        "end_sample": int(params["end_sample"]),
        "text": text,
        "confidence_raw": confidence_raw,
        "words": words or [],
        "timing_kind": "native" if words else "request_span_not_model_native",
    }


def _write_clip(source: Path, destination: Path, start: int, end: int) -> None:
    with wave.open(str(source), "rb") as reader:
        if (
            reader.getframerate() != SAMPLE_RATE
            or reader.getnchannels() != 1
            or reader.getsampwidth() != 2
        ):
            raise AdapterError(
                RPCErrorCode.INVALID_REQUEST,
                "body ASR requires canonical 16 kHz mono s16 WAV",
            )
        if start < 0 or end > reader.getnframes():
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "body ASR window exceeds audio")
        reader.setpos(start)
        frames = reader.readframes(end - start)
    with wave.open(str(destination), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(frames)
