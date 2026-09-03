from __future__ import annotations

import asyncio
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REVISION = "e" * 40


class _Cuda:
    @staticmethod
    def is_available() -> bool:
        return False


class _Torch(types.ModuleType):
    cuda = _Cuda()
    float32 = "float32"
    bfloat16 = "bfloat16"


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\0\0" * 64_000)


def test_firered_punctuation_uses_official_process_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    package = types.ModuleType("fireredasr2s")
    subpackage = types.ModuleType("fireredasr2s.fireredpunc")
    module = types.ModuleType("fireredasr2s.fireredpunc.punc")

    class Config:
        def __init__(self, **kwargs: object) -> None:
            captured["config"] = kwargs

    class Model:
        @classmethod
        def from_pretrained(cls, path: str, _config: Config) -> Model:
            captured["path"] = path
            return cls()

        def process(self, values: list[str]) -> list[dict[str, str]]:
            assert values == ["今天学习CPU"]
            return [{"origin_text": values[0], "punc_text": "今天学习CPU。"}]

    module.__dict__.update(FireRedPunc=Model, FireRedPuncConfig=Config)
    monkeypatch.setitem(sys.modules, "torch", _Torch("torch"))
    monkeypatch.setitem(sys.modules, "fireredasr2s", package)
    monkeypatch.setitem(sys.modules, "fireredasr2s.fireredpunc", subpackage)
    monkeypatch.setitem(sys.modules, "fireredasr2s.fireredpunc.punc", module)
    from workers.firered.adapter import create_adapter

    model_path = tmp_path / "punc"
    model_path.mkdir()
    for name in ("config.yaml", "model.pth.tar", "chinese-bert-wwm-ext_vocab.txt", "out_dict"):
        (model_path / name).write_text("fixture", encoding="utf-8")
    (model_path / "chinese-lert-base").mkdir()
    adapter = create_adapter()

    async def exercise() -> dict[str, Any]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "firered_punc",
                "model_revision": REVISION,
                "model_path": str(model_path),
                "device": "cpu",
            },
            event,
        )
        return dict(
            await adapter.dispatch(
                "punctuate",
                {
                    "text": "今天学习CPU",
                    "language": "zh",
                    "manual_language": True,
                    "punctuation_contract": "strict-punctuation-v1",
                },
                event,
            )
        )

    result = asyncio.run(exercise())
    assert result["raw_text"] == "今天学习CPU"
    assert result["normalized_text"] == "今天学习CPU。"
    assert captured["config"] == {"use_gpu": False}


def test_qwen_forced_aligner_uses_canonical_crop_and_absolute_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    qwen = types.ModuleType("qwen_asr")

    class Model:
        @classmethod
        def from_pretrained(cls, _path: str, **kwargs: object) -> Model:
            captured["load"] = kwargs
            return cls()

        def align(self, **kwargs: object) -> list[list[SimpleNamespace]]:
            captured["align"] = kwargs
            with wave.open(str(kwargs["audio"]), "rb") as reader:
                captured["samples"] = reader.getnframes()
            return [
                [
                    SimpleNamespace(text="one", start_time=0.0, end_time=1.0),
                    SimpleNamespace(text="two", start_time=1.0, end_time=2.0),
                ]
            ]

    qwen.__dict__["Qwen3ForcedAligner"] = Model
    monkeypatch.setitem(sys.modules, "torch", _Torch("torch"))
    monkeypatch.setitem(sys.modules, "qwen_asr", qwen)
    from workers.qwen.adapter import create_adapter

    model_path = tmp_path / "aligner"
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    audio = tmp_path / "audio.wav"
    _wav(audio)
    adapter = create_adapter()

    async def exercise() -> dict[str, Any]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "qwen3_forced_aligner_0_6b",
                "model_revision": REVISION,
                "model_path": str(model_path),
                "device": "cpu",
            },
            event,
        )
        return dict(
            await adapter.dispatch(
                "align",
                {
                    "audio_path": str(audio),
                    "start_sample": 16_000,
                    "end_sample": 48_000,
                    "sample_rate": 16_000,
                    "text": "one two",
                    "language": "en",
                    "manual_language": True,
                    "alignment_contract": "final-align-v1",
                    "safe_max_seconds": 30,
                    "quality_gate": {
                        "no_decode_loop": True,
                        "no_missing_text": True,
                        "normal_character_rate": True,
                        "language_matches": True,
                        "coverage_ratio": 0.9,
                        "voiced_seconds": 2,
                    },
                },
                event,
            )
        )

    result = asyncio.run(exercise())
    assert captured["samples"] == 32_000
    assert captured["align"]["language"] == "English"
    assert result["raw_text"] == result["normalized_text"] == "one two"
    assert result["segments"][0]["words"][0]["start_sample"] == 16_000
    assert result["segments"][0]["words"][1]["end_sample"] == 48_000
