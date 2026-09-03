from __future__ import annotations

import asyncio
import importlib
import sys
import types
import wave
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast


class _Device:
    type = "cpu"

    def __str__(self) -> str:
        return "cpu"


class _FakeModel:
    def to(self, *_args: object, **_kwargs: object) -> _FakeModel:
        return self

    def eval(self) -> _FakeModel:
        return self


class _AutoFactory:
    @classmethod
    def from_pretrained(cls, *_args: object, **_kwargs: object) -> _FakeModel:
        return _FakeModel()


class _FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return False

    @staticmethod
    def empty_cache() -> None:
        return None


class _FakeTorch(types.ModuleType):
    bfloat16 = "bf16"
    float32 = "fp32"
    cuda = _FakeCuda()

    def device(self, _name: str) -> _Device:
        return _Device()

    def from_numpy(self, value: Any) -> _FakeTensor:
        return _FakeTensor((1, value.shape[0]))


class _FakeTensor:
    def __init__(self, shape: tuple[int, int]) -> None:
        self.shape = shape


class _FakeAudioArray:
    def __init__(self, frames: int) -> None:
        self.shape = (frames, 1)

    @property
    def T(self) -> _FakeAudioArray:
        return self

    def copy(self) -> _FakeAudioArray:
        return self


class _Turn:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _Annotation:
    def __init__(self, rows: list[tuple[float, float, str]]) -> None:
        self.rows = rows

    def itertracks(self, *, yield_label: bool) -> Any:
        assert yield_label is True
        for start, end, label in self.rows:
            yield _Turn(start, end), "track", label

    def labels(self) -> list[str]:
        return sorted({label for _start, _end, label in self.rows})


class _Vector:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return self.values


class _FakePyannotePipeline:
    embedding_exclude_overlap = False

    @classmethod
    def from_pretrained(cls, _path: str) -> _FakePyannotePipeline:
        return cls()

    def to(self, _device: _Device) -> None:
        return None

    def __call__(self, _audio: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["return_embeddings"] is True
        regular = _Annotation([(0.0, 3.0, "P0"), (2.5, 4.0, "P1")])
        exclusive = _Annotation([(0.0, 2.75, "P0"), (2.75, 4.0, "P1")])
        return SimpleNamespace(
            speaker_diarization=regular,
            exclusive_speaker_diarization=exclusive,
            speaker_embeddings=[_Vector([1.0, 0.0]), _Vector([0.0, 1.0])],
        )


def _canonical_wav(path: Path, samples: int = 64_000) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * samples)


def test_moss_production_adapter_loads_local_model_and_offsets_window_samples(
    tmp_path: Path, monkeypatch: Any
) -> None:
    moss_package = types.ModuleType("moss_transcribe_diarize")
    moss_package.__dict__["parse_transcript"] = lambda _text: [
        SimpleNamespace(start=0.1, end=1.0, speaker="S01", text="hello [applause]")
    ]
    helpers = types.ModuleType("moss_transcribe_diarize.inference_utils")
    captured: dict[str, object] = {}

    def build_messages(path: Path, *, prompt: str) -> list[dict[str, object]]:
        with wave.open(str(path), "rb") as clip:
            captured["clip_samples"] = clip.getnframes()
        captured["prompt"] = prompt
        return [{"path": str(path)}]

    def generate(*_args: object, **kwargs: object) -> dict[str, object]:
        cast(Callable[[int], None], kwargs["token_callback"])(10)
        return {"text": "[0.1][S01]hello [applause][1.0]", "generated_tokens": 10}

    helpers.__dict__["build_transcription_messages"] = build_messages
    helpers.__dict__["generate_transcription"] = generate
    helpers.__dict__["resolve_device"] = lambda _name: _Device()
    transformers = types.ModuleType("transformers")
    transformers.__dict__["AutoModelForCausalLM"] = _AutoFactory
    transformers.__dict__["AutoProcessor"] = _AutoFactory
    torch = _FakeTorch("torch")
    monkeypatch.setitem(sys.modules, "moss_transcribe_diarize", moss_package)
    monkeypatch.setitem(sys.modules, "moss_transcribe_diarize.inference_utils", helpers)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "torch", torch)
    module = importlib.import_module("workers.moss_td.adapter")
    adapter = module.create_adapter()
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    audio = tmp_path / "audio.wav"
    _canonical_wav(audio)

    async def exercise() -> dict[str, Any]:
        cancelled = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "moss_td_0_9b",
                "model_revision": "a" * 40,
                "model_path": str(model_path),
            },
            cancelled,
        )
        result = dict(
            await adapter.dispatch(
                "transcribe_batch",
                {
                    "audio_path": str(audio),
                    "start_sample": 16_000,
                    "end_sample": 48_000,
                    "sample_rate": 16_000,
                    "language": "en",
                    "hotwords": ["ClassScribe"],
                    "speaker_policy": {
                        "expected_speakers": "auto",
                        "prior_min": 1,
                        "prior_typical": 2,
                        "max_speakers": 12,
                    },
                },
                cancelled,
            )
        )
        await adapter.dispatch("unload", {}, cancelled)
        return result

    result = asyncio.run(exercise())
    assert captured["clip_samples"] == 32_000
    assert "ClassScribe" in str(captured["prompt"])
    assert result["segments"][0]["start_sample"] == 17_600
    assert result["segments"][0]["end_sample"] == 32_000
    assert result["segments"][0]["acoustic_events"] == ["applause"]
    assert result["adopted_as_final"] is False


def test_pyannote_production_adapter_emits_regular_exclusive_overlap_inputs_and_embeddings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    pyannote = types.ModuleType("pyannote")
    pyannote_audio = types.ModuleType("pyannote.audio")
    pyannote_audio.__dict__["Pipeline"] = _FakePyannotePipeline
    soundfile = types.ModuleType("soundfile")
    soundfile.__dict__["read"] = lambda *_args, **_kwargs: (
        _FakeAudioArray(64_000),
        16_000,
    )
    torch = _FakeTorch("torch")
    monkeypatch.setitem(sys.modules, "pyannote", pyannote)
    monkeypatch.setitem(sys.modules, "pyannote.audio", pyannote_audio)
    monkeypatch.setitem(sys.modules, "soundfile", soundfile)
    monkeypatch.setitem(sys.modules, "torch", torch)
    module = importlib.import_module("workers.pyannote.adapter")
    adapter = module.create_adapter()
    model_path = tmp_path / "pyannote-model"
    model_path.mkdir()
    (model_path / "config.yaml").write_text("pipeline: test", encoding="utf-8")

    async def exercise() -> dict[str, Any]:
        cancelled = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "pyannote_community_1",
                "model_revision": "b" * 40,
                "model_path": str(model_path),
            },
            cancelled,
        )
        result = dict(
            await adapter.dispatch(
                "diarize",
                {
                    "audio_path": str(tmp_path / "audio.wav"),
                    "start_sample": 32_000,
                    "end_sample": 96_000,
                    "sample_rate": 16_000,
                    "min_speakers": 1,
                    "max_speakers": 12,
                },
                cancelled,
            )
        )
        await adapter.dispatch("unload", {}, cancelled)
        return result

    result = asyncio.run(exercise())
    assert result["segments"][0]["start_sample"] == 32_000
    assert result["exclusive_segments"][1]["start_sample"] == 76_000
    assert {item["speaker_local"] for item in result["embeddings"]} == {"P0", "P1"}
    assert all(
        item["end_sample"] <= 72_000 or item["start_sample"] >= 80_000
        for item in result["embeddings"]
    )
    assert result["metrics"]["regular_span_count"] == 2
    assert result["metrics"]["exclusive_span_count"] == 2
