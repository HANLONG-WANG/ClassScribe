"""MOSS English boundary plus the small real-model reference smoke adapter."""

from __future__ import annotations

import asyncio
import gc
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from classscribe_protocol.adapter import AdapterError, StatefulAdapter
from classscribe_protocol.batch_audio import (
    bounded_text,
    canonical_window,
    deterministic_decode,
    manual_language,
    pronunciation_context,
    response_payload,
)
from classscribe_protocol.messages import RPCErrorCode

_PRODUCTION_MODEL = "moss_transcribe_preview_2b"


class MossEnglishAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "moss_en",
            capabilities=("asr_en", "reference_asr_zh", "reference_asr_ja"),
            supported_methods=("transcribe_batch",),
        )
        self._reference_model: Any | None = None
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None
        self._librosa: Any | None = None
        self._device: Any | None = None

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load" and params.get("model_id") == _PRODUCTION_MODEL:
            return await self._load_preview(params, cancelled)
        if method == "load" and (Path(str(params["model_path"])) / "model.bin").is_file():
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]

                model_path = str(params["model_path"])
                model = await asyncio.to_thread(
                    WhisperModel,
                    model_path,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=2,
                    num_workers=1,
                    local_files_only=True,
                )
            except Exception as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    f"reference model initialization failed: {type(exc).__name__}: {exc}",
                ) from exc
            result = await super().dispatch(method, params, cancelled)
            self._reference_model = model
            return {**result, "backend": "faster_whisper_cpu_int8", "real_model": True}
        if method == "unload":
            self._reference_model = None
            self._model = None
            self._processor = None
            self._torch = None
            self._librosa = None
            self._device = None
            gc.collect()
            return await super().dispatch(method, params, cancelled)
        if method == "transcribe_batch" and self._model is not None:
            return await self._transcribe_preview(params, cancelled)
        if method == "transcribe_batch" and self._reference_model is not None:
            return await self._transcribe_reference(params, cancelled)
        return await super().dispatch(method, params, cancelled)

    async def _load_preview(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        required = ("config.json", "chat_template_default.py")
        if model_path.is_symlink() or any(not (model_path / name).is_file() for name in required):
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "MOSS preview model_path is not a complete non-symlink local snapshot",
            )

        def load_model() -> tuple[Any, Any, Any, Any, Any]:
            try:
                import librosa  # type: ignore[import-not-found]
                import torch  # type: ignore[import-not-found]
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForCausalLM,
                    AutoTokenizer,
                )
                from transformers.dynamic_module_utils import (  # type: ignore[import-not-found]
                    get_class_from_dynamic_module,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated MOSS English worker environment is incomplete",
                ) from exc
            device_name = str(params.get("device", "auto"))
            if device_name == "auto":
                device_name = "cuda:0" if torch.cuda.is_available() else "cpu"
            device = torch.device(device_name)
            dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
            model = (
                AutoModelForCausalLM.from_pretrained(
                    str(model_path),
                    dtype=dtype,
                    trust_remote_code=True,
                    local_files_only=True,
                )
                .to(device)
                .eval()
            )
            tokenizer = AutoTokenizer.from_pretrained(
                str(model_path), trust_remote_code=True, local_files_only=True
            )
            processor_class = get_class_from_dynamic_module(
                "processing_Moss.MossProcessor", str(model_path), local_files_only=True
            )
            mel_class = get_class_from_dynamic_module(
                "processing_Moss.MelConfig", str(model_path), local_files_only=True
            )
            mel_config = mel_class(
                mel_sr=16_000,
                mel_dim=128,
                mel_n_fft=400,
                mel_hop_length=160,
            )
            processor = processor_class(tokenizer, config=mel_config, enable_time_marker=False)
            processor.load_template(str(model_path / "chat_template_default.py"))
            return model, processor, torch, librosa, device

        try:
            loaded = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"MOSS preview initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model, self._processor, self._torch, self._librosa, self._device = loaded
        return {**result, "backend": "transformers_moss_preview", "offline": True}

    async def _transcribe_preview(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        language = manual_language(params, frozenset({"en"}))
        decode = deterministic_decode(params)
        context = pronunciation_context(params)
        started = time.perf_counter()

        def infer() -> tuple[str, int]:
            assert self._model is not None
            assert self._processor is not None
            assert self._torch is not None
            assert self._librosa is not None
            with canonical_window(params, prefix="classscribe-moss-en-") as clip:
                waveform, sample_rate = self._librosa.load(str(clip), sr=16_000, mono=True)
                if sample_rate != 16_000:
                    raise ValueError("MOSS loader did not produce 16 kHz audio")
                inputs = self._processor(audio=waveform, return_tensors="pt").to(self._device)
                inputs["audio_data"] = inputs["audio_data"].to(self._model.dtype)
                self._torch.manual_seed(decode.seed)
                with self._torch.no_grad():
                    output_ids = self._model.generate(
                        **inputs,
                        max_new_tokens=decode.max_new_tokens,
                        do_sample=False,
                        num_beams=1,
                        use_cache=True,
                        eos_token_id=[self._processor.end_token_id],
                    )
                input_length = int(inputs["input_ids"].shape[1])
                new_ids = output_ids[:, input_length:]
                decoded = self._processor.batch_decode(new_ids, skip_special_tokens=True)
                if len(decoded) != 1:
                    raise ValueError("MOSS preview must return exactly one result")
                return str(decoded[0]).strip(), int(new_ids.shape[-1])

        try:
            text, generated_tokens = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"MOSS preview inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        warnings = []
        if context:
            warnings.append(
                "MOSS preview has no supported terminology-bias channel; hints were not sent"
            )
        return response_payload(
            params,
            text=bounded_text(text, decode),
            language=language,
            backend="transformers_moss_preview",
            started=started,
            generated_tokens=generated_tokens,
            warnings=warnings,
            extra_metrics={"language_forced_by_registry": True},
        )

    async def _transcribe_reference(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        start_sample = int(params["start_sample"])
        end_sample = int(params["end_sample"])
        language_value = str(params.get("language", "auto"))
        language = None if language_value == "auto" else language_value
        hotwords_value = params.get("hotwords", ())
        hotwords = " ".join(str(item) for item in hotwords_value) or None
        started = time.perf_counter()

        def infer() -> tuple[list[Any], Any]:
            assert self._reference_model is not None
            segments, info = self._reference_model.transcribe(
                str(params["audio_path"]),
                language=language,
                task="transcribe",
                beam_size=1,
                temperature=0.0,
                word_timestamps=True,
                condition_on_previous_text=False,
                clip_timestamps=[start_sample / 16_000, end_sample / 16_000],
                hotwords=hotwords,
            )
            return list(segments), info

        native_segments, info = await asyncio.to_thread(infer)
        if cancelled.is_set():
            raise asyncio.CancelledError
        output: list[dict[str, Any]] = []
        for segment in native_segments:
            segment_start = max(start_sample, round(float(segment.start) * 16_000))
            segment_end = min(end_sample, round(float(segment.end) * 16_000))
            if segment_end <= segment_start:
                continue
            words = []
            for word in segment.words or ():
                word_start = max(start_sample, round(float(word.start) * 16_000))
                word_end = min(end_sample, round(float(word.end) * 16_000))
                if word_end > word_start:
                    words.append(
                        {
                            "start_sample": word_start,
                            "end_sample": word_end,
                            "text": str(word.word),
                            "confidence_raw": float(word.probability),
                        }
                    )
            output.append(
                {
                    "start_sample": segment_start,
                    "end_sample": segment_end,
                    "text": str(segment.text),
                    "confidence_raw": float(segment.avg_logprob),
                    "words": words,
                }
            )
        raw_text = "".join(str(item.text) for item in native_segments)
        normalized_text = " ".join(raw_text.split())
        elapsed_ms = (time.perf_counter() - started) * 1000
        duration_seconds = (end_sample - start_sample) / 16_000
        return {
            "language": str(info.language),
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "segments": output,
            "metrics": {
                "inference_ms": round(elapsed_ms, 3),
                "peak_vram_mb": 0,
                "rtf": round(elapsed_ms / 1000 / duration_seconds, 6),
                "backend": "faster_whisper_cpu_int8",
                "language_probability_raw": float(info.language_probability),
            },
            "warnings": ["reference smoke model; never used by production auto-best"],
        }


def create_adapter() -> MossEnglishAdapter:
    return MossEnglishAdapter()
