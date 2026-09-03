from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import types
import wave
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from classscribe_protocol.adapter import AdapterError
from classscribe_protocol.batch_audio import deterministic_decode

REVISION = "a" * 40


class _Device:
    def __init__(self, name: str = "cpu") -> None:
        self.type = "cuda" if name.startswith("cuda") else "cpu"

    def __str__(self) -> str:
        return self.type


class _Cuda:
    @staticmethod
    def is_available() -> bool:
        return False


class _Context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> None:
        return None


class _Tensor:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape

    def to(self, *_args: object, **_kwargs: object) -> _Tensor:
        return self

    def unsqueeze(self, dimension: int) -> _Tensor:
        shape = list(self.shape)
        shape.insert(dimension, 1)
        return _Tensor(tuple(shape))

    def __getitem__(self, key: object) -> _Tensor:
        if not isinstance(key, tuple) or len(key) != 2 or not isinstance(key[1], slice):
            raise AssertionError(f"unsupported fake tensor index: {key!r}")
        start = int(key[1].start or 0)
        remaining = max(0, self.shape[-1] - start)
        return _Tensor((remaining,) if isinstance(key[0], int) else (self.shape[0], remaining))


class _Inputs(dict[str, _Tensor]):
    def to(self, *_args: object, **_kwargs: object) -> _Inputs:
        return self

    @property
    def input_ids(self) -> _Tensor:
        return self["input_ids"]


class _Torch(types.ModuleType):
    bfloat16 = "bfloat16"
    float32 = "float32"
    cuda = _Cuda()

    def device(self, name: str) -> _Device:
        return _Device(name)

    def manual_seed(self, value: int) -> None:
        self.__dict__["seed"] = value

    def inference_mode(self) -> _Context:
        return _Context()

    def no_grad(self) -> _Context:
        return _Context()


class _Tokenizer:
    eos_token_id = 2
    pad_token_id = 0
    all_special_ids = (0, 1, 2, 3)

    def __init__(self, captured: dict[str, Any]) -> None:
        self.captured = captured

    def get_added_vocab(self) -> dict[str, int]:
        return {"<audio>": 3, "ordinary": 9}

    def apply_chat_template(self, chat: object, **kwargs: object) -> str:
        self.captured["tokenizer_chat"] = chat
        self.captured["tokenizer_chat_kwargs"] = kwargs
        return "rendered chat"

    def batch_decode(self, _tokens: _Tensor, **_kwargs: object) -> list[str]:
        return [str(self.captured["decoded_text"])]


class _Processor:
    end_token_id = 2

    def __init__(self, captured: dict[str, Any]) -> None:
        self.captured = captured
        self.tokenizer = _Tokenizer(captured)

    def apply_chat_template(self, conversation: object, **kwargs: object) -> _Inputs:
        self.captured["processor_chat"] = conversation
        self.captured["processor_chat_kwargs"] = kwargs
        messages = cast(list[dict[str, Any]], conversation)
        content = messages[0]["content"]
        _capture_clip(Path(str(content[0]["path"])), self.captured)
        return _Inputs(input_ids=_Tensor((1, 5)), audios=_Tensor((1, 32_000)))

    def apply_transcription_request(self, **kwargs: object) -> _Inputs:
        self.captured["transcription_request"] = kwargs
        _capture_clip(Path(str(kwargs["audio"])), self.captured)
        return _Inputs(input_ids=_Tensor((1, 5)), audios=_Tensor((1, 32_000)))

    def __call__(self, *args: object, **kwargs: object) -> _Inputs:
        self.captured["processor_call"] = (args, kwargs)
        return _Inputs(input_ids=_Tensor((1, 5)), audio_data=_Tensor((1, 32_000)))

    def batch_decode(self, _tokens: _Tensor, **_kwargs: object) -> list[str]:
        return [str(self.captured["decoded_text"])]

    def load_template(self, path: str) -> None:
        self.captured["template"] = path


class _Model:
    device = _Device()
    dtype = "float32"

    def __init__(self, captured: dict[str, Any]) -> None:
        self.captured = captured

    def to(self, *_args: object, **_kwargs: object) -> _Model:
        return self

    def eval(self) -> _Model:
        return self

    def generate(self, **kwargs: object) -> _Tensor:
        self.captured["generate"] = kwargs
        input_ids = kwargs["input_ids"]
        assert isinstance(input_ids, _Tensor)
        return _Tensor((1, input_ids.shape[-1] + 3))


def _install_transformer_fakes(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
) -> None:
    processor = _Processor(captured)
    model = _Model(captured)

    class AutoProcessor:
        @classmethod
        def from_pretrained(cls, *_args: object, **kwargs: object) -> _Processor:
            captured["processor_load"] = kwargs
            return processor

    class AutoTokenizer:
        @classmethod
        def from_pretrained(cls, *_args: object, **kwargs: object) -> _Tokenizer:
            captured["tokenizer_load"] = kwargs
            return processor.tokenizer

    class AutoModel:
        @classmethod
        def from_pretrained(cls, *_args: object, **kwargs: object) -> _Model:
            captured["model_load"] = kwargs
            return model

    transformers = types.ModuleType("transformers")
    transformers.__dict__.update(
        AutoModelForCausalLM=AutoModel,
        AutoModelForSpeechSeq2Seq=AutoModel,
        AutoProcessor=AutoProcessor,
        AutoTokenizer=AutoTokenizer,
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "torch", _Torch("torch"))
    captured["processor"] = processor


def _install_librosa(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    librosa = types.ModuleType("librosa")

    def load(path: str, *, sr: int, mono: bool) -> tuple[list[float], int]:
        assert sr == 16_000 and mono is True
        _capture_clip(Path(path), captured)
        return [0.0] * 8, sr

    librosa.__dict__["load"] = load
    monkeypatch.setitem(sys.modules, "librosa", librosa)


def _canonical_wav(path: Path, samples: int = 64_000) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x01\x00" * samples)


def _spoken_wav(path: Path, *, voice: str, sentence: str) -> None:
    if shutil.which("espeak-ng") is None or shutil.which("ffmpeg") is None:
        pytest.skip("espeak-ng and FFmpeg are required for spoken-segment adapter tests")
    source = path.with_name(f"{path.stem}-source.wav")
    subprocess.run(
        ["espeak-ng", "-v", voice, "-s", "125", "-w", str(source), sentence],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )
    source.unlink()
    with wave.open(str(path), "rb") as recording:
        assert recording.getnframes() >= 48_000


def _capture_clip(path: Path, captured: dict[str, Any]) -> None:
    with wave.open(str(path), "rb") as clip:
        captured["clip_samples"] = clip.getnframes()
        captured["clip_rate"] = clip.getframerate()


def _params(audio: Path, language: str, *, expert: bool = False) -> dict[str, Any]:
    return {
        "audio_path": str(audio),
        "start_sample": 16_000,
        "end_sample": 48_000,
        "core_start_sample": 20_000,
        "core_end_sample": 44_000,
        "sample_rate": 16_000,
        "language": language,
        "manual_language": True,
        "candidate_role": "course_expert" if expert else "primary",
        "experimental_enabled": expert,
        "hints": [
            {
                "canonical": "ClassScribe",
                "reading": "class scribe",
                "category": "organization",
                "language": language,
            }
        ],
        "decode": {
            "temperature": 0.0,
            "do_sample": False,
            "seed": 7,
            "batch_size": 1,
            "mixed_length_batch": False,
            "max_new_tokens": 64,
            "max_output_characters": 128,
        },
        "batch_items": 1,
    }


def _model_dir(tmp_path: Path, *files: str) -> Path:
    path = tmp_path / "model"
    path.mkdir()
    for name in files:
        (path / name).write_text("test", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("do_sample", True, "temperature=0"),
        ("temperature", 0.2, "temperature=0"),
        ("batch_size", 2, "batch_size=1"),
        ("mixed_length_batch", True, "no mixed batch"),
    ],
)
def test_worker_decode_contract_rejects_sampling_and_mixed_batches(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    audio = tmp_path / "contract.wav"
    _canonical_wav(audio)
    params = _params(audio, "en")
    params["decode"][field] = value
    with pytest.raises(AdapterError, match=message):
        deterministic_decode(params)


def test_worker_decode_contract_rejects_more_than_one_audio_item(tmp_path: Path) -> None:
    audio = tmp_path / "contract.wav"
    _canonical_wav(audio)
    params = _params(audio, "en")
    params["batch_items"] = 2
    with pytest.raises(AdapterError, match="exactly one"):
        deterministic_decode(params)


def test_firered_real_zh_wav_preserves_english_and_native_absolute_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    torch = _Torch("torch")
    package = types.ModuleType("fireredasr2s")
    module = types.ModuleType("fireredasr2s.fireredasr2")

    class Config:
        def __init__(self, **kwargs: object) -> None:
            captured["config"] = kwargs

    class Model:
        @classmethod
        def from_pretrained(cls, kind: str, path: str, _config: Config) -> Model:
            captured["load"] = (kind, path)
            return cls()

        def transcribe(self, ids: list[str], paths: list[str]) -> list[dict[str, Any]]:
            assert ids == ["segment"] and len(paths) == 1
            _capture_clip(Path(paths[0]), captured)
            return [
                {
                    "text": "今天学习 CPU",
                    "confidence": 0.82,
                    "rtf": "0.01",
                    "timestamp": [("今天", 0.1, 0.5), ("CPU", 0.5, 1.0)],
                }
            ]

    module.__dict__.update(FireRedAsr2=Model, FireRedAsr2Config=Config)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "fireredasr2s", package)
    monkeypatch.setitem(sys.modules, "fireredasr2s.fireredasr2", module)
    from workers.firered.adapter import create_adapter

    model_path = _model_dir(
        tmp_path, "model.pth.tar", "cmvn.ark", "dict.txt", "train_bpe1000.model"
    )
    audio = tmp_path / "zh.wav"
    _spoken_wav(
        audio,
        voice="cmn",
        sentence="今天我们学习光合作用和中央处理器。今天我们继续学习课堂内容。",
    )
    adapter = create_adapter()

    async def exercise() -> Mapping[str, Any]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "firered_asr2_aed",
                "model_revision": REVISION,
                "model_path": str(model_path),
                "device": "cpu",
            },
            event,
        )
        return await adapter.dispatch("transcribe_batch", _params(audio, "zh"), event)

    result = asyncio.run(exercise())
    assert captured["clip_samples"] == 32_000
    assert result["raw_text"] == "今天学习 CPU"
    assert result["segments"][0]["confidence_raw"] == pytest.approx(0.82)
    assert result["segments"][0]["words"][0]["start_sample"] == 17_600
    assert result["segments"][0]["words"][1]["text"] == "CPU"


def test_ark_real_wav_uses_forced_nontranslation_prompt_and_greedy_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {"decoded_text": "量子 CPU"}
    _install_transformer_fakes(monkeypatch, captured)
    from workers.ark.adapter import create_adapter

    model_path = _model_dir(tmp_path, "config.json")
    audio = tmp_path / "ark.wav"
    _canonical_wav(audio)
    adapter = create_adapter()

    async def exercise() -> Mapping[str, Any]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "ark_asr_3b",
                "model_revision": REVISION,
                "model_path": str(model_path),
            },
            event,
        )
        return await adapter.dispatch("transcribe_batch", _params(audio, "zh"), event)

    result = asyncio.run(exercise())
    prompt = captured["processor_chat"][0]["content"][1]["text"]
    assert captured["clip_samples"] == 32_000
    assert "Chinese" in prompt and "without translating" in prompt
    assert captured["generate"]["do_sample"] is False
    assert captured["generate"]["max_new_tokens"] == 64
    assert [3] in captured["generate"]["bad_words_ids"]
    assert result["raw_text"] == "量子 CPU"


def test_qwen_real_ja_wav_forces_japanese_and_sends_pronunciation_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "torch", _Torch("torch"))
    qwen = types.ModuleType("qwen_asr")

    class Model:
        def __init__(self) -> None:
            self.max_new_tokens = 2048

        @classmethod
        def from_pretrained(cls, _path: str, **kwargs: object) -> Model:
            captured["load"] = kwargs
            return cls()

        def transcribe(self, **kwargs: object) -> list[SimpleNamespace]:
            captured["transcribe"] = kwargs
            _capture_clip(Path(str(kwargs["audio"])), captured)
            assert self.max_new_tokens == 64
            return [SimpleNamespace(text="今日は CPU です", language="Japanese")]

    qwen.__dict__["Qwen3ASRModel"] = Model
    monkeypatch.setitem(sys.modules, "qwen_asr", qwen)
    from workers.qwen.adapter import create_adapter

    model_path = _model_dir(tmp_path, "config.json")
    audio = tmp_path / "ja.wav"
    _canonical_wav(audio)
    params = _params(audio, "ja")
    params["candidate_role"] = "experimental"
    params["experimental_enabled"] = True
    adapter = create_adapter()

    async def exercise() -> Mapping[str, Any]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "qwen3_asr_1_7b",
                "model_revision": REVISION,
                "model_path": str(model_path),
            },
            event,
        )
        return await adapter.dispatch("transcribe_batch", params, event)

    result = asyncio.run(exercise())
    assert captured["clip_samples"] == 32_000
    assert captured["transcribe"]["language"] == "Japanese"
    assert "ClassScribe (class scribe)" in captured["transcribe"]["context"]
    assert result["raw_text"] == "今日は CPU です"
    assert result["metrics"]["route_variant"] == "qwen3_1_7b_forced_japanese_experimental"


def test_granite_real_ja_wav_uses_official_english_task_prompt_and_keywords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {"decoded_text": "今日は ClassScribe です"}
    _install_transformer_fakes(monkeypatch, captured)
    _install_librosa(monkeypatch, captured)
    from workers.granite.adapter import create_adapter

    model_path = _model_dir(tmp_path, "config.json")
    audio = tmp_path / "granite-ja.wav"
    _spoken_wav(
        audio,
        voice="ja",
        sentence="今日は光合成と中央処理装置を勉強します。次の授業内容も勉強します。",
    )
    adapter = create_adapter()

    async def exercise() -> Mapping[str, Any]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "granite_speech_4_1_2b",
                "model_revision": REVISION,
                "model_path": str(model_path),
            },
            event,
        )
        return await adapter.dispatch("transcribe_batch", _params(audio, "ja"), event)

    result = asyncio.run(exercise())
    assert captured["clip_samples"] == 32_000
    assert result["metrics"]["prompt"].startswith("transcribe the speech to text.")
    assert "Keywords: ClassScribe (class scribe)" in result["metrics"]["prompt"]
    assert result["metrics"]["prompt_language"] == "en"
    assert result["raw_text"] == "今日は ClassScribe です"


def test_moss_preview_real_en_wav_uses_local_dynamic_processor_and_greedy_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {"decoded_text": "Today we study photosynthesis"}
    _install_transformer_fakes(monkeypatch, captured)
    _install_librosa(monkeypatch, captured)
    dynamic = types.ModuleType("transformers.dynamic_module_utils")
    processor = captured["processor"]

    class MelConfig:
        def __init__(self, **kwargs: object) -> None:
            captured["mel_config"] = kwargs

    def get_class(name: str, *_args: object, **kwargs: object) -> object:
        captured.setdefault("dynamic_loads", []).append((name, kwargs))
        if name.endswith("MossProcessor"):
            return lambda *_args, **_kwargs: processor
        return MelConfig

    dynamic.__dict__["get_class_from_dynamic_module"] = get_class
    monkeypatch.setitem(sys.modules, "transformers.dynamic_module_utils", dynamic)
    from workers.moss_en.adapter import create_adapter

    model_path = _model_dir(tmp_path, "config.json", "chat_template_default.py")
    audio = tmp_path / "moss-en.wav"
    _spoken_wav(
        audio,
        voice="en-us",
        sentence=(
            "Today we study photosynthesis and central processing units. "
            "Then the class reviews the scientific evidence."
        ),
    )
    adapter = create_adapter()

    async def exercise() -> Mapping[str, Any]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "moss_transcribe_preview_2b",
                "model_revision": REVISION,
                "model_path": str(model_path),
            },
            event,
        )
        return await adapter.dispatch("transcribe_batch", _params(audio, "en"), event)

    result = asyncio.run(exercise())
    assert captured["clip_samples"] == 32_000
    assert captured["generate"]["do_sample"] is False
    assert captured["generate"]["num_beams"] == 1
    assert result["raw_text"] == "Today we study photosynthesis"
    assert "hints were not sent" in result["warnings"][0]


def test_funasr_real_wav_is_expert_gated_and_rejects_detected_generation_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {"decoded_text": "今日は ClassScribe です"}
    _install_transformer_fakes(monkeypatch, captured)
    from workers.funasr_experimental.adapter import create_adapter

    model_path = _model_dir(tmp_path, "config.json")
    audio = tmp_path / "fun-ja.wav"
    _canonical_wav(audio)
    adapter = create_adapter()

    async def exercise() -> tuple[Mapping[str, Any], str]:
        event = asyncio.Event()
        await adapter.dispatch(
            "load",
            {
                "model_id": "fun_asr_nano_2512",
                "model_revision": REVISION,
                "model_path": str(model_path),
            },
            event,
        )
        with pytest.raises(AdapterError, match="expert-only"):
            await adapter.dispatch("transcribe_batch", _params(audio, "ja"), event)
        result = await adapter.dispatch(
            "transcribe_batch", _params(audio, "ja", expert=True), event
        )
        captured["decoded_text"] = "反復 反復 反復 反復"
        with pytest.raises(AdapterError, match="loop detection") as loop_error:
            await adapter.dispatch("transcribe_batch", _params(audio, "ja", expert=True), event)
        return result, loop_error.value.detail

    result, detail = asyncio.run(exercise())
    request = captured["transcription_request"]
    assert captured["clip_samples"] == 32_000
    assert request["language"] == "ja"
    assert request["keywords"] == ["ClassScribe"]
    assert result["metrics"]["mandatory_loop_detection"] == "passed"
    assert "loop detection" in detail
