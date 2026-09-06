from __future__ import annotations

import json
from pathlib import Path

import pytest
from yaml.constructor import ConstructorError

from classscribe_manifest_tool.selection import load_file_selection

ROOT = Path(__file__).resolve().parents[3]


def _write_selection(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_production_selection_contains_only_reviewed_model_closures() -> None:
    selection = load_file_selection(ROOT / "config/model-file-selection.v1.yaml")

    assert selection.schema_version == 1
    assert tuple(selection.models) == (
        "ark_asr_3b",
        "firered_asr2_aed",
        "firered_asr2_llm",
        "firered_lid",
        "firered_punc",
        "firered_vad",
        "fun_asr_nano_2512",
        "granite_speech_4_1_2b",
        "granite_speech_5_0_turboctc_470m",
        "moss_td_0_9b",
        "moss_transcribe_preview_2b",
        "nemotron_3_5_asr_streaming_0_6b",
        "nemotron_speech_streaming_en_0_6b",
        "pyannote_community_1",
        "qwen3_asr_0_6b",
        "qwen3_asr_1_7b",
        "qwen3_forced_aligner_0_6b",
        "vibevoice_asr_streaming_1_5b",
        "voxtral_mini_4b_realtime_2602",
        "whisper_tiny_reference",
    )
    ark = selection.selection("ark_asr_3b")
    assert {
        "configuration_arkasr.py",
        "modeling_arkasr.py",
        "modeling_audio.py",
        "processing_arkasr.py",
        "processor_config.json",
    } <= set(ark.include)
    assert selection.selection("firered_asr2_aed").include == (
        "cmvn.ark",
        "dict.txt",
        "model.pth.tar",
        "train_bpe1000.model",
    )
    assert selection.selection("firered_lid").include == (
        "cmvn.ark",
        "dict.txt",
        "model.pth.tar",
    )
    vad = selection.selection("firered_vad")
    assert vad.include == (
        "Stream-VAD/cmvn.ark",
        "Stream-VAD/model.pth.tar",
        "VAD/cmvn.ark",
        "VAD/model.pth.tar",
    )
    assert "AED/model.pth.tar" in vad.exclude
    punctuation = selection.selection("firered_punc")
    assert {
        "chinese-bert-wwm-ext_vocab.txt",
        "chinese-lert-base/config.json",
        "chinese-lert-base/pytorch_model.bin",
        "chinese-lert-base/tokenizer.json",
        "chinese-lert-base/vocab.txt",
        "config.yaml",
        "model.pth.tar",
        "out_dict",
    } <= set(punctuation.include)
    assert "chinese-lert-base/tf_model.h5" in punctuation.exclude
    llm = selection.selection("firered_asr2_llm")
    assert "Qwen2-7B-Instruct/model.safetensors.index.json" in llm.include
    assert {
        f"Qwen2-7B-Instruct/model-{shard:05d}-of-00004.safetensors"
        for shard in range(1, 5)
    } <= set(llm.include)
    assert "Qwen2-7B-Instruct/LICENSE" in llm.exclude
    fun_asr = selection.selection("fun_asr_nano_2512")
    assert {path for path, kind in fun_asr.kinds.items() if kind == "model"} == {
        "model.safetensors"
    }
    assert {
        "chat_template.jinja",
        "processor_config.json",
        "tokenizer.json",
    } <= set(fun_asr.include)
    assert "remote_code" not in fun_asr.kinds.values()
    granite = selection.selection("granite_speech_4_1_2b")
    assert {
        "model-00001-of-00003.safetensors",
        "model-00002-of-00003.safetensors",
        "model-00003-of-00003.safetensors",
        "model.safetensors.index.json",
    } <= set(granite.include)
    assert {"model.sig", "out_llm.safetensors"} <= set(granite.exclude)
    assert "remote_code" not in granite.kinds.values()
    turboctc = selection.selection("granite_speech_5_0_turboctc_470m")
    assert turboctc.kinds["processing_ctc_conformer.py"] == "remote_code"
    assert {
        "configuration_ctc_conformer.py",
        "granite_encoder.py",
        "modeling_ctc_conformer.py",
    } <= set(turboctc.exclude)
    assert {path for path, kind in turboctc.kinds.items() if kind == "model"} == {
        "model.safetensors"
    }
    moss_td = selection.selection("moss_td_0_9b")
    assert {
        "configuration_moss_transcribe_diarize.py",
        "modeling_moss_transcribe_diarize.py",
        "processing_moss_transcribe_diarize.py",
        "processor_config.json",
    } <= set(moss_td.include)
    moss_preview = selection.selection("moss_transcribe_preview_2b")
    assert {
        "chat_template_default.py",
        "modeling_Moss.py",
        "processing_Moss.py",
    } <= set(moss_preview.include)
    for remote_selection in (ark, moss_td, moss_preview):
        dynamic_paths = {
            path
            for path in remote_selection.include
            if Path(path).suffix in {".jinja", ".py"}
        }
        assert dynamic_paths == {
            path
            for path, kind in remote_selection.kinds.items()
            if kind == "remote_code"
        }
    nemo_multilingual = selection.selection("nemotron_3_5_asr_streaming_0_6b")
    nemo_english = selection.selection("nemotron_speech_streaming_en_0_6b")
    assert nemo_multilingual.include == ("nemotron-3.5-asr-streaming-0.6b.nemo",)
    assert nemo_english.include == ("nemotron-speech-streaming-en-0.6b.nemo",)
    for nemo in (nemo_multilingual, nemo_english):
        assert nemo.kinds[nemo.include[0]] == "model"
        assert "model.safetensors" in nemo.exclude
        assert any(path.endswith(".gguf") for path in nemo.exclude)
    pyannote = selection.selection("pyannote_community_1")
    assert pyannote.include == (
        "config.yaml",
        "embedding/pytorch_model.bin",
        "plda/plda.npz",
        "plda/xvec_transform.npz",
        "segmentation/pytorch_model.bin",
    )
    assert dict(pyannote.kinds) == {
        "config.yaml": "config",
        "embedding/pytorch_model.bin": "model",
        "plda/plda.npz": "model",
        "plda/xvec_transform.npz": "model",
        "segmentation/pytorch_model.bin": "model",
    }
    assert pyannote.exclude == (
        ".gitattributes",
        "README.md",
        "diarization.gif",
        "embedding/README.md",
        "plda/README.md",
    )
    qwen_small = selection.selection("qwen3_asr_0_6b")
    qwen_large = selection.selection("qwen3_asr_1_7b")
    qwen_aligner = selection.selection("qwen3_forced_aligner_0_6b")
    assert "model.safetensors" in qwen_small.include
    assert "model.safetensors" in qwen_aligner.include
    assert {
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
        "model.safetensors.index.json",
    } <= set(qwen_large.include)
    for qwen in (qwen_small, qwen_large, qwen_aligner):
        assert "chat_template.json" in qwen.include
        assert "preprocessor_config.json" in qwen.include
        assert "remote_code" not in qwen.kinds.values()
    assert "qwen3_asr_1_7b_ja" not in selection.models
    vibevoice = selection.selection("vibevoice_asr_streaming_1_5b")
    assert {
        "model-00001-of-00003.safetensors",
        "model-00002-of-00003.safetensors",
        "model-00003-of-00003.safetensors",
        "model.safetensors.index.json",
    } <= set(vibevoice.include)
    assert {
        "figures/VibeVoice_ASR_Streaming_architecture.png",
        "figures/VibeVoice_ASR_Streaming_results.png",
    } <= set(vibevoice.exclude)
    assert "remote_code" not in vibevoice.kinds.values()
    voxtral = selection.selection("voxtral_mini_4b_realtime_2602")
    assert {path for path, kind in voxtral.kinds.items() if kind == "model"} == {
        "model.safetensors"
    }
    assert {"processor_config.json", "tekken.json"} <= set(voxtral.include)
    assert {"consolidated.safetensors", "params.json"} <= set(voxtral.exclude)
    assert "remote_code" not in voxtral.kinds.values()
    whisper = selection.selection("whisper_tiny_reference")
    assert whisper.include == (
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    )
    assert dict(whisper.kinds) == {
        "config.json": "config",
        "model.bin": "model",
        "tokenizer.json": "tokenizer",
        "vocabulary.txt": "tokenizer",
    }
    assert whisper.exclude == (".gitattributes", "README.md")


def test_selection_loads_exact_sorted_paths_and_is_immutable(tmp_path: Path) -> None:
    path = _write_selection(
        tmp_path / "selection.yaml",
        """schema_version: 1
models:
  alpha:
    include:
      - config.json
      - pkg/modeling.py
      - weights/model.safetensors
    kinds:
      config.json: config
      pkg/modeling.py: remote_code
      weights/model.safetensors: model
    exclude:
      - unused/model.onnx
""",
    )

    selection = load_file_selection(path)
    alpha = selection.selection("alpha")

    assert alpha.include == (
        "config.json",
        "pkg/modeling.py",
        "weights/model.safetensors",
    )
    assert dict(alpha.kinds) == {
        "config.json": "config",
        "pkg/modeling.py": "remote_code",
        "weights/model.safetensors": "model",
    }
    assert alpha.exclude == ("unused/model.onnx",)
    with pytest.raises(TypeError):
        selection.models["beta"] = alpha  # type: ignore[index]
    with pytest.raises(TypeError):
        alpha.kinds["config.json"] = "model"  # type: ignore[index]


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            """schema_version: 1
models:
  alpha:
    include: [config.json, config.json]
    kinds: {config.json: config}
    exclude: []
""",
            "duplicate paths",
        ),
        (
            """schema_version: 1
models:
  alpha:
    include: [weights.bin, config.json]
    kinds: {weights.bin: model, config.json: config}
    exclude: []
""",
            "must be sorted",
        ),
        (
            """schema_version: 1
models:
  alpha:
    include: [config.json]
    kinds: {other.json: config}
    exclude: []
""",
            "classify every include path exactly once",
        ),
        (
            """schema_version: 1
models:
  alpha:
    include: [weights/*.bin]
    kinds: {weights/*.bin: model}
    exclude: []
""",
            "unsafe or non-exact path",
        ),
        (
            """schema_version: 1
models:
  alpha:
    include: [../escape.bin]
    kinds: {../escape.bin: model}
    exclude: []
""",
            "unsafe or non-exact path",
        ),
        (
            """schema_version: 1
models:
  alpha:
    include: [config.json]
    kinds: {config.json: config}
    exclude: [config.json]
""",
            "include and exclude overlap",
        ),
        (
            """schema_version: 1
models:
  beta:
    include: [config.json]
    kinds: {config.json: config}
    exclude: []
  alpha:
    include: [config.json]
    kinds: {config.json: config}
    exclude: []
""",
            "sorted by model ID",
        ),
    ],
)
def test_selection_rejects_non_exact_or_ambiguous_entries(
    tmp_path: Path, body: str, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        load_file_selection(_write_selection(tmp_path / "selection.yaml", body))


def test_selection_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = _write_selection(
        tmp_path / "selection.yaml",
        """schema_version: 1
schema_version: 1
models: {}
""",
    )

    with pytest.raises(ConstructorError, match="duplicate key"):
        load_file_selection(path)


def test_selection_schema_describes_exact_path_and_kind_contract() -> None:
    schema = json.loads(
        (ROOT / "config/schema/model-file-selection.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    selection = schema["$defs"]["model_selection"]
    path = schema["$defs"]["safe_exact_path"]

    assert schema["additionalProperties"] is False
    assert set(selection["required"]) == {"include", "kinds", "exclude"}
    assert selection["properties"]["include"]["minItems"] == 1
    assert selection["properties"]["include"]["uniqueItems"] is True
    assert selection["properties"]["exclude"]["uniqueItems"] is True
    assert selection["properties"]["kinds"]["additionalProperties"]["enum"] == [
        "model",
        "tokenizer",
        "config",
        "remote_code",
        "other",
    ]
    assert "(?!.*[*?\\[\\]])" in path["pattern"]
