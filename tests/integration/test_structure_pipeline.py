from __future__ import annotations

import asyncio
import json
from pathlib import Path

from classscribe.audio.segmentation import StructureWindow, make_structure_windows
from classscribe.config import load_config
from classscribe.structure.pipeline import StructurePipeline, WindowStructurePath
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from classscribe_protocol import RPCRequest, RPCResponse

MOSS_REVISION = "d" * 40
PYANNOTE_REVISION = "e" * 40


def _moss_response(request: RPCRequest) -> RPCResponse:
    start = int(request.params["start_sample"])
    end = int(request.params["end_sample"])
    ordinal = int(request.params["window_ordinal"])
    overlap = 4 * SAMPLE_RATE
    label = f"M{ordinal:02d}"
    segments: list[dict[str, object]] = []
    if ordinal:
        segments.append(
            {
                "start_sample": start,
                "end_sample": start + overlap,
                "speaker_local": label,
                "text": f"window join {ordinal - 1}",
                "acoustic_events": [],
                "structure_score": 0.9,
            }
        )
    body_start = start + overlap if ordinal else start
    body_end = end - overlap if ordinal < 7 and end - body_start > overlap else end
    if body_end > body_start:
        segments.append(
            {
                "start_sample": body_start,
                "end_sample": body_end,
                "speaker_local": label,
                "text": f"unique body {ordinal}",
                "acoustic_events": ["lecture"],
                "structure_score": 0.7,
            }
        )
    if end - body_end == overlap:
        segments.append(
            {
                "start_sample": body_end,
                "end_sample": end,
                "speaker_local": label,
                "text": f"window join {ordinal}",
                "acoustic_events": [],
                "structure_score": 0.5,
            }
        )
    return RPCResponse(
        request_id=request.request_id,
        job_id=request.job_id,
        ok=True,
        model_id="moss_td_0_9b",
        model_revision=MOSS_REVISION,
        segments=tuple(segments),
        metrics={"rtf": 0.2},
    )


def _pyannote_response(request: RPCRequest) -> RPCResponse:
    start = int(request.params["start_sample"])
    end = int(request.params["end_sample"])
    ordinal = int(request.params["window_ordinal"])
    label = f"P{ordinal:02d}"
    span = {"start_sample": start, "end_sample": end, "speaker_local": label}
    return RPCResponse(
        request_id=request.request_id,
        job_id=request.job_id,
        ok=True,
        model_id="pyannote_community_1",
        model_revision=PYANNOTE_REVISION,
        segments=(span,),
        result={
            "exclusive_segments": [span],
            "embeddings": [
                {
                    "start_sample": start,
                    "end_sample": start + SAMPLE_RATE,
                    "speaker_local": label,
                    "vector": [1.0, 0.0, 0.0],
                    "signal_quality": 0.95,
                },
                {
                    "start_sample": start + 2 * SAMPLE_RATE,
                    "end_sample": start + 3 * SAMPLE_RATE,
                    "speaker_local": label,
                    "vector": [0.99, 0.01, 0.0],
                    "signal_quality": 0.9,
                },
            ],
        },
    )


def test_ninety_minute_pipeline_uses_twelve_minute_windows_without_overlap_duplicates() -> None:
    duration = 90 * 60 * SAMPLE_RATE
    windows = make_structure_windows(duration)
    moss_calls: list[int] = []
    pyannote_calls: list[int] = []

    async def moss(request: RPCRequest) -> RPCResponse:
        moss_calls.append(int(request.params["window_ordinal"]))
        return _moss_response(request)

    async def pyannote(request: RPCRequest) -> RPCResponse:
        pyannote_calls.append(int(request.params["window_ordinal"]))
        return _pyannote_response(request)

    pipeline = StructurePipeline(load_config(environment={}).classroom, moss, pyannote)
    result = asyncio.run(
        pipeline.process(
            job_id="job-90m",
            audio_path=Path("/tmp/audio.wav"),
            windows=windows,
            speech_spans=(AudioSpan(0, duration),),
        )
    )
    assert len(windows) == 8
    assert moss_calls == list(range(8))
    assert pyannote_calls == list(range(8))
    assert all(item.path is WindowStructurePath.MOSS for item in result.windows)
    assert len(result.dedup_diagnostics) == 7
    assert len(result.segments) == 15
    assert len([item for item in result.segments if item.text.startswith("window join")]) == 7
    assert all(item.provenance["absolute_samples"] is True for item in result.segments)
    assert all(item.provenance["adopted_as_final"] is False for item in result.segments)
    assert {item.speaker_global for item in result.segments} == {"SPEAKER_01"}
    assert result.speaker_id_switches == 0
    assert result.transcript_chunks
    assert all(
        8 * SAMPLE_RATE <= item.core_span.duration_samples <= 30 * SAMPLE_RATE
        for item in result.transcript_chunks
    )
    assert all(
        not item.hard_split
        or (
            (
                item.audio_span.start_sample < item.core_span.start_sample
                or item.core_span.start_sample == 0
            )
            and (
                item.audio_span.end_sample > item.core_span.end_sample
                or item.core_span.end_sample == duration
            )
        )
        for item in result.transcript_chunks
    )
    json.dumps(result.diagnostic_payload())


def test_natural_transcript_chunks_split_on_stitched_speaker_change() -> None:
    duration = 40 * SAMPLE_RATE
    window = StructureWindow(0, AudioSpan(0, duration), 0, 0, "test")

    async def moss(request: RPCRequest) -> RPCResponse:
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id="moss_td_0_9b",
            model_revision=MOSS_REVISION,
            segments=(
                {
                    "start_sample": 0,
                    "end_sample": 20 * SAMPLE_RATE,
                    "speaker_local": "M0",
                    "text": "first speaker",
                    "acoustic_events": [],
                    "structure_score": 0.8,
                },
                {
                    "start_sample": 20 * SAMPLE_RATE,
                    "end_sample": duration,
                    "speaker_local": "M1",
                    "text": "second speaker",
                    "acoustic_events": [],
                    "structure_score": 0.8,
                },
            ),
        )

    async def pyannote(request: RPCRequest) -> RPCResponse:
        spans = [
            {"start_sample": 0, "end_sample": 20 * SAMPLE_RATE, "speaker_local": "P0"},
            {
                "start_sample": 20 * SAMPLE_RATE,
                "end_sample": duration,
                "speaker_local": "P1",
            },
        ]
        embeddings = [
            {
                "start_sample": index * 20 * SAMPLE_RATE,
                "end_sample": index * 20 * SAMPLE_RATE + SAMPLE_RATE,
                "speaker_local": f"P{index}",
                "vector": [float(index == 0), float(index == 1)],
                "signal_quality": 0.9,
                "support_count": 2,
            }
            for index in range(2)
        ]
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id="pyannote_community_1",
            model_revision=PYANNOTE_REVISION,
            segments=tuple(spans),
            result={"exclusive_segments": spans, "embeddings": embeddings},
        )

    result = asyncio.run(
        StructurePipeline(load_config(environment={}).classroom, moss, pyannote).process(
            job_id="job-speaker-boundary",
            audio_path=Path("/tmp/audio.wav"),
            windows=(window,),
            speech_spans=(window.span,),
        )
    )
    assert [item.core_span for item in result.transcript_chunks] == [
        AudioSpan(0, 20 * SAMPLE_RATE),
        AudioSpan(20 * SAMPLE_RATE, duration),
    ]
    assert all(not item.hard_split for item in result.transcript_chunks)


def test_anomalous_moss_runs_pyannote_and_keeps_coarse_text_role() -> None:
    window = make_structure_windows(12 * 60 * SAMPLE_RATE)[0]

    async def empty_moss(request: RPCRequest) -> RPCResponse:
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id="moss_td_0_9b",
            model_revision=MOSS_REVISION,
        )

    async def pyannote(request: RPCRequest) -> RPCResponse:
        return _pyannote_response(request)

    result = asyncio.run(
        StructurePipeline(
            load_config(environment={}).classroom,
            empty_moss,
            pyannote,
        ).process(
            job_id="job-fallback",
            audio_path=Path("/tmp/audio.wav"),
            windows=(window,),
            speech_spans=(window.span,),
        )
    )
    outcome = result.windows[0]
    assert outcome.path is WindowStructurePath.PYANNOTE_FALLBACK
    assert outcome.moss_attempts == 2
    assert outcome.pyannote_attempts == 1
    assert all(item.fallback and item.text == "" for item in outcome.segments)
    assert all(item.provenance["adopted_as_final"] is False for item in outcome.segments)


def test_worker_failures_are_isolated_to_one_window_and_leave_retryable_coarse_structure() -> None:
    windows = make_structure_windows(20 * 60 * SAMPLE_RATE)

    async def moss(request: RPCRequest) -> RPCResponse:
        if int(request.params["window_ordinal"]) == 0:
            return RPCResponse(
                request_id=request.request_id,
                job_id=request.job_id,
                ok=False,
                model_id="moss_td_0_9b",
                model_revision=MOSS_REVISION,
                error_code="internal_error",
                error_detail="window zero failure",
            )
        return _moss_response(request)

    async def pyannote(request: RPCRequest) -> RPCResponse:
        if int(request.params["window_ordinal"]) == 0:
            return RPCResponse(
                request_id=request.request_id,
                job_id=request.job_id,
                ok=False,
                model_id="pyannote_community_1",
                model_revision=PYANNOTE_REVISION,
                error_code="internal_error",
                error_detail="window zero fallback failure",
            )
        return _pyannote_response(request)

    result = asyncio.run(
        StructurePipeline(load_config(environment={}).classroom, moss, pyannote).process(
            job_id="job-isolation",
            audio_path=Path("/tmp/audio.wav"),
            windows=windows,
            speech_spans=(AudioSpan(0, 20 * 60 * SAMPLE_RATE),),
        )
    )
    assert result.windows[0].path is WindowStructurePath.COARSE_VAD
    assert result.windows[0].segments[0].provenance["retryable"] is True
    assert result.windows[1].path is WindowStructurePath.MOSS
    assert result.windows[1].errors == ()
