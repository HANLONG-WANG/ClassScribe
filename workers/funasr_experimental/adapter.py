"""Explicitly gated offline Fun-ASR-Nano expert adapter."""

from __future__ import annotations

import asyncio
import gc
import re
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

_PRODUCTION_MODEL = "fun_asr_nano_2512"


class FunASRExperimentalAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "funasr_experimental",
            capabilities=("asr_zh", "asr_ja", "asr_en", "hotwords", "loop_detection"),
            supported_methods=("transcribe_batch",),
        )
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load" and params.get("model_id") == _PRODUCTION_MODEL:
            return await self._load_fun(params, cancelled)
        if method == "unload":
            self._model = None
            self._processor = None
            self._torch = None
            gc.collect()
            return await super().dispatch(method, params, cancelled)
        if method == "transcribe_batch" and self._model is not None:
            return await self._transcribe(params, cancelled)
        return await super().dispatch(method, params, cancelled)

    async def _load_fun(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or not (model_path / "config.json").is_file():
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "Fun-ASR model_path must be a complete non-symlink local snapshot",
            )

        def load_model() -> tuple[Any, Any, Any]:
            try:
                import torch  # type: ignore[import-not-found]
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForSpeechSeq2Seq,
                    AutoProcessor,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated Fun-ASR worker environment is incomplete",
                ) from exc
            device = str(params.get("device", "auto"))
            if device == "auto":
                device = "cuda:0" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
            processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                str(model_path),
                dtype=dtype,
                device_map=device,
                local_files_only=True,
            ).eval()
            return model, processor, torch

        try:
            model, processor, torch = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"Fun-ASR initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._model = model
        self._processor = processor
        self._torch = torch
        return {
            **result,
            "backend": "transformers_fun_asr_nano",
            "offline": True,
            "expert_only": True,
        }

    async def _transcribe(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if (
            params.get("experimental_enabled") is not True
            or params.get("candidate_role") != "course_expert"
        ):
            raise AdapterError(
                RPCErrorCode.INVALID_REQUEST,
                "Fun-ASR is an explicit expert-only candidate",
            )
        language = manual_language(params, frozenset({"zh", "ja", "en"}))
        decode = deterministic_decode(params)
        context = pronunciation_context(params)
        keywords = [str(item["canonical"]) for item in params.get("hints", ())]
        started = time.perf_counter()

        def infer() -> tuple[str, int]:
            assert self._model is not None
            assert self._processor is not None
            assert self._torch is not None
            with canonical_window(params, prefix="classscribe-funasr-") as clip:
                inputs = self._processor.apply_transcription_request(
                    audio=str(clip),
                    language=language,
                    prompt=context,
                    keywords=keywords,
                    return_tensors="pt",
                ).to(self._model.device)
                self._torch.manual_seed(decode.seed)
                with self._torch.inference_mode():
                    output_ids = self._model.generate(
                        **inputs,
                        max_new_tokens=decode.max_new_tokens,
                        do_sample=False,
                        num_beams=1,
                    )
                input_length = int(inputs["input_ids"].shape[1])
                new_ids = output_ids[:, input_length:]
                decoded = self._processor.batch_decode(new_ids, skip_special_tokens=True)
                if len(decoded) != 1:
                    raise ValueError("Fun-ASR must return exactly one result")
                return str(decoded[0]).strip(), int(new_ids.shape[-1])

        try:
            text, generated_tokens = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"Fun-ASR inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        text = bounded_text(text, decode)
        if _has_generation_loop(text):
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                "Fun-ASR output failed mandatory loop detection",
            )
        return response_payload(
            params,
            text=text,
            language=language,
            backend="transformers_fun_asr_nano",
            started=started,
            generated_tokens=generated_tokens,
            warnings=["experimental expert candidate; never eligible for automatic adoption"],
            extra_metrics={
                "mandatory_loop_detection": "passed",
                "context_length": len(context),
                "keyword_count": len(keywords),
            },
        )


def _has_generation_loop(text: str) -> bool:
    tokens = re.findall(r"\w+|[^\w\s]", text.casefold())
    for width in range(1, min(12, len(tokens) // 4) + 1):
        for start in range(0, len(tokens) - width * 4 + 1):
            unit = tokens[start : start + width]
            if all(tokens[start + width * n : start + width * (n + 1)] == unit for n in range(4)):
                return True
    return False


def create_adapter() -> FunASRExperimentalAdapter:
    return FunASRExperimentalAdapter()
