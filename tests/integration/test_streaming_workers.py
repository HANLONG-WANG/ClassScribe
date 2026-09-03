from __future__ import annotations

import asyncio
import builtins
import contextlib
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REVISION = "f" * 40


class _Cuda:
    @staticmethod
    def is_available() -> bool:
        return False

    @staticmethod
    def empty_cache() -> None:
        pass


class _Torch(types.ModuleType):
    cuda = _Cuda()
    bfloat16 = "bfloat16"
    float32 = "float32"
    int16 = "int16"
    long = "long"

    @staticmethod
    def frombuffer(value: bytearray, *, dtype: object) -> _Tensor:
        assert dtype == "int16"
        return _Tensor((len(value) // 2,))

    @staticmethod
    def tensor(value: list[int], **_kwargs: object) -> _Tensor:
        return _Tensor((len(value),), value=value)

    @staticmethod
    def inference_mode() -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()


class _Tensor:
    def __init__(self, shape: tuple[int, ...], *, value: object | None = None) -> None:
        self.shape = shape
        self.value = value

    def clone(self) -> _Tensor:
        return self

    def to(self, _device: object) -> _Tensor:
        return self

    def float(self) -> _Tensor:
        return self

    def div_(self, _value: builtins.float) -> _Tensor:
        return self

    def unsqueeze(self, dimension: int) -> _Tensor:
        assert dimension == 0
        return _Tensor((1, *self.shape), value=self.value)


def _wav(path: Path, samples: int = 32000) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\0\0" * samples)


def test_qwen_streaming_worker_emits_incremental_and_final_timed_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    module = types.ModuleType("qwen_asr")

    class Model:
        max_new_tokens = 2048

        @classmethod
        def from_pretrained(cls, _path: str, **kwargs: object) -> Model:
            captured["load"] = kwargs
            return cls()

        def transcribe(self, **kwargs: object) -> list[SimpleNamespace]:
            audio = Path(str(kwargs["audio"]))
            assert audio.is_file()
            captured["temporary"] = audio
            captured["transcribe"] = kwargs
            return [SimpleNamespace(text="日本語", language="Japanese")]

    module.__dict__["Qwen3ASRModel"] = Model
    monkeypatch.setitem(sys.modules, "torch", _Torch("torch"))
    monkeypatch.setitem(sys.modules, "qwen_asr", module)
    from workers.qwen.adapter import create_adapter

    model_path = tmp_path / "qwen"
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    adapter = create_adapter()

    async def scenario() -> None:
        cancelled = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "qwen3_asr_0_6b",
                "model_revision": REVISION,
                "model_path": str(model_path),
                "device": "cpu",
            },
            cancelled,
        )
        await adapter.dispatch(
            "stream_open",
            {"stream_id": "s", "sample_rate": 16000, "channels": 1, "chunk_ms": 560},
            cancelled,
        )
        interim = await adapter.dispatch(
            "stream_push",
            {
                "stream_id": "s",
                "pcm_s16le": b"\0\0" * 8960,
                "absolute_start_sample": 32000,
                "language": "ja",
            },
            cancelled,
        )
        assert interim["normalized_text"] == "日本語"
        words = interim["segments"][0]["words"]
        assert words[0]["start_sample"] == 32000
        assert words[-1]["end_sample"] == 40960
        final = await adapter.dispatch(
            "stream_flush",
            {"stream_id": "s", "language": "ja", "rolling_context": ["前文"]},
            cancelled,
        )
        assert final["candidates"] == ["日本語"]
        await adapter.dispatch("stream_close", {"stream_id": "s"}, cancelled)

    asyncio.run(scenario())
    assert not captured["temporary"].exists()
    assert captured["transcribe"]["language"] == "Japanese"


def test_nemotron_worker_uses_local_nemo_snapshot_for_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {"cache_calls": []}
    nemo = types.ModuleType("nemo")
    collections = types.ModuleType("nemo.collections")
    asr = types.ModuleType("nemo.collections.asr")
    models = types.ModuleType("nemo.collections.asr.models")
    parts = types.ModuleType("nemo.collections.asr.parts")
    utils = types.ModuleType("nemo.collections.asr.parts.utils")
    streaming_utils = types.ModuleType("nemo.collections.asr.parts.utils.streaming_utils")

    class FeatureBuffer:
        def __init__(self, **kwargs: object) -> None:
            captured["feature_buffer"] = kwargs

        def update_feature_buffer(self, samples: _Tensor) -> None:
            captured["feature_samples"] = samples.shape

        def get_normalized_feature_buffer(self) -> _Tensor:
            return _Tensor((80, 8))

    class Encoder:
        streaming_cfg = SimpleNamespace(pre_encode_cache_size=[2, 2], drop_extra_pre_encoded=3)

        def get_initial_cache_state(self, *, batch_size: int) -> tuple[str, str, str]:
            assert batch_size == 1
            return "channel-0", "time-0", "length-0"

    class Model:
        device = "cpu"
        cfg = SimpleNamespace(preprocessor=SimpleNamespace(window_stride=0.01))

        def __init__(self) -> None:
            self.encoder = Encoder()

        @classmethod
        def restore_from(cls, path: str, *, map_location: str) -> Model:
            captured["archive"] = path
            captured["device"] = map_location
            return cls()

        def freeze(self) -> None:
            pass

        def eval(self) -> Model:
            return self

        def to(self, device: str) -> Model:
            captured["to"] = device
            return self

        def set_inference_prompt(self, language: str) -> None:
            captured["prompt"] = language

        def conformer_stream_step(self, **kwargs: object) -> tuple[object, ...]:
            calls = captured["cache_calls"]
            assert isinstance(calls, list)
            calls.append(kwargs)
            number = len(calls)
            hypothesis = SimpleNamespace(text="hello", language="English")
            return (
                f"pred-{number}",
                [hypothesis],
                f"channel-{number}",
                f"time-{number}",
                f"length-{number}",
                [hypothesis],
            )

        def transcribe(self, paths: list[str], **kwargs: object) -> list[SimpleNamespace]:
            assert Path(paths[0]).is_file()
            captured["temporary"] = Path(paths[0])
            captured["decode"] = kwargs
            return [SimpleNamespace(text="hello")]

    models.__dict__["ASRModel"] = Model
    streaming_utils.__dict__["StreamingFeatureBufferer"] = FeatureBuffer
    monkeypatch.setitem(sys.modules, "torch", _Torch("torch"))
    monkeypatch.setitem(sys.modules, "nemo", nemo)
    monkeypatch.setitem(sys.modules, "nemo.collections", collections)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr", asr)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr.models", models)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr.parts", parts)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr.parts.utils", utils)
    monkeypatch.setitem(
        sys.modules, "nemo.collections.asr.parts.utils.streaming_utils", streaming_utils
    )
    from workers.nemotron.adapter import create_adapter

    model_path = tmp_path / "nemotron"
    model_path.mkdir()
    (model_path / "model.nemo").write_bytes(b"fixture")
    adapter = create_adapter()

    async def scenario() -> None:
        cancelled = asyncio.Event()
        loaded = await adapter.dispatch(
            "load",
            {
                "model_id": "nemotron_3_5_asr_streaming_0_6b",
                "model_revision": REVISION,
                "model_path": str(model_path),
                "device": "cpu",
            },
            cancelled,
        )
        assert loaded["backend"] == "nemo_cached_streaming"
        await adapter.dispatch(
            "stream_open",
            {"stream_id": "n", "sample_rate": 16000, "channels": 1, "chunk_ms": 80},
            cancelled,
        )
        interim = await adapter.dispatch(
            "stream_push",
            {
                "stream_id": "n",
                "pcm_s16le": b"\0\0" * 1280,
                "absolute_start_sample": 0,
                "language": "en",
            },
            cancelled,
        )
        assert interim["normalized_text"] == "hello"
        second = await adapter.dispatch(
            "stream_push",
            {
                "stream_id": "n",
                "pcm_s16le": b"\0\0" * 1280,
                "absolute_start_sample": 1280,
                "language": "en",
            },
            cancelled,
        )
        assert second["normalized_text"] == "hello"
        final = await adapter.dispatch(
            "stream_flush", {"stream_id": "n", "language": "en"}, cancelled
        )
        assert final["candidates"] == ["hello"]
        await adapter.dispatch("stream_close", {"stream_id": "n"}, cancelled)
        await adapter.dispatch("unload", {}, cancelled)

    asyncio.run(scenario())
    assert Path(captured["archive"]).name == "model.nemo"
    assert not captured["temporary"].exists()
    cache_calls = captured["cache_calls"]
    assert isinstance(cache_calls, list)
    assert cache_calls[0]["cache_last_channel"] == "channel-0"
    assert cache_calls[1]["cache_last_channel"] == "channel-1"
    assert cache_calls[1]["previous_pred_out"] == "pred-1"
    assert cache_calls[0]["drop_extra_pre_encoded"] == 0
    assert cache_calls[1]["drop_extra_pre_encoded"] == 3
    assert captured["prompt"] == "en-US"


def test_firered_worker_uses_official_batch_vad_lid_and_stream_vad_apis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vad_module = types.ModuleType("fireredasr2s.fireredvad")
    lid_module = types.ModuleType("fireredasr2s.fireredlid")
    numpy_module = types.ModuleType("numpy")
    numpy_module.__dict__["frombuffer"] = lambda value, dtype: memoryview(value).cast("h")
    numpy_module.__dict__["isscalar"] = lambda _value: True
    captured: dict[str, Any] = {"stream_frames": []}

    class Config:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class BatchVAD:
        @classmethod
        def from_pretrained(cls, path: str, config: Config) -> BatchVAD:
            captured["vad_path"] = path
            captured["vad_config"] = config.kwargs
            return cls()

        def detect(self, path: str) -> tuple[dict[str, Any], list[float]]:
            with wave.open(path, "rb") as reader:
                assert reader.getnframes() == 16000
            return {"timestamps": [(0.1, 0.9)]}, [0.8, 0.9]

    class StreamVAD:
        @classmethod
        def from_pretrained(cls, path: str, config: Config) -> StreamVAD:
            captured["stream_path"] = path
            captured["stream_config"] = config.kwargs
            return cls()

        def reset(self) -> None:
            captured["reset"] = int(captured.get("reset", 0)) + 1

        def detect_frame(self, frame: Any) -> SimpleNamespace:
            captured["stream_frames"].append(len(frame))
            return SimpleNamespace(is_speech=True, raw_prob=0.9, smoothed_prob=0.85)

    class LID:
        @classmethod
        def from_pretrained(cls, path: str, config: Config) -> LID:
            captured["lid_path"] = path
            captured["lid_config"] = config.kwargs
            return cls()

        def process(self, ids: list[str], paths: list[str]) -> list[dict[str, object]]:
            assert ids == ["segment"] and Path(paths[0]).is_file()
            return [{"lang": "ja", "confidence": 0.93}]

    vad_module.__dict__.update(
        FireRedVad=BatchVAD,
        FireRedVadConfig=Config,
        FireRedStreamVad=StreamVAD,
        FireRedStreamVadConfig=Config,
    )
    lid_module.__dict__.update(FireRedLid=LID, FireRedLidConfig=Config)
    monkeypatch.setitem(sys.modules, "fireredasr2s.fireredvad", vad_module)
    monkeypatch.setitem(sys.modules, "fireredasr2s.fireredlid", lid_module)
    monkeypatch.setitem(sys.modules, "numpy", numpy_module)
    from workers.firered.adapter import create_adapter

    vad_root = tmp_path / "vad"
    for name in ("VAD", "Stream-VAD"):
        directory = vad_root / name
        directory.mkdir(parents=True)
        (directory / "cmvn.ark").write_bytes(b"cmvn")
        (directory / "model.pth.tar").write_bytes(b"model")
    lid_root = tmp_path / "lid"
    lid_root.mkdir()
    for name in ("cmvn.ark", "model.pth.tar", "dict.txt"):
        (lid_root / name).write_bytes(b"fixture")
    audio = tmp_path / "audio.wav"
    _wav(audio)

    async def scenario() -> None:
        event = asyncio.Event()
        batch = create_adapter()
        await batch.dispatch(
            "load",
            {
                "model_id": "firered_vad",
                "model_revision": REVISION,
                "model_path": str(vad_root),
            },
            event,
        )
        vad = await batch.dispatch(
            "vad",
            {
                "audio_path": str(audio),
                "start_sample": 8000,
                "end_sample": 24000,
                "sample_rate": 16000,
            },
            event,
        )
        assert vad["segments"][0]["start_sample"] == 9600
        assert vad["segments"][0]["end_sample"] == 22400
        assert abs(vad["segments"][0]["vad_score"] - 0.85) < 1e-9

        lid = create_adapter()
        await lid.dispatch(
            "load",
            {
                "model_id": "firered_lid",
                "model_revision": REVISION,
                "model_path": str(lid_root),
            },
            event,
        )
        language = await lid.dispatch(
            "lid",
            {
                "audio_path": str(audio),
                "start_sample": 0,
                "end_sample": 16000,
                "sample_rate": 16000,
            },
            event,
        )
        assert language["language"] == "ja"
        assert language["language_probabilities"] == {"ja": 0.93}
        language_pcm = await lid.dispatch(
            "lid_pcm",
            {
                "pcm_s16le": b"\0\0" * 16000,
                "absolute_start_sample": 32000,
                "sample_rate": 16000,
            },
            event,
        )
        assert language_pcm["segments"][0]["start_sample"] == 32000
        assert language_pcm["segments"][0]["end_sample"] == 48000

        streaming = create_adapter()
        await streaming.dispatch(
            "load",
            {
                "model_id": "firered_vad",
                "model_revision": REVISION,
                "model_path": str(vad_root),
                "streaming": True,
            },
            event,
        )
        await streaming.dispatch(
            "stream_open",
            {"stream_id": "v", "sample_rate": 16000, "channels": 1},
            event,
        )
        first = await streaming.dispatch(
            "stream_push", {"stream_id": "v", "pcm_s16le": b"\0\0" * 320}, event
        )
        second = await streaming.dispatch(
            "stream_push", {"stream_id": "v", "pcm_s16le": b"\0\0" * 320}, event
        )
        assert first["frames_processed"] == 0
        assert second["voiced"] is True and second["frames_processed"] == 2
        assert captured["stream_frames"] == [400, 400]
        await streaming.dispatch("stream_close", {"stream_id": "v"}, event)

    asyncio.run(scenario())
