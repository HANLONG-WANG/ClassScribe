"""Offline production adapter for MOSS-Transcribe-Diarize 0.9B."""

from __future__ import annotations

import asyncio
import gc
import re
import tempfile
import time
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from classscribe_protocol.adapter import AdapterError, StatefulAdapter
from classscribe_protocol.messages import RPCErrorCode

SAMPLE_RATE = 16_000
_EVENT_RE = re.compile(r"\[([^\]]{1,64})\]")
_SPEAKER_RE = re.compile(r"S\d{2,}", re.IGNORECASE)


class _GenerationCancelled(RuntimeError):
    pass


class MossTDAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "moss_td",
            capabilities=(
                "asr_zh",
                "asr_ja",
                "asr_en",
                "timestamps",
                "diarization",
                "acoustic_events",
                "hotwords",
            ),
            supported_methods=("transcribe_batch",),
        )
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None
        self._device: Any | None = None
        self._dtype: Any | None = None
        self._helpers: tuple[Any, Any, Any] | None = None

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load":
            return await self._load(params, cancelled)
        if method == "unload":
            self._release_model()
            return await super().dispatch(method, params, cancelled)
        if method == "transcribe_batch" and self._model is not None:
            return await self._transcribe(params, cancelled)
        return await super().dispatch(method, params, cancelled)

    async def _load(self, params: Mapping[str, Any], cancelled: asyncio.Event) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or not (model_path / "config.json").is_file():
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "MOSS model_path must be a complete non-symlink local snapshot",
            )

        def load_model() -> tuple[Any, Any, Any, Any, Any, tuple[Any, Any, Any]]:
            try:
                import torch  # type: ignore[import-not-found]
                from moss_transcribe_diarize import (  # type: ignore[import-not-found]
                    parse_transcript,
                )
                from moss_transcribe_diarize.inference_utils import (  # type: ignore[import-not-found]
                    build_transcription_messages,
                    generate_transcription,
                    resolve_device,
                )
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForCausalLM,
                    AutoProcessor,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated MOSS worker environment is incomplete",
                ) from exc
            device = resolve_device(str(params.get("device", "auto")))
            dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
            attention = str(params.get("attention", "sdpa"))
            model = (
                AutoModelForCausalLM.from_pretrained(
                    str(model_path),
                    trust_remote_code=True,
                    local_files_only=True,
                    dtype="auto",
                    attn_implementation=attention,
                )
                .to(dtype=dtype)
                .to(device)
                .eval()
            )
            processor = AutoProcessor.from_pretrained(
                str(model_path), trust_remote_code=True, local_files_only=True
            )
            return (
                model,
                processor,
                torch,
                device,
                dtype,
                (parse_transcript, build_transcription_messages, generate_transcription),
            )

        try:
            loaded = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"MOSS initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch(method="load", params=params, cancelled=cancelled)
        (
            self._model,
            self._processor,
            self._torch,
            self._device,
            self._dtype,
            self._helpers,
        ) = loaded
        return {
            **result,
            "backend": "transformers_moss_transcribe_diarize",
            "device": str(self._device),
            "attention": str(params.get("attention", "sdpa")),
            "offline": True,
        }

    async def _transcribe(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        start_sample = int(params["start_sample"])
        end_sample = int(params["end_sample"])
        if end_sample <= start_sample:
            raise AdapterError(RPCErrorCode.INVALID_REQUEST, "empty MOSS structure window")
        started = time.perf_counter()
        torch = self._torch
        device = self._device
        if torch is not None and device is not None and device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)

        def infer() -> tuple[str, list[Any], int, str]:
            if cancelled.is_set():
                raise _GenerationCancelled
            assert self._helpers is not None
            parse_transcript, build_messages, generate_transcription = self._helpers
            with tempfile.TemporaryDirectory(prefix="classscribe-moss-") as temporary:
                clip = Path(temporary) / "window.wav"
                _write_canonical_clip(
                    Path(str(params["audio_path"])), clip, start_sample, end_sample
                )
                prompt = _build_prompt(params)
                messages = build_messages(clip, prompt=prompt)

                def token_callback(_generated_tokens: int) -> None:
                    if cancelled.is_set():
                        raise _GenerationCancelled

                duration_seconds = (end_sample - start_sample) / SAMPLE_RATE
                decode_value = params.get("decode", {})
                decode = decode_value if isinstance(decode_value, Mapping) else {}
                max_new_tokens = int(
                    decode.get("max_new_tokens", max(2048, min(65_536, duration_seconds * 10)))
                )
                generated = generate_transcription(
                    self._model,
                    self._processor,
                    messages,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    temperature=0.0,
                    device=self._device,
                    dtype=self._dtype,
                    token_callback=token_callback,
                )
                text = str(generated["text"])
                return (
                    text,
                    list(parse_transcript(text)),
                    int(generated["generated_tokens"]),
                    prompt,
                )

        try:
            raw_text, native_segments, generated_tokens, prompt = await asyncio.to_thread(infer)
        except _GenerationCancelled as exc:
            raise asyncio.CancelledError from exc
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"MOSS inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        segments: list[dict[str, Any]] = []
        for native in native_segments:
            local_start = round(float(native.start) * SAMPLE_RATE)
            local_end = round(float(native.end) * SAMPLE_RATE)
            absolute_start = max(start_sample, start_sample + local_start)
            absolute_end = min(end_sample, start_sample + local_end)
            if absolute_end <= absolute_start:
                continue
            text = str(native.text)
            duration = absolute_end - absolute_start
            segments.append(
                {
                    "start_sample": absolute_start,
                    "end_sample": absolute_end,
                    "speaker_local": str(native.speaker),
                    "text": text,
                    "acoustic_events": _extract_acoustic_events(text),
                    "confidence_raw": None,
                    "structure_score": round(min(0.8, 0.5 + duration / (20 * SAMPLE_RATE)), 6),
                    "overlap": False,
                }
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        duration_seconds = (end_sample - start_sample) / SAMPLE_RATE
        peak_vram_mb = 0.0
        if torch is not None and device is not None and device.type == "cuda":
            peak_vram_mb = torch.cuda.max_memory_allocated(device) / (1024 * 1024)
        return {
            "language": str(params.get("language", "auto")),
            "raw_text": raw_text,
            "normalized_text": " ".join(raw_text.split()),
            "segments": segments,
            "metrics": {
                "inference_ms": round(elapsed_ms, 3),
                "peak_vram_mb": round(peak_vram_mb, 3),
                "rtf": round(elapsed_ms / 1000 / duration_seconds, 6),
                "generated_tokens": generated_tokens,
                "window_samples": end_sample - start_sample,
                "backend": "transformers_moss_transcribe_diarize",
            },
            "warnings": [],
            "prompt_kind": "speaker_timestamp_hotword_acoustic_event",
            "prompt_length": len(prompt),
            "text_role": "coarse_timeline_consensus_candidate_boundary_reference",
            "adopted_as_final": False,
        }

    def _release_model(self) -> None:
        self._model = None
        self._processor = None
        self._helpers = None
        torch = self._torch
        device = self._device
        self._torch = None
        self._device = None
        self._dtype = None
        gc.collect()
        if torch is not None and device is not None and device.type == "cuda":
            torch.cuda.empty_cache()


def _write_canonical_clip(source: Path, destination: Path, start: int, end: int) -> None:
    with wave.open(str(source), "rb") as reader:
        if (
            reader.getframerate() != SAMPLE_RATE
            or reader.getnchannels() != 1
            or reader.getsampwidth() != 2
        ):
            raise ValueError("MOSS worker requires canonical 16 kHz mono s16 WAV")
        if end > reader.getnframes():
            raise ValueError("requested MOSS window exceeds the canonical WAV")
        reader.setpos(start)
        frames = reader.readframes(end - start)
    with wave.open(str(destination), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(frames)


def _build_prompt(params: Mapping[str, Any]) -> str:
    base = (
        "请将音频转写为文本, 每一段需以起始时间戳和说话人编号"
        "([S01]、[S02]、[S03]…)开头, 正文为对应的语音内容, "
        "并在段末标注结束时间戳。保留可听见的掌声、笑声、音乐等声学事件。"
    )
    policy = params.get("speaker_policy", {})
    if isinstance(policy, Mapping):
        expected = policy.get("expected_speakers", "auto")
        minimum = policy.get("prior_min", 1)
        typical = policy.get("prior_typical", 2)
        maximum = policy.get("max_speakers", 12)
        base += (
            f"说话人数期望={expected}, 下限={minimum}, 通常={typical}, 上限={maximum};"
            "不要为了满足通常人数而合并证据不足的说话人。"
        )
    hotwords = params.get("hotwords", ())
    if isinstance(hotwords, (list, tuple)):
        normalized = [str(item).strip() for item in hotwords if str(item).strip()]
        if normalized:
            base += "热词提示:" + ", ".join(normalized)
    return base


def _extract_acoustic_events(text: str) -> list[str]:
    events: list[str] = []
    for match in _EVENT_RE.finditer(text):
        token = match.group(1).strip()
        if not token or _SPEAKER_RE.fullmatch(token):
            continue
        try:
            float(token)
        except ValueError:
            events.append(token)
    return events


def create_adapter() -> MossTDAdapter:
    return MossTDAdapter()
