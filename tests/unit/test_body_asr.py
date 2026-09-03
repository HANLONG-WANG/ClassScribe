from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from classscribe.asr.models import (
    ASRContractError,
    CandidateRole,
    HintCategory,
    PronunciationHint,
    build_asr_request,
    moss_structure_candidate,
    parse_asr_response,
)
from classscribe.asr.pipeline import BodyASRPipeline, BodyASRPlanner
from classscribe.asr.variants import TraditionalChineseConverter
from classscribe.audio.segmentation import TranscriptChunk
from classscribe.models.registry import ModelEntry, ModelRegistry, load_registry
from classscribe.structure.models import StructureSegment
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from classscribe_protocol import RPCRequest, RPCResponse

ROOT = Path(__file__).resolve().parents[2]


def _chunk() -> TranscriptChunk:
    return TranscriptChunk(
        ordinal=3,
        core_span=AudioSpan(10 * SAMPLE_RATE, 28 * SAMPLE_RATE),
        audio_span=AudioSpan(9 * SAMPLE_RATE, 29 * SAMPLE_RATE),
        boundary_reason="speaker_change",
        hard_split=True,
        overlap_before_samples=SAMPLE_RATE,
        overlap_after_samples=SAMPLE_RATE,
    )


def _registry() -> ModelRegistry:
    return load_registry(ROOT / "config/model-registry.v1.yaml")


def _installed(entry: ModelEntry) -> ModelEntry:
    return entry.model_copy(
        update={
            "installation": entry.installation.model_copy(
                update={
                    "state": "installed",
                    "artifact_sha256": "a" * 64,
                    "local_revision": entry.revision,
                }
            )
        }
    )


def _installed_registry() -> ModelRegistry:
    registry = _registry()
    primary_ids = {
        "firered_asr2_aed",
        "granite_speech_4_1_2b",
        "moss_transcribe_preview_2b",
    }
    return registry.model_copy(
        update={
            "models": tuple(
                _installed(entry) if entry.id in primary_ids else entry for entry in registry.models
            )
        }
    )


def test_registry_planner_preserves_required_language_candidate_order() -> None:
    planner = BodyASRPlanner(_registry())
    assert [item.entry.id for item in planner.candidates("zh")] == [
        "firered_asr2_aed",
        "ark_asr_3b",
        "qwen3_asr_1_7b",
        "moss_td_0_9b",
    ]
    assert [item.entry.id for item in planner.candidates("ja")] == [
        "granite_speech_4_1_2b",
        "qwen3_asr_1_7b",
        "ark_asr_3b",
        "moss_td_0_9b",
    ]
    assert [item.entry.id for item in planner.candidates("en")] == [
        "moss_transcribe_preview_2b",
        "ark_asr_3b",
        "granite_speech_4_1_2b",
        "qwen3_asr_1_7b",
    ]
    with_expert = planner.candidates("ja", include_explicit_experts=True)
    assert with_expert[-1].entry.id == "fun_asr_nano_2512"
    assert with_expert[-1].role is CandidateRole.EXPERT
    ja_variant = planner.japanese_experimental_variant()
    assert ja_variant.entry.id == "qwen3_asr_1_7b"
    assert ja_variant.role is CandidateRole.EXPERIMENTAL


def test_qwen_forced_japanese_experiment_requires_explicit_enablement() -> None:
    planner = BodyASRPlanner(_registry())
    variant = planner.japanese_experimental_variant()
    with pytest.raises(ASRContractError, match="explicit"):
        build_asr_request(
            request_id="ja-experiment-blocked",
            job_id="job",
            audio_path=Path("/tmp/audio.wav"),
            chunk=_chunk(),
            entry=variant.entry,
            language="ja",
            role=variant.role,
        )
    request = build_asr_request(
        request_id="ja-experiment-enabled",
        job_id="job",
        audio_path=Path("/tmp/audio.wav"),
        chunk=_chunk(),
        entry=variant.entry,
        language="ja",
        role=variant.role,
        explicitly_enabled_experimental=True,
    )
    assert request.params["language"] == "ja"
    assert request.params["experimental_enabled"] is True


def test_request_is_one_deterministic_duration_bounded_item_with_pronunciations() -> None:
    entry = _registry().model("qwen3_asr_1_7b")
    request = build_asr_request(
        request_id="body-1",
        job_id="job-1",
        audio_path=Path("/tmp/audio.wav"),
        chunk=_chunk(),
        entry=entry,
        language="ja",
        role=CandidateRole.SECONDARY,
        hints=(
            PronunciationHint("鳥羽市", "とばし", HintCategory.PLACE, "ja"),
            PronunciationHint("ClassScribe", "class scribe", language="en"),
        ),
        rolling_context=("第一文", "第二文", "第三文", "直前の確認文"),
    )
    decode = request.params["decode"]
    assert decode == {
        "temperature": 0.0,
        "do_sample": False,
        "seed": 0,
        "batch_size": 1,
        "mixed_length_batch": False,
        "max_new_tokens": 352,
        "max_output_characters": 800,
    }
    assert request.params["language"] == "ja"
    assert request.params["batch_items"] == 1
    assert request.params["hints"] == [
        {"canonical": "鳥羽市", "reading": "とばし", "category": "place", "language": "ja"}
    ]
    assert request.params["rolling_context"] == ["第二文", "第三文", "直前の確認文"]


def test_runtime_route_rejects_firered_for_japanese_and_gated_expert() -> None:
    registry = _registry()
    with pytest.raises(ASRContractError, match="FireRed"):
        build_asr_request(
            request_id="bad-ja",
            job_id="job",
            audio_path=Path("/tmp/audio.wav"),
            chunk=_chunk(),
            entry=registry.model("firered_asr2_aed"),
            language="ja",
            role=CandidateRole.PRIMARY,
        )
    with pytest.raises(ASRContractError, match="explicit"):
        build_asr_request(
            request_id="expert",
            job_id="job",
            audio_path=Path("/tmp/audio.wav"),
            chunk=_chunk(),
            entry=registry.model("fun_asr_nano_2512"),
            language="ja",
            role=CandidateRole.EXPERT,
        )


def test_response_keeps_native_absolute_word_times_raw_confidence_and_decode() -> None:
    entry = _registry().model("firered_asr2_aed")
    request = build_asr_request(
        request_id="body-zh",
        job_id="job",
        audio_path=Path("/tmp/audio.wav"),
        chunk=_chunk(),
        entry=entry,
        language="zh",
        role=CandidateRole.PRIMARY,
    )
    response = RPCResponse(
        request_id=request.request_id,
        job_id=request.job_id,
        ok=True,
        model_id=entry.id,
        model_revision=entry.revision,
        raw_text="量子 CPU",
        normalized_text="量子 CPU",
        language="zh",
        segments=(
            {
                "start_sample": 10 * SAMPLE_RATE,
                "end_sample": 28 * SAMPLE_RATE,
                "text": "量子 CPU",
                "confidence_raw": 0.81,
                "words": [
                    {
                        "start_sample": 11 * SAMPLE_RATE,
                        "end_sample": 12 * SAMPLE_RATE,
                        "text": "量",
                        "confidence_raw": 0.9,
                    },
                    {
                        "start_sample": 12 * SAMPLE_RATE,
                        "end_sample": 13 * SAMPLE_RATE,
                        "text": "CPU",
                        "confidence_raw": 0.8,
                    },
                ],
            },
        ),
        metrics={"inference_ms": 20, "rtf": 0.01},
    )
    candidate = parse_asr_response(response, request, entry, _chunk(), CandidateRole.PRIMARY)
    assert candidate.raw_text.endswith("CPU")
    assert candidate.confidence_raw == pytest.approx(0.81)
    assert candidate.tokens[1].text == "CPU"
    assert candidate.tokens[1].span.start_sample == 12 * SAMPLE_RATE
    assert candidate.decode["seed"] == 0
    assert candidate.provenance["raw_confidence_not_cross_model_probability"] is True


def test_overlong_generation_is_rejected_before_candidate_persistence() -> None:
    entry = _registry().model("ark_asr_3b")
    request = build_asr_request(
        request_id="loop",
        job_id="job",
        audio_path=Path("/tmp/audio.wav"),
        chunk=_chunk(),
        entry=entry,
        language="en",
        role=CandidateRole.SECONDARY,
    )
    with pytest.raises(ASRContractError, match="character limit"):
        parse_asr_response(
            RPCResponse(
                request_id="loop",
                job_id="job",
                ok=True,
                model_id=entry.id,
                model_revision=entry.revision,
                raw_text="x" * 801,
                normalized_text="x" * 801,
            ),
            request,
            entry,
            _chunk(),
            CandidateRole.SECONDARY,
        )


@pytest.mark.parametrize(
    ("language", "model_id", "text"),
    [
        ("zh", "firered_asr2_aed", "今天学习 photosynthesis"),
        ("ja", "granite_speech_4_1_2b", "今日は光合成を学びます"),
        ("en", "moss_transcribe_preview_2b", "Today we study photosynthesis"),
    ],
)
def test_manual_language_primary_pipeline_end_to_end(
    language: str, model_id: str, text: str
) -> None:
    calls: list[tuple[str, RPCRequest]] = []

    async def invoke(entry: ModelEntry, request: RPCRequest) -> RPCResponse:
        calls.append((entry.id, request))
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id=entry.id,
            model_revision=entry.revision,
            raw_text=text,
            normalized_text=text,
            language=language,
            segments=(
                {
                    "start_sample": _chunk().core_span.start_sample,
                    "end_sample": _chunk().core_span.end_sample,
                    "text": text,
                    "confidence_raw": 0.8,
                    "words": [],
                },
            ),
            metrics={"inference_ms": 10, "peak_vram_mb": 100, "rtf": 0.01},
        )

    candidate = asyncio.run(
        BodyASRPipeline(_installed_registry(), invoke).transcribe_primary(
            job_id=f"job-{language}",
            audio_path=Path("/tmp/real-segment.wav"),
            chunk=_chunk(),
            language=language,
        )
    )
    assert calls[0][0] == model_id
    assert calls[0][1].params["manual_language"] is True
    assert candidate.normalized_text == text
    assert candidate.audio_span == _chunk().audio_span
    assert candidate.metrics["rtf"] == 0.01


def test_moss_structure_candidate_remains_nonfinal() -> None:
    segment = StructureSegment(
        window_ordinal=0,
        span=_chunk().core_span,
        speaker_local="M0",
        text="粗结构候选",
        acoustic_events=(),
        source_model="moss_td_0_9b",
        source_revision="b" * 40,
        provenance={
            "source_model": "moss_td_0_9b",
            "source_revision": "b" * 40,
            "adopted_as_final": False,
        },
    )
    candidate = moss_structure_candidate((segment,), _chunk(), language="zh")
    assert candidate is not None
    assert candidate.role is CandidateRole.STRUCTURE
    assert candidate.provenance["adopted_as_final"] is False


def test_opencc_variant_is_derived_after_faithful_simplified_and_preserves_english() -> None:
    class FakeOpenCC:
        def convert(self, text: str) -> str:
            return text.replace("学习", "學習")

    result = TraditionalChineseConverter(FakeOpenCC()).from_faithful_simplified(
        "学习 ClassScribe CPU"
    )
    assert result.text == "學習 ClassScribe CPU"
    assert result.source_layer == "faithful_simplified"
