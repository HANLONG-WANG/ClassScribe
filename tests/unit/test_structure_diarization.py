from __future__ import annotations

from pathlib import Path

from classscribe.audio.segmentation import StructureWindow
from classscribe.config import load_config
from classscribe.structure.diarization import (
    apply_pyannote_fallback,
    build_pyannote_request,
    parse_pyannote_response,
)
from classscribe.structure.models import StructureSegment
from classscribe.timeline import AudioSpan
from classscribe_protocol import RPCResponse

REVISION = "b" * 40


def _window() -> StructureWindow:
    return StructureWindow(1, AudioSpan(100_000, 300_000), 64_000, 64_000, "test")


def _response() -> RPCResponse:
    return RPCResponse(
        request_id="py-1",
        job_id="job-1",
        ok=True,
        model_id="pyannote_community_1",
        model_revision=REVISION,
        segments=(
            {
                "start_sample": 100_000,
                "end_sample": 220_000,
                "speaker_local": "P0",
            },
            {
                "start_sample": 180_000,
                "end_sample": 260_000,
                "speaker_local": "P1",
            },
        ),
        result={
            "exclusive_segments": [
                {
                    "start_sample": 100_000,
                    "end_sample": 190_000,
                    "speaker_local": "P0",
                },
                {
                    "start_sample": 190_000,
                    "end_sample": 260_000,
                    "speaker_local": "P1",
                },
            ],
            "embeddings": [
                {
                    "start_sample": 100_000,
                    "end_sample": 150_000,
                    "speaker_local": "P0",
                    "vector": [1.0, 0.0],
                    "signal_quality": 0.9,
                    "support_count": 3,
                },
                {
                    "start_sample": 220_000,
                    "end_sample": 250_000,
                    "speaker_local": "P1",
                    "vector": [0.0, 1.0],
                    "signal_quality": 0.8,
                    "support_count": 2,
                },
            ],
        },
    )


def test_pyannote_request_uses_bounds_and_exact_count_when_selected() -> None:
    config = load_config(environment={}).classroom.model_copy(
        update={"speaker_context": "group_discussion", "expected_speakers": 4}
    )
    request = build_pyannote_request(
        request_id="py-1",
        job_id="job-1",
        audio_path=Path("/tmp/audio.wav"),
        window=_window(),
        config=config,
    )
    assert request.params["num_speakers"] == 4
    assert request.params["min_speakers"] == 1
    assert request.params["max_speakers"] == 12
    assert request.params["return_exclusive"] is True
    assert request.params["return_embeddings"] is True


def test_pyannote_parser_keeps_exclusive_track_and_explicit_true_overlap() -> None:
    result = parse_pyannote_response(_response(), _window())
    assert len(result.regular_spans) == 2
    assert len(result.exclusive_spans) == 2
    assert result.true_overlaps[0].span == AudioSpan(180_000, 220_000)
    assert result.true_overlaps[0].speaker_local_ids == ("P0", "P1")
    assert len(result.overlap_spans) == 2
    assert all(item.overlap and not item.exclusive for item in result.overlap_spans)
    assert all(
        item.overlap or not item.span.overlaps(result.true_overlaps[0].span)
        for item in result.persistent_speaker_spans
    )
    assert result.embeddings[0].support_count == 3


def test_fallback_uses_exclusive_speaker_but_refuses_unique_overlap_assignment() -> None:
    coarse = (
        StructureSegment(
            window_ordinal=1,
            span=AudioSpan(105_000, 160_000),
            speaker_local="M0",
            text="ordinary",
            acoustic_events=(),
            source_model="moss_td_0_9b",
            source_revision="a" * 40,
            provenance={"absolute_samples": True, "adopted_as_final": False},
        ),
        StructureSegment(
            window_ordinal=1,
            span=AudioSpan(180_000, 220_000),
            speaker_local="M0",
            text="two people speaking",
            acoustic_events=(),
            source_model="moss_td_0_9b",
            source_revision="a" * 40,
            provenance={"absolute_samples": True, "adopted_as_final": False},
        ),
    )
    mapped = apply_pyannote_fallback(coarse, parse_pyannote_response(_response(), _window()))
    assert mapped[0].speaker_local == "P0"
    assert mapped[0].exclusive is True
    assert mapped[1].speaker_local is None
    assert mapped[1].overlap is True
    assert mapped[1].provenance["true_overlap_speakers"] == ["P0", "P1"]
    assert mapped[1].provenance["moss_text_retained_as_coarse_only"] is True


def test_fallback_without_coarse_text_creates_timeline_only_segments() -> None:
    mapped = apply_pyannote_fallback((), parse_pyannote_response(_response(), _window()))
    assert [item.speaker_local for item in mapped] == ["P0", None, "P1"]
    assert mapped[1].span == AudioSpan(180_000, 220_000)
    assert mapped[1].overlap is True and mapped[1].exclusive is False
    assert all(item.text == "" and item.fallback for item in mapped)
    assert all(item.provenance["adopted_as_final"] is False for item in mapped)
