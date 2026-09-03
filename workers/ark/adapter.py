"""Offline ARK-ASR-3B multilingual review adapter."""

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

_PRODUCTION_MODEL = "ark_asr_3b"
_LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "en": "English"}


class ArkAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "ark",
            capabilities=("asr_zh", "asr_ja", "asr_en"),
            supported_methods=("transcribe_batch",),
        )
        self._model: Any | None = None
        self._processor: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        self._device: Any | None = None
        self._dtype: Any | None = None

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load" and params.get("model_id") == _PRODUCTION_MODEL:
            return await self._load_ark(params, cancelled)
        if method == "unload":
            self._release()
            return await super().dispatch(method, params, cancelled)
        if method == "transcribe_batch" and self._model is not None:
            return await self._transcribe(params, cancelled)
        return await super().dispatch(method, params, cancelled)

    async def _load_ark(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or not (model_path / "config.json").is_file():
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "ARK model_path must be a complete non-symlink local snapshot",
            )

        def load_model() -> tuple[Any, Any, Any, Any, Any, Any]:
            try:
                import torch  # type: ignore[import-not-found]
                from transformers import (  # type: ignore[import-not-found]
                    AutoModelForCausalLM,
                    AutoProcessor,
                    AutoTokenizer,
                )
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated ARK worker environment is incomplete",
                ) from exc
            device_name = str(params.get("device", "auto"))
            if device_name == "auto":
                device_name = "cuda:0" if torch.cuda.is_available() else "cpu"
            device = torch.device(device_name)
            dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
            processor = AutoProcessor.from_pretrained(
                str(model_path), trust_remote_code=True, local_files_only=True
            )
            tokenizer = AutoTokenizer.from_pretrained(
                str(model_path), trust_remote_code=True, local_files_only=True
            )
            model = (
                AutoModelForCausalLM.from_pretrained(
                    str(model_path),
                    trust_remote_code=True,
                    local_files_only=True,
                    torch_dtype=dtype,
                    attn_implementation="sdpa",
                )
                .to(device)
                .eval()
            )
            return model, processor, tokenizer, torch, device, dtype

        try:
            loaded = await asyncio.to_thread(load_model)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"ARK initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        (
            self._model,
            self._processor,
            self._tokenizer,
            self._torch,
            self._device,
            self._dtype,
        ) = loaded
        return {**result, "backend": "transformers_arkasr", "offline": True}

    async def _transcribe(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        language = manual_language(params, frozenset(_LANGUAGE_NAMES))
        decode = deterministic_decode(params)
        context = pronunciation_context(params)
        started = time.perf_counter()

        def infer() -> tuple[str, int, str]:
            assert self._processor is not None
            assert self._tokenizer is not None
            assert self._model is not None
            assert self._torch is not None
            prompt = (
                f"Please transcribe this audio in its spoken {_LANGUAGE_NAMES[language]} "
                "language without translating it."
            )
            if context:
                prompt += " " + context
            with canonical_window(params, prefix="classscribe-ark-") as clip:
                conversation = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "audio", "path": str(clip)},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ]
                inputs = self._processor.apply_chat_template(
                    conversation,
                    add_generation_prompt=True,
                    return_tensors="pt",
                    sampling_rate=16_000,
                    audio_padding="longest",
                    text_kwargs={"padding": "longest"},
                    audio_max_length=int(params["end_sample"]) - int(params["start_sample"]),
                )
                inputs = inputs.to(self._device)
                if "audios" in inputs:
                    inputs["audios"] = inputs["audios"].to(dtype=self._dtype)
                self._torch.manual_seed(decode.seed)
                bad_words_ids = _bad_words_ids(self._tokenizer)
                with self._torch.inference_mode():
                    outputs = self._model.generate(
                        **inputs,
                        do_sample=False,
                        max_new_tokens=decode.max_new_tokens,
                        pad_token_id=self._tokenizer.pad_token_id,
                        eos_token_id=self._tokenizer.eos_token_id,
                        bad_words_ids=bad_words_ids,
                    )
                input_length = int(inputs["input_ids"].shape[1])
                new_tokens = outputs[:, input_length:]
                decoded = self._tokenizer.batch_decode(new_tokens, skip_special_tokens=True)
                if len(decoded) != 1:
                    raise ValueError("ARK must return exactly one result")
                return str(decoded[0]).strip(), int(new_tokens.shape[-1]), prompt

        try:
            text, generated_tokens, prompt = await asyncio.to_thread(infer)
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"ARK inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        return response_payload(
            params,
            text=bounded_text(text, decode),
            language=language,
            backend="transformers_arkasr",
            started=started,
            generated_tokens=generated_tokens,
            extra_metrics={"prompt": prompt, "language_forced": True},
            warnings=["ARK script-preservation QA is required before adoption"],
        )

    def _release(self) -> None:
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._torch = None
        self._device = None
        self._dtype = None
        gc.collect()


def _bad_words_ids(tokenizer: Any) -> list[list[int]]:
    eos = tokenizer.eos_token_id
    keep = {eos} if isinstance(eos, int) else set(eos or ())
    blocked = set(tokenizer.all_special_ids) - keep
    blocked.update(
        token_id
        for token, token_id in tokenizer.get_added_vocab().items()
        if token.startswith("<") and token.endswith(">") and token_id not in keep
    )
    return [[int(token_id)] for token_id in sorted(blocked)]


def create_adapter() -> ArkAdapter:
    return ArkAdapter()
