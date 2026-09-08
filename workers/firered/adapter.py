"""Offline FireRedASR2-AED and FireRedPunc adapter."""

from __future__ import annotations

import asyncio
import gc
import math
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
    response_payload,
)
from classscribe_protocol.messages import RPCErrorCode

_AED_MODEL = "firered_asr2_aed"
_PUNC_MODEL = "firered_punc"
_VAD_MODEL = "firered_vad"
_LID_MODEL = "firered_lid"


class FireRedAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "firered",
            capabilities=(
                "asr_zh",
                "asr_en",
                "word_timestamps",
                "confidence",
                "punctuation_zh",
                "punctuation_en",
                "vad",
                "lid",
                "streaming_vad",
            ),
            supported_methods=(
                "transcribe_batch",
                "punctuate",
                "vad",
                "lid",
                "lid_pcm",
                "stream_open",
                "stream_push",
                "stream_flush",
                "stream_close",
            ),
        )
        self._model: Any | None = None
        self._model_kind: str | None = None
        self._vad_offsets: dict[str, int] = {}

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load" and params.get("model_id") == _AED_MODEL:
            return await self._load_aed(params, cancelled)
        if method == "load" and params.get("model_id") == _PUNC_MODEL:
            return await self._load_punctuation(params, cancelled)
        if method == "load" and params.get("model_id") == _VAD_MODEL:
            return await self._load_vad(params, cancelled)
        if method == "load" and params.get("model_id") == _LID_MODEL:
            return await self._load_lid(params, cancelled)
        if method == "unload":
            self._model = None
            self._model_kind = None
            self._vad_offsets.clear()
            gc.collect()
            return await super().dispatch(method, params, cancelled)
        if method == "transcribe_batch" and self._model_kind == "aed":
            return await self._transcribe_aed(params, cancelled)
        if method == "punctuate" and self._model_kind == "punctuation":
            return await self._punctuate(params, cancelled)
        if method == "vad" and self._model_kind == "vad":
            return await self._vad(params, cancelled)
        if method == "lid" and self._model_kind == "lid":
            return await self._lid(params, cancelled)
        if method == "lid_pcm" and self._model_kind == "lid":
            return await self._lid_pcm(params, cancelled)
        if method == "stream_open" and self._model_kind == "stream_vad":
            result = await super().dispatch(method, params, cancelled)
            stream_id = str(params["stream_id"])
            self._vad_offsets[stream_id] = 0
            assert self._model is not None
            self._model.reset()
            return result
        if method == "stream_push" and self._model_kind == "stream_vad":
            return await self._stream_vad(params, cancelled)
        if method == "stream_close" and self._model_kind == "stream_vad":
            stream_id = str(params["stream_id"])
            result = await super().dispatch(method, params, cancelled)
            self._vad_offsets.pop(stream_id, None)
            assert self._model is not None
            self._model.reset()
            return result
        return await super().dispatch(method, params, cancelled)

    async def _load_vad(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_root = Path(str(params["model_path"]))
        streaming = bool(params.get("streaming", False))
        model_path = model_root / ("Stream-VAD" if streaming else "VAD")
        if not model_path.is_dir():
            model_path = model_root
        if model_path.is_symlink() or any(
            not (model_path / name).is_file() for name in ("cmvn.ark", "model.pth.tar")
        ):
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "FireRedVAD snapshot lacks cmvn.ark/model.pth.tar",
            )

        def load_model() -> Any:
            try:
                if streaming:
                    from fireredasr2s.fireredvad import (  # type: ignore[import-not-found]
                        FireRedStreamVad,
                        FireRedStreamVadConfig,
                    )

                    config = FireRedStreamVadConfig(
                        use_gpu=False,
                        smooth_window_size=5,
                        speech_threshold=0.4,
                        pad_start_frame=5,
                        min_speech_frame=8,
                        max_speech_frame=2000,
                        min_silence_frame=20,
                        chunk_max_frame=30000,
                    )
                    return FireRedStreamVad.from_pretrained(str(model_path), config)
                from fireredasr2s.fireredvad import (
                    FireRedVad,
                    FireRedVadConfig,
                )

                config = FireRedVadConfig(
                    use_gpu=False,
                    smooth_window_size=5,
                    speech_threshold=0.4,
                    min_speech_frame=20,
                    max_speech_frame=2000,
                    min_silence_frame=20,
                    merge_silence_frame=0,
                    extend_speech_frame=0,
                    chunk_max_frame=30000,
                )
                return FireRedVad.from_pretrained(str(model_path), config)
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated FireRed worker lacks the VAD runtime",
                ) from exc

        try:
            model = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"FireRedVAD initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._model_kind = "stream_vad" if streaming else "vad"
        return {**result, "backend": self._model_kind, "offline": True}

    async def _load_lid(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or any(
            not (model_path / name).is_file() for name in ("cmvn.ark", "model.pth.tar", "dict.txt")
        ):
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "FireRedLID snapshot lacks cmvn.ark/model.pth.tar/dict.txt",
            )

        def load_model() -> Any:
            try:
                from fireredasr2s.fireredlid import (  # type: ignore[import-not-found]
                    FireRedLid,
                    FireRedLidConfig,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated FireRed worker lacks the LID runtime",
                ) from exc
            return FireRedLid.from_pretrained(
                str(model_path), FireRedLidConfig(use_gpu=False, use_half=False)
            )

        try:
            model = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"FireRedLID initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._model_kind = "lid"
        return {**result, "backend": "fireredlid", "offline": True}

    async def _vad(self, params: Mapping[str, Any], cancelled: asyncio.Event) -> Mapping[str, Any]:
        started = time.perf_counter()

        def infer() -> tuple[Mapping[str, Any], Any]:
            with canonical_window(params, prefix="classscribe-firered-vad-") as clip:
                assert self._model is not None
                result, probabilities = self._model.detect(str(clip))
            if not isinstance(result, Mapping):
                raise TypeError("FireRedVAD result is not an object")
            return result, probabilities

        result, probabilities = await asyncio.to_thread(infer)
        if cancelled.is_set():
            raise asyncio.CancelledError
        offset = int(params["start_sample"])
        limit = int(params["end_sample"])
        timestamps = result.get("timestamps", ())
        if not isinstance(timestamps, (list, tuple)):
            raise AdapterError(RPCErrorCode.INTERNAL, "FireRedVAD timestamps are invalid")
        segments = []
        for value in timestamps:
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise AdapterError(RPCErrorCode.INTERNAL, "FireRedVAD span is invalid")
            start = max(offset, offset + round(float(value[0]) * SAMPLE_RATE))
            end = min(limit, offset + round(float(value[1]) * SAMPLE_RATE))
            if end > start:
                segments.append(
                    {
                        "start_sample": start,
                        "end_sample": end,
                        "vad_score": _mean_probability(probabilities),
                        "acoustic_class": "speech",
                        "source": "firered_vad",
                    }
                )
        return {
            "segments": segments,
            "metrics": {
                "backend": "firered_vad",
                "inference_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            "warnings": [],
        }

    async def _lid(self, params: Mapping[str, Any], cancelled: asyncio.Event) -> Mapping[str, Any]:
        started = time.perf_counter()

        def infer() -> Mapping[str, Any]:
            with canonical_window(params, prefix="classscribe-firered-lid-") as clip:
                assert self._model is not None
                results = self._model.process(["segment"], [str(clip)])
            if not isinstance(results, list) or len(results) != 1:
                raise TypeError("FireRedLID must return exactly one result")
            result = results[0]
            if not isinstance(result, Mapping):
                raise TypeError("FireRedLID result is not an object")
            return result

        result = await asyncio.to_thread(infer)
        if cancelled.is_set():
            raise asyncio.CancelledError
        label = result.get("lang")
        raw_confidence = result.get("confidence")
        if (
            not isinstance(label, str)
            or not label.strip()
            or isinstance(raw_confidence, bool)
            or not isinstance(raw_confidence, (int, float))
        ):
            raise AdapterError(
                RPCErrorCode.INTERNAL, "FireRedLID returned malformed classification"
            )
        raw_language = label.split(maxsplit=1)[0].lower()
        confidence = float(raw_confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise AdapterError(
                RPCErrorCode.INTERNAL, "FireRedLID confidence must be finite and within [0, 1]"
            )
        supported = raw_language in {"zh", "ja", "en"}
        # The multilingual classifier can legitimately return another language,
        # especially for noise. This is unknown routing evidence, not a worker crash.
        language = raw_language if supported else None
        return {
            "language": language,
            "segments": [
                {
                    "start_sample": int(params["start_sample"]),
                    "end_sample": int(params["end_sample"]),
                    "language": language,
                    "confidence_raw": confidence,
                }
            ],
            "language_probabilities": {raw_language: confidence}
            if supported
            else {code: 0.0 for code in ("zh", "ja", "en")},
            "raw_language": raw_language,
            "raw_confidence": confidence,
            "language_status": "classified" if supported else "unknown",
            "metrics": {
                "backend": "firered_lid",
                "inference_ms": round((time.perf_counter() - started) * 1000, 3),
            },
            "warnings": [] if supported else [f"unsupported_language: {raw_language}"],
        }

    async def _lid_pcm(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        pcm = params.get("pcm_s16le")
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise AdapterError(
                RPCErrorCode.INVALID_REQUEST,
                "lid_pcm requires non-empty whole PCM S16LE samples",
            )
        if len(pcm) > SAMPLE_RATE * 2 * 30:
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "lid_pcm is limited to 30 seconds")
        absolute_start = int(params.get("absolute_start_sample", 0))
        sample_count = len(pcm) // 2
        with tempfile.TemporaryDirectory(prefix="classscribe-firered-lid-pcm-") as temporary:
            clip = Path(temporary) / "segment.wav"
            with wave.open(str(clip), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(SAMPLE_RATE)
                writer.writeframes(pcm)
            result = await self._lid(
                {
                    "audio_path": str(clip),
                    "start_sample": 0,
                    "end_sample": sample_count,
                    "sample_rate": SAMPLE_RATE,
                },
                cancelled,
            )
        segments = result.get("segments", ())
        adjusted: list[dict[str, Any]] = []
        if isinstance(segments, list):
            for segment in segments:
                if isinstance(segment, Mapping):
                    adjusted.append(
                        {
                            **segment,
                            "start_sample": int(segment["start_sample"]) + absolute_start,
                            "end_sample": int(segment["end_sample"]) + absolute_start,
                        }
                    )
        return {**result, "segments": adjusted}

    async def _stream_vad(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        await super().dispatch("stream_push", params, cancelled)
        stream_id = str(params["stream_id"])
        pcm = self._streams[stream_id]
        offset = self._vad_offsets[stream_id]
        results = []

        def infer(frame: bytes) -> Any:
            try:
                import numpy as np  # type: ignore[import-not-found]
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.INTERNAL, "FireRed streaming VAD requires NumPy"
                ) from exc
            assert self._model is not None
            return self._model.detect_frame(np.frombuffer(frame, dtype="<i2"))

        while offset * 2 + 800 <= len(pcm):
            results.append(
                await asyncio.to_thread(infer, bytes(pcm[offset * 2 : offset * 2 + 800]))
            )
            offset += 160
        self._vad_offsets[stream_id] = offset
        if cancelled.is_set():
            raise asyncio.CancelledError
        latest = results[-1] if results else None
        return {
            "voiced": bool(latest.is_speech) if latest is not None else False,
            "raw_probability": float(latest.raw_prob) if latest is not None else 0.0,
            "smoothed_probability": (float(latest.smoothed_prob) if latest is not None else 0.0),
            "frames_processed": len(results),
        }

    async def _load_aed(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        required = ("model.pth.tar", "cmvn.ark", "dict.txt", "train_bpe1000.model")
        if model_path.is_symlink() or any(not (model_path / name).is_file() for name in required):
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "FireRed AED model_path is not a complete non-symlink local snapshot",
            )

        def load_model() -> Any:
            try:
                import torch  # type: ignore[import-not-found]
                from fireredasr2s.fireredasr2 import (  # type: ignore[import-not-found]
                    FireRedAsr2,
                    FireRedAsr2Config,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated FireRed worker environment is incomplete",
                ) from exc
            device = str(params.get("device", "auto"))
            use_gpu = device != "cpu" and bool(torch.cuda.is_available())
            config = FireRedAsr2Config(
                use_gpu=use_gpu,
                use_half=use_gpu,
                beam_size=3,
                nbest=1,
                decode_max_len=0,
                return_timestamp=True,
            )
            return FireRedAsr2.from_pretrained("aed", str(model_path), config)

        try:
            model = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"FireRed AED initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._model_kind = "aed"
        return {**result, "backend": "fireredasr2s_aed", "offline": True}

    async def _load_punctuation(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        required_files = (
            "config.yaml",
            "model.pth.tar",
            "chinese-bert-wwm-ext_vocab.txt",
            "out_dict",
        )
        encoder = model_path / "chinese-lert-base"
        if (
            model_path.is_symlink()
            or any(not (model_path / name).is_file() for name in required_files)
            or not encoder.is_dir()
            or encoder.is_symlink()
        ):
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "FireRedPunc model_path is not a complete non-symlink local snapshot",
            )

        def load_model() -> Any:
            try:
                import torch
                from fireredasr2s.fireredpunc.punc import (  # type: ignore[import-not-found]
                    FireRedPunc,
                    FireRedPuncConfig,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated FireRed worker environment is incomplete",
                ) from exc
            device = str(params.get("device", "auto"))
            config = FireRedPuncConfig(use_gpu=device != "cpu" and bool(torch.cuda.is_available()))
            return FireRedPunc.from_pretrained(str(model_path), config)

        try:
            model = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"FireRedPunc initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._model_kind = "punctuation"
        return {**result, "backend": "fireredpunc", "offline": True}

    async def _punctuate(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        text = params.get("text")
        language = params.get("language")
        if params.get("punctuation_contract") != "strict-punctuation-v1":
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "strict punctuation contract required")
        if not isinstance(text, str) or not text.strip():
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "punctuation text must not be empty")
        if params.get("manual_language") is not True or language not in {"zh", "en"}:
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "manual zh or en language required")
        started = time.perf_counter()

        def infer() -> Mapping[str, Any]:
            assert self._model is not None
            results = self._model.process([text])
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError("FireRedPunc must return exactly one result")
            result = results[0]
            if not isinstance(result, Mapping):
                raise TypeError("FireRedPunc result is not an object")
            return result

        try:
            native = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"FireRedPunc inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        origin = native.get("origin_text", text)
        punctuated = native.get("punc_text")
        if not isinstance(origin, str) or not isinstance(punctuated, str):
            raise AdapterError(RPCErrorCode.INTERNAL, "FireRedPunc returned invalid text")
        return {
            "raw_text": origin,
            "normalized_text": punctuated,
            "language": language,
            "metrics": {
                "backend": "fireredpunc",
                "inference_ms": round((time.perf_counter() - started) * 1000, 3),
                "input_characters": len(text),
            },
            "warnings": [],
        }

    async def _transcribe_aed(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        language = manual_language(params, frozenset({"zh", "en"}))
        decode = deterministic_decode(params)
        started = time.perf_counter()

        def infer() -> Mapping[str, Any]:
            with canonical_window(params, prefix="classscribe-firered-") as clip:
                assert self._model is not None
                results = self._model.transcribe(["segment"], [str(clip)])
                if not isinstance(results, list) or len(results) != 1:
                    raise ValueError("FireRed AED must return exactly one result")
                result = results[0]
                if not isinstance(result, Mapping):
                    raise TypeError("FireRed AED result is not an object")
                return result

        try:
            native = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"FireRed AED inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        text = bounded_text(native.get("text"), decode)
        confidence = _optional_float(native.get("confidence"))
        words = _native_words(native.get("timestamp"), int(params["start_sample"]), params)
        return response_payload(
            params,
            text=text,
            language=language,
            backend="fireredasr2s_aed",
            started=started,
            confidence_raw=confidence,
            words=words,
            extra_metrics={"native_rtf": str(native.get("rtf", ""))},
        )


def _native_words(value: Any, offset: int, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AdapterError(RPCErrorCode.INTERNAL, "FireRed timestamp output is invalid")
    end_limit = int(params["end_sample"])
    words: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            raise AdapterError(RPCErrorCode.INTERNAL, "FireRed word timestamp is invalid")
        start = max(offset, offset + round(float(item[1]) * SAMPLE_RATE))
        end = min(end_limit, offset + round(float(item[2]) * SAMPLE_RATE))
        if end > start:
            words.append(
                {
                    "start_sample": start,
                    "end_sample": end,
                    "text": str(item[0]),
                    "confidence_raw": None,
                }
            )
    return words


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _mean_probability(value: Any) -> float:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        flattened: list[float] = []
        stack = list(value)
        while stack:
            item = stack.pop()
            if isinstance(item, (list, tuple)):
                stack.extend(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                flattened.append(float(item))
        if flattened:
            return max(0.0, min(1.0, sum(flattened) / len(flattened)))
    return 0.0


def create_adapter() -> FireRedAdapter:
    return FireRedAdapter()
