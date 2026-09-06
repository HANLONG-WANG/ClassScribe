"""Offline Qwen3-ASR and Qwen3 ForcedAligner adapter."""

from __future__ import annotations

import asyncio
import gc
import tempfile
import time
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from classscribe_protocol.adapter import AdapterError, StatefulAdapter
from classscribe_protocol.batch_audio import (
    SAMPLE_RATE,
    bounded_text,
    canonical_window,
    deterministic_decode,
    manual_language,
    pronunciation_context,
    response_payload,
)
from classscribe_protocol.messages import RPCErrorCode

_ASR_MODELS = frozenset({"qwen3_asr_1_7b", "qwen3_asr_0_6b"})
_ALIGN_MODEL = "qwen3_forced_aligner_0_6b"
_LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "en": "English"}


class QwenAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "qwen",
            capabilities=("asr_zh", "asr_ja", "asr_en", "streaming", "alignment", "context"),
            supported_methods=(
                "transcribe_batch",
                "stream_open",
                "stream_push",
                "stream_flush",
                "stream_close",
                "align",
            ),
        )
        self._model: Any | None = None
        self._torch: Any | None = None
        self._model_kind: str | None = None
        self._decode_lock = asyncio.Lock()
        self._stream_bases: dict[str, int] = {}
        self._stream_decoded_samples: dict[str, int] = {}
        self._stream_chunk_samples: dict[str, int] = {}
        self._stream_last: dict[str, Mapping[str, Any]] = {}

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load" and params.get("model_id") in _ASR_MODELS:
            return await self._load_qwen(params, cancelled)
        if method == "load" and params.get("model_id") == _ALIGN_MODEL:
            return await self._load_aligner(params, cancelled)
        if method == "unload":
            self._model = None
            self._torch = None
            self._model_kind = None
            self._stream_bases.clear()
            self._stream_decoded_samples.clear()
            self._stream_chunk_samples.clear()
            self._stream_last.clear()
            gc.collect()
            return await super().dispatch(method, params, cancelled)
        if method == "transcribe_batch" and self._model_kind == "asr":
            return await self._transcribe(params, cancelled)
        if method == "align" and self._model_kind == "aligner":
            return await self._align(params, cancelled)
        if method == "stream_open" and self._model_kind == "asr":
            result = await super().dispatch(method, params, cancelled)
            stream_id = str(params["stream_id"])
            self._stream_decoded_samples[stream_id] = 0
            self._stream_last.pop(stream_id, None)
            self._stream_chunk_samples[stream_id] = (
                int(params.get("chunk_ms", 560)) * SAMPLE_RATE // 1000
            )
            return result
        if method == "stream_push" and self._model_kind == "asr":
            return await self._stream_push(params, cancelled)
        if method == "stream_flush" and self._model_kind == "asr":
            return await self._stream_flush(params, cancelled)
        if method == "stream_close" and self._model_kind == "asr":
            stream_id = str(params["stream_id"])
            result = await super().dispatch(method, params, cancelled)
            self._stream_bases.pop(stream_id, None)
            self._stream_decoded_samples.pop(stream_id, None)
            self._stream_chunk_samples.pop(stream_id, None)
            self._stream_last.pop(stream_id, None)
            return result
        return await super().dispatch(method, params, cancelled)

    async def _stream_push(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        result = await super().dispatch("stream_push", params, cancelled)
        stream_id = str(params["stream_id"])
        absolute_start = int(params.get("absolute_start_sample", 0))
        self._stream_bases.setdefault(stream_id, absolute_start)
        pcm = self._streams[stream_id]
        samples = len(pcm) // 2
        threshold = self._stream_chunk_samples.get(stream_id, 560 * SAMPLE_RATE // 1000)
        if samples - self._stream_decoded_samples.get(stream_id, 0) < threshold:
            return result
        self._stream_decoded_samples[stream_id] = samples
        decoded = await self._decode_stream(
            bytes(pcm),
            start_sample=self._stream_bases[stream_id],
            language=str(params.get("language", "auto")),
            context="",
            cancelled=cancelled,
            final=False,
        )
        self._stream_last[stream_id] = decoded
        return decoded

    async def _stream_flush(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        stream_id = str(params["stream_id"])
        pcm = self._streams.get(stream_id)
        if pcm is None:
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "unknown stream")
        context_value = params.get("rolling_context", ())
        context = (
            "\n".join(str(item) for item in context_value)
            if isinstance(context_value, (list, tuple))
            else ""
        )
        fast = params.get("confirmation") == "fast"
        cached = self._stream_last.get(stream_id)
        if (
            fast
            and cached is not None
            and self._stream_decoded_samples.get(stream_id) == len(pcm) // 2
        ):
            result = _finalize_cached_stream(cached)
        else:
            result = await self._decode_stream(
                bytes(pcm),
                start_sample=self._stream_bases.get(
                    stream_id, int(params.get("core_start_sample", 0))
                ),
                language=str(params.get("language", "auto")),
                context=context,
                cancelled=cancelled,
                final=True,
            )
        text = str(result.get("normalized_text", ""))
        return {**result, "candidates": [text] if text else []}

    async def _decode_stream(
        self,
        pcm: bytes,
        *,
        start_sample: int,
        language: str,
        context: str,
        cancelled: asyncio.Event,
        final: bool,
    ) -> Mapping[str, Any]:
        if not pcm:
            return {"raw_text": "", "normalized_text": "", "segments": []}
        started = time.perf_counter()

        def infer() -> tuple[str, str]:
            assert self._model is not None
            with tempfile.TemporaryDirectory(prefix="classscribe-qwen-stream-") as temporary:
                clip = Path(temporary) / "stream.wav"
                with wave.open(str(clip), "wb") as writer:
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(SAMPLE_RATE)
                    writer.writeframes(pcm)
                kwargs: dict[str, Any] = {
                    "audio": str(clip),
                    "context": context,
                    "return_time_stamps": False,
                }
                if language in _LANGUAGE_NAMES:
                    kwargs["language"] = _LANGUAGE_NAMES[language]
                results = self._model.transcribe(**kwargs)
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError("Qwen streaming decode must return exactly one result")
            return str(results[0].text), str(results[0].language)

        async with self._decode_lock:
            text, reported_language = await asyncio.to_thread(infer)
        if cancelled.is_set():
            raise asyncio.CancelledError
        end_sample = start_sample + len(pcm) // 2
        pieces = tuple(text) if text else ()
        words: list[dict[str, Any]] = []
        for index, piece in enumerate(pieces):
            word_start = start_sample + (end_sample - start_sample) * index // len(pieces)
            word_end = start_sample + (end_sample - start_sample) * (index + 1) // len(pieces)
            if word_end > word_start:
                words.append(
                    {
                        "text": piece,
                        "start_sample": word_start,
                        "end_sample": word_end,
                        "stable": final or index < max(0, len(pieces) - 2),
                    }
                )
        return {
            "raw_text": text,
            "normalized_text": text,
            "language": (
                language if language in _LANGUAGE_NAMES else _reported_language(reported_language)
            ),
            "segments": [
                {
                    "start_sample": start_sample,
                    "end_sample": end_sample,
                    "text": text,
                    "words": words,
                }
            ]
            if text
            else [],
            "metrics": {
                "backend": "qwen_asr_incremental",
                "inference_ms": round((time.perf_counter() - started) * 1000, 3),
                "final": final,
                "temporary_audio_retained": False,
            },
            "warnings": [],
        }

    async def _load_qwen(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or not (model_path / "config.json").is_file():
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "Qwen ASR model_path must be a complete non-symlink local snapshot",
            )

        def load_model() -> tuple[Any, Any]:
            try:
                import torch  # type: ignore[import-not-found]
                from qwen_asr import Qwen3ASRModel  # type: ignore[import-not-found]
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated Qwen worker environment is incomplete",
                ) from exc
            device = str(params.get("device", "auto"))
            if device == "auto":
                device = "cuda:0" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
            model = Qwen3ASRModel.from_pretrained(
                str(model_path),
                dtype=dtype,
                device_map=device,
                max_inference_batch_size=1,
                max_new_tokens=2048,
                local_files_only=True,
                attn_implementation=str(params.get("attention", "sdpa")),
            )
            return model, torch

        try:
            model, torch = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"Qwen ASR initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._torch = torch
        self._model_kind = "asr"
        return {
            **result,
            "backend": "qwen_asr_transformers",
            "offline": True,
            "max_inference_batch_size": 1,
        }

    async def _load_aligner(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or not (model_path / "config.json").is_file():
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "Qwen forced-aligner model_path must be a complete non-symlink local snapshot",
            )

        def load_model() -> tuple[Any, Any]:
            try:
                import torch
                from qwen_asr import Qwen3ForcedAligner
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated Qwen worker environment is incomplete",
                ) from exc
            device = str(params.get("device", "auto"))
            if device == "auto":
                device = "cuda:0" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
            model = Qwen3ForcedAligner.from_pretrained(
                str(model_path),
                dtype=dtype,
                device_map=device,
                local_files_only=True,
                attn_implementation=str(params.get("attention", "sdpa")),
            )
            return model, torch

        try:
            model, torch = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"Qwen forced-aligner initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._torch = torch
        self._model_kind = "aligner"
        return {**result, "backend": "qwen3_forced_aligner", "offline": True}

    async def _align(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        language = _alignment_language(params)
        text = str(params["text"])
        start = int(params["start_sample"])
        end = int(params["end_sample"])
        started = time.perf_counter()

        def infer() -> Any:
            assert self._model is not None
            with canonical_window(params, prefix="classscribe-qwen-align-") as clip:
                results = self._model.align(
                    audio=str(clip),
                    text=text,
                    language=_LANGUAGE_NAMES[language],
                )
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError("Qwen forced aligner must return exactly one result")
            return results[0]

        try:
            async with self._decode_lock:
                native = await asyncio.to_thread(infer)
            words = _alignment_words(native, offset=start, end_limit=end)
        except Exception as exc:
            if isinstance(exc, AdapterError):
                raise
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"Qwen forced alignment failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "raw_text": text,
            "normalized_text": text,
            "language": language,
            "segments": [
                {
                    "start_sample": start,
                    "end_sample": end,
                    "text": text,
                    "words": words,
                    "timing_kind": "qwen_forced_alignment",
                }
            ],
            "metrics": {
                "backend": "qwen3_forced_aligner",
                "inference_ms": round(elapsed_ms, 3),
                "aligned_tokens": len(words),
                "text_unchanged": True,
            },
            "warnings": [],
        }

    async def _transcribe(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        language = manual_language(params, frozenset(_LANGUAGE_NAMES))
        decode = deterministic_decode(params)
        context = pronunciation_context(params)
        started = time.perf_counter()

        def infer() -> tuple[str, str]:
            assert self._model is not None
            assert self._torch is not None
            self._torch.manual_seed(decode.seed)
            previous_bound = self._model.max_new_tokens
            self._model.max_new_tokens = decode.max_new_tokens
            try:
                with canonical_window(params, prefix="classscribe-qwen-") as clip:
                    results = self._model.transcribe(
                        audio=str(clip),
                        context=context,
                        language=_LANGUAGE_NAMES[language],
                        return_time_stamps=False,
                    )
            finally:
                self._model.max_new_tokens = previous_bound
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError("Qwen ASR must return exactly one result")
            return str(results[0].text), str(results[0].language)

        try:
            async with self._decode_lock:
                text, reported_language = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"Qwen ASR inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        japanese_variant = language == "ja" and params.get("candidate_role") == "experimental"
        return response_payload(
            params,
            text=bounded_text(text, decode),
            language=language,
            backend="qwen_asr_transformers",
            started=started,
            extra_metrics={
                "model_reported_language": reported_language,
                "language_forced": True,
                "context_length": len(context),
                "route_variant": "qwen3_1_7b_forced_japanese_experimental"
                if japanese_variant
                else "standard_forced_language",
            },
        )


def create_adapter() -> QwenAdapter:
    return QwenAdapter()


def _reported_language(value: str) -> str:
    normalized = value.strip().casefold()
    return {"chinese": "zh", "japanese": "ja", "english": "en"}.get(normalized, normalized)


def _finalize_cached_stream(value: Mapping[str, Any]) -> Mapping[str, Any]:
    segments: list[dict[str, Any]] = []
    raw_segments = value.get("segments", ())
    if isinstance(raw_segments, (list, tuple)):
        for segment in raw_segments:
            if not isinstance(segment, Mapping):
                continue
            words = segment.get("words", ())
            final_words = (
                [{**word, "stable": True} for word in words if isinstance(word, Mapping)]
                if isinstance(words, (list, tuple))
                else []
            )
            segments.append({**segment, "words": final_words})
    metrics = value.get("metrics", {})
    return {
        **value,
        "segments": segments,
        "metrics": {
            **(metrics if isinstance(metrics, Mapping) else {}),
            "final": True,
            "fast_cached_flush": True,
        },
    }


def _alignment_language(params: Mapping[str, Any]) -> str:
    if params.get("alignment_contract") != "final-align-v1":
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "final alignment contract required")
    language = params.get("language")
    if params.get("manual_language") is not True or language not in _LANGUAGE_NAMES:
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "manual zh, ja, or en language required")
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "alignment text must not be empty")
    gate = params.get("quality_gate")
    flags = (
        "no_decode_loop",
        "no_missing_text",
        "normal_character_rate",
        "language_matches",
    )
    if not isinstance(gate, Mapping) or any(gate.get(flag) is not True for flag in flags):
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "alignment quality gate failed")
    coverage = gate.get("coverage_ratio")
    voiced = gate.get("voiced_seconds")
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not 0.5 <= float(coverage) <= 1.0
        or isinstance(voiced, bool)
        or not isinstance(voiced, (int, float))
        or float(voiced) <= 0
    ):
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "alignment acoustic gate is invalid")
    duration = (int(params["end_sample"]) - int(params["start_sample"])) / SAMPLE_RATE
    safe_max = params.get("safe_max_seconds", 30.0)
    if (
        isinstance(safe_max, bool)
        or not isinstance(safe_max, (int, float))
        or not 0 < float(safe_max) <= 30.0
        or duration >= float(safe_max)
        or len("".join(text.split())) / float(voiced) > 24
    ):
        raise AdapterError(RPCErrorCode.INVALID_REQUEST, "alignment duration/text ratio is unsafe")
    return str(language)


def _alignment_words(native: Any, *, offset: int, end_limit: int) -> list[dict[str, Any]]:
    items = native
    if isinstance(native, Mapping):
        items = native.get("items", native.get("timestamps", native.get("words")))
    elif hasattr(native, "items"):
        items = native.items
    elif hasattr(native, "timestamps"):
        items = native.timestamps
    if not isinstance(items, (list, tuple)):
        raise AdapterError(RPCErrorCode.INTERNAL, "Qwen alignment output has no token list")
    words: list[dict[str, Any]] = []
    previous_end = offset
    for item in items:
        if isinstance(item, Mapping):
            token = item.get("text")
            start_time = item.get("start_time")
            end_time = item.get("end_time")
        else:
            token = getattr(item, "text", None)
            start_time = getattr(item, "start_time", None)
            end_time = getattr(item, "end_time", None)
        if not isinstance(token, str) or start_time is None or end_time is None:
            raise AdapterError(RPCErrorCode.INTERNAL, "Qwen alignment token is invalid")
        start_sample = max(offset, offset + round(float(start_time) * SAMPLE_RATE))
        end_sample = min(end_limit, offset + round(float(end_time) * SAMPLE_RATE))
        if start_sample < previous_end or end_sample <= start_sample:
            raise AdapterError(RPCErrorCode.INTERNAL, "Qwen alignment tokens are not monotonic")
        words.append(
            {
                "start_sample": start_sample,
                "end_sample": end_sample,
                "text": token,
                "confidence_raw": None,
            }
        )
        previous_end = end_sample
    if not words:
        raise AdapterError(RPCErrorCode.INTERNAL, "Qwen alignment returned no tokens")
    return words
