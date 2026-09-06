"""Offline Nemotron cached-streaming adapter with bounded incremental sessions."""

from __future__ import annotations

import asyncio
import gc
import tempfile
import time
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from classscribe_protocol.adapter import AdapterError, StatefulAdapter
from classscribe_protocol.messages import RPCErrorCode

SAMPLE_RATE = 16_000
_SUPPORTED_MODELS = {
    "nemotron_3_5_asr_streaming_0_6b",
    "nemotron_speech_streaming_en_0_6b",
}


@dataclass(slots=True)
class _CacheStream:
    audio_buffer: Any
    cache_last_channel: Any
    cache_last_time: Any
    cache_last_channel_len: Any
    attention_context: tuple[int, int]
    pending_pcm: bytearray = field(default_factory=bytearray)
    previous_hypotheses: Any | None = None
    previous_pred_out: Any | None = None
    audio_started: bool = False
    steps: int = 0
    language: str = "auto"


class NemotronAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "nemotron",
            capabilities=("asr_zh", "asr_ja", "asr_en", "streaming", "punctuation"),
            supported_methods=(
                "transcribe_batch",
                "stream_open",
                "stream_push",
                "stream_flush",
                "stream_close",
            ),
        )
        self._model: Any | None = None
        self._torch: Any | None = None
        self._numpy: Any | None = None
        self._audio_buffer_type: Any | None = None
        self._decode_lock = asyncio.Lock()
        self._stream_bases: dict[str, int] = {}
        self._decoded_samples: dict[str, int] = {}
        self._chunk_samples: dict[str, int] = {}
        self._stream_last: dict[str, Mapping[str, Any]] = {}
        self._cache_streams: dict[str, _CacheStream] = {}

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load" and params.get("model_id") in _SUPPORTED_MODELS:
            return await self._load(params, cancelled)
        if method == "unload":
            self._release()
            return await super().dispatch(method, params, cancelled)
        if method == "stream_open" and self._model is not None:
            result = await super().dispatch(method, params, cancelled)
            stream_id = str(params["stream_id"])
            try:
                self._decoded_samples[stream_id] = 0
                self._stream_last.pop(stream_id, None)
                chunk_ms = int(params.get("chunk_ms", 560))
                self._chunk_samples[stream_id] = chunk_ms * SAMPLE_RATE // 1000
                self._cache_streams[stream_id] = await self._open_cache_stream(
                    chunk_ms=chunk_ms,
                    language=str(params.get("language", "auto")),
                )
            except BaseException:
                self._streams.pop(stream_id, None)
                self._decoded_samples.pop(stream_id, None)
                self._chunk_samples.pop(stream_id, None)
                raise
            return result
        if method == "stream_push" and self._model is not None:
            return await self._push(params, cancelled)
        if method == "stream_flush" and self._model is not None:
            return await self._flush(params, cancelled)
        if method == "stream_close" and self._model is not None:
            stream_id = str(params["stream_id"])
            result = await super().dispatch(method, params, cancelled)
            self._stream_bases.pop(stream_id, None)
            self._decoded_samples.pop(stream_id, None)
            self._chunk_samples.pop(stream_id, None)
            self._stream_last.pop(stream_id, None)
            self._cache_streams.pop(stream_id, None)
            return result
        if method == "transcribe_batch" and self._model is not None:
            return await self._batch(params, cancelled)
        return await super().dispatch(method, params, cancelled)

    async def _load(self, params: Mapping[str, Any], cancelled: asyncio.Event) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        archives = tuple(model_path.glob("*.nemo")) if model_path.is_dir() else ()
        if model_path.is_symlink() or len(archives) != 1:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "Nemotron snapshot must contain exactly one local .nemo archive",
            )

        def load() -> tuple[Any, Any, Any, Any, str]:
            try:
                import numpy  # type: ignore[import-not-found]
                import torch  # type: ignore[import-not-found]
                from nemo.collections.asr.models import ASRModel  # type: ignore[import-not-found]
                from nemo.collections.asr.parts.utils.streaming_utils import (  # type: ignore[import-not-found]
                    CacheAwareStreamingAudioBuffer,
                )
                from nemo.utils import model_utils  # type: ignore[import-not-found]
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated Nemotron worker environment lacks NeMo ASR",
                ) from exc
            device = str(params.get("device", "auto"))
            if device == "auto":
                device = "cuda:0" if torch.cuda.is_available() else "cpu"
            config = ASRModel.restore_from(str(archives[0]), return_config=True)
            target = config.get("target")
            if not isinstance(target, str) or not target.startswith(
                "nemo.collections.asr.models."
            ):
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "Nemotron archive has no supported NeMo ASR target class",
                )
            model_type = model_utils.import_class_by_path(target)
            if not isinstance(model_type, type) or not issubclass(model_type, ASRModel):
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "Nemotron archive target is not a NeMo ASR model class",
                )
            model = cast(Any, model_type).restore_from(str(archives[0]), map_location=device)
            model.freeze()
            model.eval()
            model.to(device)
            if hasattr(model, "set_inference_prompt"):
                strip_language_tags = getattr(
                    getattr(model, "decoding", None), "set_strip_lang_tags", None
                )
                if not callable(strip_language_tags):
                    raise AdapterError(
                        RPCErrorCode.MODEL_LOAD_FAILED,
                        "prompt-aware Nemotron cannot strip language control tags",
                    )
                strip_language_tags(True)
            if not hasattr(model, "conformer_stream_step") or not hasattr(model, "encoder"):
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "Nemotron archive does not expose cache-aware Conformer streaming",
                )
            return model, torch, numpy, CacheAwareStreamingAudioBuffer, device

        try:
            model, torch, numpy, audio_buffer_type, device = await asyncio.to_thread(load)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"Nemotron initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._torch = torch
        self._numpy = numpy
        self._audio_buffer_type = audio_buffer_type
        return {**result, "backend": "nemo_cached_streaming", "device": device, "offline": True}

    async def _open_cache_stream(self, *, chunk_ms: int, language: str) -> _CacheStream:
        def create() -> _CacheStream:
            assert self._model is not None
            assert self._audio_buffer_type is not None
            attention_context = _attention_context(self._model.encoder, chunk_ms)
            self._model.encoder.set_default_att_context_size(list(attention_context))
            audio_buffer = self._audio_buffer_type(
                model=self._model,
                online_normalization=False,
                pad_and_drop_preencoded=False,
            )
            cache_channel, cache_time, cache_length = self._model.encoder.get_initial_cache_state(
                batch_size=1
            )
            return _CacheStream(
                audio_buffer=audio_buffer,
                cache_last_channel=cache_channel,
                cache_last_time=cache_time,
                cache_last_channel_len=cache_length,
                attention_context=attention_context,
                language=language,
            )

        try:
            async with self._decode_lock:
                return await asyncio.to_thread(create)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"Nemotron stream initialization failed: {type(exc).__name__}: {exc}",
            ) from exc

    async def _push(self, params: Mapping[str, Any], cancelled: asyncio.Event) -> Mapping[str, Any]:
        accepted = await super().dispatch("stream_push", params, cancelled)
        stream_id = str(params["stream_id"])
        self._stream_bases.setdefault(stream_id, int(params.get("absolute_start_sample", 0)))
        state = self._cache_streams[stream_id]
        state.language = str(params.get("language", state.language))
        state.pending_pcm.extend(params["pcm_s16le"])
        threshold = self._chunk_samples.get(stream_id, 560 * SAMPLE_RATE // 1000)
        if len(state.pending_pcm) < threshold * 2:
            return accepted
        decoded: Mapping[str, Any] = accepted
        while len(state.pending_pcm) >= threshold * 2:
            pcm = bytes(state.pending_pcm[: threshold * 2])
            del state.pending_pcm[: threshold * 2]
            self._decoded_samples[stream_id] += threshold
            decoded = await self._decode_cached(
                stream_id,
                pcm,
                end_sample=self._stream_bases[stream_id] + self._decoded_samples[stream_id],
                cancelled=cancelled,
                final=False,
            )
            self._stream_last[stream_id] = decoded
        return decoded

    async def _flush(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        stream_id = str(params["stream_id"])
        pcm = self._streams.get(stream_id)
        if pcm is None:
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "unknown stream")
        fast = params.get("confirmation") == "fast"
        if fast:
            state = self._cache_streams[stream_id]
            threshold = self._chunk_samples[stream_id]
            actual_samples = len(state.pending_pcm) // 2
            padded = bytes(state.pending_pcm) + b"\0\0" * (threshold - actual_samples)
            state.pending_pcm.clear()
            self._decoded_samples[stream_id] += actual_samples
            result = await self._decode_cached(
                stream_id,
                padded,
                end_sample=self._stream_bases.get(stream_id, 0) + self._decoded_samples[stream_id],
                cancelled=cancelled,
                final=True,
            )
            result = _finalize_cached_stream(result)
            self._stream_last[stream_id] = result
        else:
            result = await self._decode(
                bytes(pcm),
                start_sample=self._stream_bases.get(
                    stream_id, int(params.get("core_start_sample", 0))
                ),
                language=str(params.get("language", "auto")),
                cancelled=cancelled,
                final=True,
            )
        text = str(result.get("normalized_text", ""))
        return {**result, "candidates": [text] if text else []}

    async def _decode_cached(
        self,
        stream_id: str,
        pcm: bytes,
        *,
        end_sample: int,
        cancelled: asyncio.Event,
        final: bool,
    ) -> Mapping[str, Any]:
        started = time.perf_counter()

        def infer() -> tuple[str, str | None]:
            assert self._model is not None
            assert self._torch is not None
            assert self._numpy is not None
            state = self._cache_streams[stream_id]
            current_context = tuple(int(item) for item in self._model.encoder.att_context_size)
            if current_context != state.attention_context:
                self._model.encoder.set_default_att_context_size(
                    list(state.attention_context)
                )
            if hasattr(self._model, "set_inference_prompt"):
                self._model.set_inference_prompt(_prompt_language(state.language))
            samples = self._numpy.frombuffer(pcm, dtype=self._numpy.int16).astype(
                self._numpy.float32
            )
            samples /= 32768.0
            state.audio_buffer.append_audio(
                samples,
                stream_id=0 if state.audio_started else -1,
            )
            state.audio_started = True
            hypothesis: object | None = None
            for processed, processed_length in state.audio_buffer:
                processed = processed.to(self._model.device)
                processed_length = processed_length.to(self._model.device)
                drop = (
                    0
                    if state.steps == 0
                    else int(
                        getattr(
                            self._model.encoder.streaming_cfg,
                            "drop_extra_pre_encoded",
                            0,
                        )
                    )
                )
                with self._torch.inference_mode():
                    (
                        pred_out,
                        transcriptions,
                        state.cache_last_channel,
                        state.cache_last_time,
                        state.cache_last_channel_len,
                        best_hypotheses,
                    ) = self._model.conformer_stream_step(
                        processed_signal=processed,
                        processed_signal_length=processed_length,
                        cache_last_channel=state.cache_last_channel,
                        cache_last_time=state.cache_last_time,
                        cache_last_channel_len=state.cache_last_channel_len,
                        keep_all_outputs=final,
                        previous_hypotheses=state.previous_hypotheses,
                        previous_pred_out=state.previous_pred_out,
                        drop_extra_pre_encoded=drop,
                        return_transcription=True,
                    )
                state.steps += 1
                state.previous_pred_out = pred_out
                state.previous_hypotheses = best_hypotheses
                hypothesis = _first_hypothesis(best_hypotheses, transcriptions)
            if hypothesis is None:
                hypothesis = _first_hypothesis(state.previous_hypotheses, ())
            reported = getattr(hypothesis, "language", getattr(hypothesis, "lang", None))
            text = str(hypothesis.text if hasattr(hypothesis, "text") else hypothesis)
            return text, str(reported) if reported is not None else None

        try:
            async with self._decode_lock:
                text, reported_language = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"Nemotron cached-stream inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        return _transcription_result(
            text,
            start_sample=self._stream_bases[stream_id],
            end_sample=end_sample,
            language=self._cache_streams[stream_id].language,
            reported_language=reported_language,
            final=final,
            started=started,
            backend="nemo_cached_streaming",
        )

    async def _batch(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        path = Path(str(params["audio_path"]))
        start = int(params["start_sample"])
        end = int(params["end_sample"])
        with wave.open(str(path), "rb") as reader:
            reader.setpos(start)
            pcm = reader.readframes(end - start)
        return await self._decode(
            pcm,
            start_sample=start,
            language=str(params.get("language", "auto")),
            cancelled=cancelled,
            final=True,
        )

    async def _decode(
        self,
        pcm: bytes,
        *,
        start_sample: int,
        language: str,
        cancelled: asyncio.Event,
        final: bool,
    ) -> Mapping[str, Any]:
        if not pcm:
            return {"raw_text": "", "normalized_text": "", "segments": []}
        started = time.perf_counter()

        def infer() -> tuple[str, str | None]:
            assert self._model is not None
            with tempfile.TemporaryDirectory(prefix="classscribe-nemotron-") as temporary:
                clip = Path(temporary) / "stream.wav"
                with wave.open(str(clip), "wb") as writer:
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(SAMPLE_RATE)
                    writer.writeframes(pcm)
                transcribe_options: dict[str, object] = {
                    "batch_size": 1,
                    "return_hypotheses": True,
                }
                if hasattr(self._model, "set_inference_prompt"):
                    config_factory = getattr(self._model, "get_transcribe_config", None)
                    if not callable(config_factory):
                        raise ValueError("prompt-aware Nemotron has no transcribe config")
                    transcribe_config = config_factory()
                    transcribe_config.batch_size = 1
                    transcribe_config.return_hypotheses = True
                    transcribe_config.use_lhotse = False
                    transcribe_config.target_lang = _prompt_language(language)
                    transcribe_options["override_config"] = transcribe_config
                hypotheses = self._model.transcribe([str(clip)], **transcribe_options)
            if not isinstance(hypotheses, list) or len(hypotheses) != 1:
                raise ValueError("Nemotron decode must return one hypothesis")
            value = hypotheses[0]
            text = str(value.text if hasattr(value, "text") else value)
            reported = getattr(value, "language", getattr(value, "lang", None))
            return text, str(reported) if reported is not None else None

        try:
            async with self._decode_lock:
                text, reported_language = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"Nemotron inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        return _transcription_result(
            text,
            start_sample=start_sample,
            end_sample=start_sample + len(pcm) // 2,
            language=language,
            reported_language=reported_language,
            final=final,
            started=started,
            backend="nemo_batch_redecode",
        )

    def _release(self) -> None:
        self._model = None
        torch = self._torch
        self._torch = None
        self._numpy = None
        self._audio_buffer_type = None
        self._stream_bases.clear()
        self._decoded_samples.clear()
        self._chunk_samples.clear()
        self._stream_last.clear()
        self._cache_streams.clear()
        gc.collect()
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()


def create_adapter() -> NemotronAdapter:
    return NemotronAdapter()


def _reported_language(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    return {"chinese": "zh", "japanese": "ja", "english": "en"}.get(normalized, normalized)


def _prompt_language(value: str) -> str:
    return {"zh": "zh-CN", "ja": "ja-JP", "en": "en-US"}.get(value, "auto")


def _attention_context(encoder: Any, chunk_ms: int) -> tuple[int, int]:
    right_context = {80: 0, 160: 1, 320: 3, 560: 6, 1120: 13}.get(chunk_ms)
    if right_context is None:
        raise ValueError("Nemotron chunk_ms must be one of 80, 160, 320, 560, or 1120")
    for value in getattr(encoder, "att_context_size_all", ()):
        if (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            and len(value) == 2
            and int(value[1]) == right_context
        ):
            return int(value[0]), int(value[1])
    raise ValueError(f"Nemotron model does not support {chunk_ms} ms cache chunks")


def _first_hypothesis(best: object, transcriptions: object) -> object:
    for values in (best, transcriptions):
        if isinstance(values, (list, tuple)) and values:
            return values[0]
    return ""


def _transcription_result(
    text: str,
    *,
    start_sample: int,
    end_sample: int,
    language: str,
    reported_language: str | None,
    final: bool,
    started: float,
    backend: str,
) -> Mapping[str, Any]:
    pieces = tuple(text)
    words = []
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
            language if language in {"zh", "ja", "en"} else _reported_language(reported_language)
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
            "backend": backend,
            "inference_ms": round((time.perf_counter() - started) * 1000, 3),
            "final": final,
            "temporary_audio_retained": False,
        },
        "warnings": [],
    }


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
