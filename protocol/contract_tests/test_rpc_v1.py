from __future__ import annotations

import asyncio
import stat
import struct
from pathlib import Path
from typing import Any

import pytest
from classscribe_protocol import (
    MAX_FRAME_BYTES,
    Priority,
    ProtocolError,
    RPCClient,
    RPCRequest,
    RPCResponse,
    RPCServer,
    decode_payload,
    encode_frame,
)
from classscribe_protocol.adapter import StatefulAdapter

REVISION = "d" * 40


def _request(method: str, params: dict[str, Any], request_id: str = "request-1") -> RPCRequest:
    return RPCRequest(
        request_id=request_id,
        job_id="job-1",
        deadline_ms=1000,
        priority=Priority.CLASSROOM_PRIMARY,
        method=method,
        params=params,
    )


def test_messagepack_frame_roundtrip_and_big_endian_prefix() -> None:
    request = _request("health", {})
    frame = encode_frame(request)
    assert struct.unpack(">I", frame[:4])[0] == len(frame) - 4
    assert decode_payload(frame[4:], RPCRequest) == request
    with pytest.raises(ProtocolError, match="frame limit"):
        encode_frame(_request("health", {"oversized": b"x" * (MAX_FRAME_BYTES + 1)}))


@pytest.mark.parametrize("priority", [1, 9, 11, 50])
def test_request_rejects_non_contract_priority(priority: int) -> None:
    with pytest.raises(ProtocolError, match="priority"):
        RPCRequest("r", "j", 1000, priority, "health", {})


def test_batch_and_stream_payloads_are_strict() -> None:
    with pytest.raises(ProtocolError, match="absolute"):
        _request(
            "transcribe_batch",
            {
                "audio_path": "relative.wav",
                "start_sample": 0,
                "end_sample": 10,
                "sample_rate": 16000,
            },
        )
    with pytest.raises(ProtocolError, match="whole int16"):
        _request("stream_push", {"stream_id": "s", "pcm_s16le": b"x"})
    with pytest.raises(ProtocolError, match="quality_probability"):
        _request("health", {"nested": {"quality_probability": 0.9}})


def test_body_asr_request_contract_rejects_implicit_language_sampling_and_bad_core() -> None:
    valid: dict[str, Any] = {
        "audio_path": "/tmp/audio.wav",
        "start_sample": 100,
        "end_sample": 500,
        "core_start_sample": 120,
        "core_end_sample": 480,
        "sample_rate": 16000,
        "language": "ja",
        "manual_language": True,
        "candidate_role": "primary",
        "experimental_enabled": False,
        "request_contract": "body-asr-v1",
        "hints": [
            {
                "canonical": "鳥羽市",
                "reading": "とばし",
                "category": "place",
                "language": "ja",
            }
        ],
        "decode": {
            "temperature": 0.0,
            "do_sample": False,
            "seed": 0,
            "batch_size": 1,
            "mixed_length_batch": False,
            "max_new_tokens": 64,
            "max_output_characters": 128,
        },
        "batch_items": 1,
    }
    assert _request("transcribe_batch", valid).params["language"] == "ja"
    for update, message in (
        ({"manual_language": False}, "manual"),
        ({"core_start_sample": 99}, "core range"),
        ({"batch_items": 2}, "one audio item"),
        ({"decode": {**valid["decode"], "do_sample": True}}, "decode requires"),
    ):
        with pytest.raises(ProtocolError, match=message):
            _request("transcribe_batch", {**valid, **update})


def test_punctuation_and_final_alignment_contracts_are_strict() -> None:
    punctuation: dict[str, Any] = {
        "text": "Hello world",
        "language": "en",
        "manual_language": True,
        "punctuation_contract": "strict-punctuation-v1",
    }
    assert _request("punctuate", punctuation).params["text"] == "Hello world"
    with pytest.raises(ProtocolError, match="manual zh or en"):
        _request("punctuate", {**punctuation, "language": "ja"})

    alignment: dict[str, Any] = {
        "audio_path": "/tmp/audio.wav",
        "start_sample": 0,
        "end_sample": 32000,
        "sample_rate": 16000,
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
            "voiced_seconds": 2.0,
        },
    }
    assert _request("align", alignment).params["alignment_contract"] == "final-align-v1"
    with pytest.raises(ProtocolError, match="quality gate failed"):
        _request(
            "align",
            {
                **alignment,
                "quality_gate": {**alignment["quality_gate"], "no_decode_loop": False},
            },
        )
    with pytest.raises(ProtocolError, match="safe maximum"):
        _request("align", {**alignment, "end_sample": 30 * 16000})


def test_response_word_timing_and_raw_confidence_are_strict() -> None:
    segment: dict[str, Any] = {
        "start_sample": 100,
        "end_sample": 500,
        "text": "CPU",
        "timing_kind": "native",
        "words": [
            {
                "start_sample": 120,
                "end_sample": 200,
                "text": "CPU",
                "confidence_raw": -0.2,
            }
        ],
    }
    assert RPCResponse("r", "j", True, "model", REVISION, segments=(segment,)).segments
    with pytest.raises(ProtocolError, match="contained"):
        RPCResponse(
            "r",
            "j",
            True,
            "model",
            REVISION,
            segments=(
                {
                    **segment,
                    "words": [{**segment["words"][0], "start_sample": 50}],
                },
            ),
        )


def test_response_requires_revision_absolute_segments_and_no_calibrated_probability() -> None:
    with pytest.raises(ProtocolError, match="immutable revision"):
        RPCResponse("r", "j", True, "model", "main")
    with pytest.raises(ProtocolError, match="ordered absolute"):
        RPCResponse(
            "r",
            "j",
            True,
            "model",
            REVISION,
            segments=({"start_sample": 5, "end_sample": 4},),
        )
    with pytest.raises(ProtocolError, match="quality_probability"):
        RPCResponse("r", "j", True, "model", REVISION, metrics={"quality_probability": 1.0})


def test_structure_and_diarization_response_extensions_are_strict() -> None:
    valid = RPCResponse(
        "r",
        "j",
        True,
        "model",
        REVISION,
        segments=(
            {
                "start_sample": 10,
                "end_sample": 20,
                "speaker_local": "S01",
                "acoustic_events": ["applause"],
                "overlap": False,
            },
        ),
        result={
            "text_role": "coarse_timeline_consensus_candidate_boundary_reference",
            "adopted_as_final": False,
            "exclusive_segments": [{"start_sample": 10, "end_sample": 20, "speaker_local": "P0"}],
            "embeddings": [
                {
                    "start_sample": 10,
                    "end_sample": 20,
                    "speaker_local": "P0",
                    "vector": [1.0, 0.0],
                    "signal_quality": 0.9,
                    "support_count": 2,
                    "overlap": False,
                }
            ],
        },
    )
    assert valid.result["adopted_as_final"] is False
    with pytest.raises(ProtocolError, match="may not be adopted"):
        RPCResponse(
            "r",
            "j",
            True,
            "model",
            REVISION,
            result={
                "text_role": "coarse_timeline_consensus_candidate_boundary_reference",
                "adopted_as_final": True,
            },
        )
    with pytest.raises(ProtocolError, match="signal_quality"):
        RPCResponse(
            "r",
            "j",
            True,
            "model",
            REVISION,
            result={
                "embeddings": [
                    {
                        "start_sample": 10,
                        "end_sample": 20,
                        "speaker_local": "P0",
                        "vector": [1.0],
                        "signal_quality": 1.1,
                    }
                ]
            },
        )


def test_rpc_load_batch_stream_unload_and_socket_permissions(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        audio = data_root / "audio.wav"
        audio.write_bytes(b"RIFF")
        audio.chmod(0o400)
        model = tmp_path / "model"
        model.mkdir()
        adapter = StatefulAdapter(
            "test",
            capabilities=("asr", "streaming"),
            supported_methods=(
                "transcribe_batch",
                "stream_open",
                "stream_push",
                "stream_flush",
                "stream_close",
            ),
        )

        async def transcribe(params: Any, _cancelled: asyncio.Event) -> dict[str, Any]:
            return {
                "language": "en",
                "raw_text": " raw ",
                "normalized_text": "raw",
                "segments": [
                    {
                        "start_sample": params["start_sample"],
                        "end_sample": params["end_sample"],
                        "text": "raw",
                        "confidence_raw": -0.2,
                        "words": [],
                    }
                ],
                "metrics": {"inference_ms": 1, "peak_vram_mb": 0, "rtf": 0.01},
                "warnings": [],
            }

        adapter.register("transcribe_batch", transcribe)
        socket_path = tmp_path / "runtime/worker.sock"
        async with RPCServer(socket_path, adapter, allowed_data_roots=(data_root,)):
            assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
            client = RPCClient(socket_path)
            loaded = await client.call(
                _request(
                    "load",
                    {"model_id": "real", "model_revision": REVISION, "model_path": str(model)},
                )
            )
            assert loaded.ok and loaded.result == {"loaded": True}
            transcribed = await client.call(
                _request(
                    "transcribe_batch",
                    {
                        "audio_path": str(audio),
                        "start_sample": 100,
                        "end_sample": 500,
                        "sample_rate": 16000,
                    },
                )
            )
            assert transcribed.model_revision == REVISION
            assert transcribed.segments[0]["start_sample"] == 100
            opened = await client.call(
                _request("stream_open", {"stream_id": "s", "sample_rate": 16000, "channels": 1})
            )
            assert opened.result["opened"]
            pushed = await client.call(
                _request("stream_push", {"stream_id": "s", "pcm_s16le": b"\0\0" * 160})
            )
            assert pushed.result["accepted_samples"] == 160
            closed = await client.call(_request("stream_close", {"stream_id": "s"}))
            assert closed.result["total_samples"] == 160
            unloaded = await client.call(_request("unload", {}))
            assert unloaded.ok

    asyncio.run(scenario())


def test_rpc_rejects_writable_or_out_of_root_batch_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.wav"
        outside.write_bytes(b"audio")
        outside.chmod(0o400)
        writable = root / "writable.wav"
        writable.write_bytes(b"audio")
        adapter = StatefulAdapter(
            "test", capabilities=("asr",), supported_methods=("transcribe_batch",)
        )
        socket_path = tmp_path / "worker.sock"
        async with RPCServer(socket_path, adapter, allowed_data_roots=(root,)):
            client = RPCClient(socket_path)
            for index, path in enumerate((outside, writable)):
                response = await client.call(
                    _request(
                        "transcribe_batch",
                        {
                            "audio_path": str(path),
                            "start_sample": 0,
                            "end_sample": 100,
                            "sample_rate": 16000,
                        },
                        f"bad-{index}",
                    )
                )
                assert not response.ok
                assert response.error_code == "invalid_request"

    asyncio.run(scenario())


def test_rpc_deadline_and_cross_connection_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = tmp_path / "model"
        model.mkdir()
        adapter = StatefulAdapter(
            "slow", capabilities=("asr",), supported_methods=("transcribe_batch",)
        )

        async def slow(_params: Any, cancelled: asyncio.Event) -> dict[str, Any]:
            while not cancelled.is_set():
                await asyncio.sleep(0.01)
            return {}

        adapter.register("transcribe_batch", slow)
        socket_path = tmp_path / "slow.sock"
        async with RPCServer(socket_path, adapter):
            client = RPCClient(socket_path)
            await client.call(
                _request(
                    "load",
                    {"model_id": "slow", "model_revision": REVISION, "model_path": str(model)},
                )
            )
            audio_params = {
                "audio_path": str(tmp_path / "missing.wav"),
                "start_sample": 0,
                "end_sample": 10,
                "sample_rate": 16000,
            }
            # Use a non-batch handler so path validation cannot mask cancellation.
            adapter.register("stream_flush", slow)
            adapter._supported = frozenset((*adapter._supported, "stream_flush"))
            adapter._streams["s"] = bytearray()
            running = asyncio.create_task(
                client.call(_request("stream_flush", {"stream_id": "s"}, "target"))
            )
            await asyncio.sleep(0.03)
            cancelled = await client.call(
                RPCRequest(
                    "cancel",
                    "job-1",
                    1000,
                    Priority.DICTATION,
                    "cancel",
                    {"target_request_id": "target"},
                )
            )
            assert cancelled.result["cancelled"]
            result = await running
            assert result.error_code == "cancelled"

            timeout_request = RPCRequest(
                "timeout",
                "job-1",
                20,
                Priority.CLASSROOM_PRIMARY,
                "stream_flush",
                {"stream_id": "s"},
            )
            with pytest.raises(ProtocolError, match="deadline exceeded"):
                await client.call(timeout_request)
            assert audio_params["sample_rate"] == 16_000

    asyncio.run(scenario())
