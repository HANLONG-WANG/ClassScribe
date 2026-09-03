"""Offline Granite Speech 4.1 2B body-ASR adapter."""

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

_PRODUCTION_MODEL = "granite_speech_4_1_2b"


class GraniteAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "granite",
            capabilities=("asr_ja", "asr_en", "punctuation", "hotwords"),
            supported_methods=("transcribe_batch",),
        )
        self._model: Any | None = None
        self._processor: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        self._librosa: Any | None = None
        self._device: Any | None = None

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load" and params.get("model_id") == _PRODUCTION_MODEL:
            return await self._load_granite(params, cancelled)
        if method == "unload":
            self._release()
            return await super().dispatch(method, params, cancelled)
        if method == "transcribe_batch" and self._model is not None:
            return await self._transcribe(params, cancelled)
        return await super().dispatch(method, params, cancelled)

    async def _load_granite(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or not (model_path / "config.json").is_file():
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "Granite model_path must be a complete non-symlink local snapshot",
            )

        def load_model() -> tuple[Any, Any, Any, Any, Any, Any]:
            try:
                import librosa  # type: ignore[import-not-found]
                import torch  # type: ignore[import-not-found]
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForSpeechSeq2Seq,
                    AutoProcessor,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated Granite worker environment is incomplete",
                ) from exc
            device_name = str(params.get("device", "auto"))
            if device_name == "auto":
                device_name = "cuda:0" if torch.cuda.is_available() else "cpu"
            device = torch.device(device_name)
            dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
            processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)
            model = (
                AutoModelForSpeechSeq2Seq.from_pretrained(
                    str(model_path), local_files_only=True, torch_dtype=dtype
                )
                .to(device)
                .eval()
            )
            return model, processor, processor.tokenizer, torch, librosa, device

        try:
            loaded = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"Granite initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        (
            self._model,
            self._processor,
            self._tokenizer,
            self._torch,
            self._librosa,
            self._device,
        ) = loaded
        return {**result, "backend": "transformers_granite_speech", "offline": True}

    async def _transcribe(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        language = manual_language(params, frozenset({"ja", "en"}))
        decode = deterministic_decode(params)
        context = pronunciation_context(params)
        prompt = _official_prompt(params)
        if context:
            prompt += " " + context
        started = time.perf_counter()

        def infer() -> tuple[str, int]:
            assert self._processor is not None
            assert self._tokenizer is not None
            assert self._model is not None
            assert self._torch is not None
            assert self._librosa is not None
            with canonical_window(params, prefix="classscribe-granite-") as clip:
                waveform, sample_rate = self._librosa.load(str(clip), sr=16_000, mono=True)
                if sample_rate != 16_000:
                    raise ValueError("Granite loader did not produce 16 kHz audio")
                chat = [{"role": "user", "content": f"<|audio|>{prompt}"}]
                rendered = self._tokenizer.apply_chat_template(
                    chat, tokenize=False, add_generation_prompt=True
                )
                inputs = self._processor(
                    rendered,
                    waveform,
                    device=self._device,
                    return_tensors="pt",
                ).to(self._device)
                self._torch.manual_seed(decode.seed)
                with self._torch.inference_mode():
                    outputs = self._model.generate(
                        **inputs,
                        max_new_tokens=decode.max_new_tokens,
                        do_sample=False,
                        num_beams=1,
                    )
                input_length = int(inputs["input_ids"].shape[-1])
                new_tokens = outputs[0, input_length:].unsqueeze(0)
                decoded = self._tokenizer.batch_decode(
                    new_tokens,
                    add_special_tokens=False,
                    skip_special_tokens=True,
                )
                if len(decoded) != 1:
                    raise ValueError("Granite must return exactly one result")
                return str(decoded[0]).strip(), int(new_tokens.shape[-1])

        try:
            text, generated_tokens = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"Granite inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        return response_payload(
            params,
            text=bounded_text(text, decode),
            language=language,
            backend="transformers_granite_speech",
            started=started,
            generated_tokens=generated_tokens,
            extra_metrics={
                "prompt": prompt,
                "prompt_language": "en",
                "language_forced_by_registry": True,
            },
        )

    def _release(self) -> None:
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._torch = None
        self._librosa = None
        self._device = None
        gc.collect()


def _official_prompt(params: Mapping[str, Any]) -> str:
    prompt = "transcribe the speech to text."
    hints = params.get("hints", ())
    if hints:
        terms = [f"{item['canonical']} ({item['reading']})" for item in hints]
        prompt += " Keywords: " + ", ".join(terms)
    return prompt


def create_adapter() -> GraniteAdapter:
    return GraniteAdapter()
