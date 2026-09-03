from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.audio.segmentation import StructureWindow
from classscribe.config import load_config
from classscribe.structure.anomaly import StructureAnomaly, inspect_structure_result
from classscribe.structure.models import (
    STRUCTURE_TEXT_ROLE,
    StructureContractError,
    build_moss_structure_request,
    parse_moss_structure_response,
)
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from classscribe_protocol import Priority, RPCResponse

REVISION = "a" * 40


def _window() -> StructureWindow:
    return StructureWindow(2, AudioSpan(1_000_000, 2_000_000), 64_000, 64_000, "test")


def _response(*segments: dict[str, object]) -> RPCResponse:
    return RPCResponse(
        request_id="request-1",
        job_id="job-1",
        ok=True,
        model_id="moss_td_0_9b",
        model_revision=REVISION,
        segments=segments,
        metrics={"rtf": 0.2},
    )


def test_moss_request_carries_full_speaker_policy_and_absolute_window() -> None:
    config = load_config(environment={})
    window = _window()
    request = build_moss_structure_request(
        request_id="request-1",
        job_id="job-1",
        audio_path=Path("/tmp/audio.wav"),
        window=window,
        config=config.classroom,
        language="ja",
        hotwords=(" 沖縄 ", "", "講義"),
    )
    assert request.priority == Priority.CLASSROOM_PRIMARY
    assert request.params["start_sample"] == window.span.start_sample
    assert request.params["end_sample"] == window.span.end_sample
    assert request.params["hotwords"] == ["沖縄", "講義"]
    assert request.params["speaker_policy"] == {
        "context": "ordinary_class",
        "expected_speakers": "auto",
        "prior_min": 1,
        "prior_typical": 2,
        "max_speakers": 12,
        "allow_overlap": True,
    }
    assert request.params["text_role"] == STRUCTURE_TEXT_ROLE


def test_moss_response_preserves_coarse_text_events_and_provenance() -> None:
    result = parse_moss_structure_response(
        _response(
            {
                "start_sample": 1_050_000,
                "end_sample": 1_400_000,
                "speaker_local": "S01",
                "text": "講義を始めます",
                "acoustic_events": ["applause", {"label": "laughter"}],
                "structure_score": 0.7,
            }
        ),
        _window(),
    )
    segment = result.segments[0]
    assert segment.span == AudioSpan(1_050_000, 1_400_000)
    assert segment.speaker_local == "S01"
    assert segment.acoustic_events == ("applause", "laughter")
    assert segment.text_role == STRUCTURE_TEXT_ROLE
    assert segment.provenance["adopted_as_final"] is False
    assert segment.provenance["absolute_samples"] is True
    assert segment.source_revision == REVISION


def test_moss_response_rejects_relative_or_out_of_window_samples() -> None:
    with pytest.raises(StructureContractError, match="escaped"):
        parse_moss_structure_response(
            _response(
                {
                    "start_sample": 0,
                    "end_sample": 10,
                    "speaker_local": "S01",
                    "text": "relative",
                }
            ),
            _window(),
        )


def test_structure_anomaly_uses_speech_coverage_duplicates_and_speaker_limit() -> None:
    window = _window()
    result = parse_moss_structure_response(
        _response(
            *(
                {
                    "start_sample": 1_000_000,
                    "end_sample": 1_010_000,
                    "speaker_local": f"S{index:02d}",
                    "text": "duplicate",
                }
                for index in range(13)
            )
        ),
        window,
    )
    report = inspect_structure_result(
        result,
        (AudioSpan(window.span.start_sample, window.span.end_sample),),
        load_config(environment={}).classroom,
    )
    assert report.needs_fallback is True
    assert StructureAnomaly.LOW_SPEECH_COVERAGE in report.anomalies
    assert StructureAnomaly.EXCESS_SPEAKERS in report.anomalies
    assert StructureAnomaly.DUPLICATE_SEGMENTS in report.anomalies
    assert report.metrics["speaker_count"] == 13


def test_silence_with_empty_structure_is_not_an_anomaly() -> None:
    result = parse_moss_structure_response(_response(), _window())
    report = inspect_structure_result(result, (), load_config(environment={}).classroom)
    assert report.needs_fallback is False
    assert report.metrics["speech_coverage"] == 1.0


def test_structure_overlap_segment_cannot_claim_exclusive_speaker() -> None:
    result = parse_moss_structure_response(
        _response(
            {
                "start_sample": 1_000_000,
                "end_sample": 1_000_000 + SAMPLE_RATE,
                "speaker_local": "S01",
                "text": "overlap",
                "overlap": True,
            }
        ),
        _window(),
    )
    assert result.segments[0].overlap is True
    assert result.segments[0].exclusive is False
