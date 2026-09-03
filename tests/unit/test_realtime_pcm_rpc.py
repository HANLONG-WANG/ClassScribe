from __future__ import annotations

import asyncio
import stat
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from classscribe_protocol import Priority, RPCRequest, RPCServer
from classscribe_protocol.adapter import StatefulAdapter


class _BatchAdapter:
    worker_id = "batch-final"
    model_id = "batch-final-model"
    model_revision = "a" * 40
    capabilities = ("asr_ja",)

    def __init__(self) -> None:
        self.audio_path: Path | None = None
        self.params: Mapping[str, Any] = {}

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        assert method == "transcribe_batch"
        assert not cancelled.is_set()
        self.params = params
        self.audio_path = Path(str(params["audio_path"]))
        assert stat.S_IMODE(self.audio_path.stat().st_mode) == 0o400
        with wave.open(str(self.audio_path), "rb") as source:
            assert source.getframerate() == 16_000
            assert source.getnchannels() == 1
            assert source.getnframes() == 320
        return {
            "raw_text": "今日は",
            "normalized_text": "今日は",
            "language": "ja",
            "segments": [
                {
                    "start_sample": 0,
                    "end_sample": 320,
                    "text": "今日は",
                    "words": [{"start_sample": 0, "end_sample": 320, "text": "今日は"}],
                }
            ],
        }


def test_transcribe_pcm_materializes_only_ephemeral_bounded_batch_audio(tmp_path: Path) -> None:
    adapter = _BatchAdapter()
    server = RPCServer(tmp_path / "unused.sock", adapter)
    request = RPCRequest(
        "accuracy",
        "dictation:session",
        5000,
        Priority.DICTATION,
        "transcribe_pcm",
        {
            "pcm_s16le": b"\0\0" * 320,
            "absolute_start_sample": 16_000,
            "core_start_sample": 16_080,
            "end_sample": 16_320,
            "sample_rate": 16_000,
            "channels": 1,
            "language": "ja",
            "rolling_context": ["前の文"],
        },
    )
    response = asyncio.run(server._dispatch(request))
    assert response.ok
    assert response.segments[0]["start_sample"] == 16_000
    assert response.segments[0]["words"][0]["end_sample"] == 16_320
    assert adapter.params["core_start_sample"] == 80
    assert adapter.params["manual_language"] is True
    assert adapter.audio_path is not None and not adapter.audio_path.exists()


def test_transcribe_pcm_rejects_mismatched_or_oversized_audio() -> None:
    common = {
        "absolute_start_sample": 0,
        "core_start_sample": 0,
        "end_sample": 2,
        "sample_rate": 16_000,
        "channels": 1,
        "language": "ja",
        "rolling_context": [],
    }
    for pcm in (b"\0\0", b"\0\0" * (16_000 * 30 + 1)):
        try:
            RPCRequest(
                "bad",
                "dictation:session",
                5000,
                Priority.DICTATION,
                "transcribe_pcm",
                {**common, "pcm_s16le": pcm},
            )
        except ValueError:
            continue
        raise AssertionError("unsafe PCM payload was accepted")


def test_batch_worker_capabilities_expose_server_pcm_bridge(tmp_path: Path) -> None:
    adapter = StatefulAdapter(
        "batch-final",
        capabilities=("asr_ja",),
        supported_methods=("transcribe_batch",),
    )
    server = RPCServer(tmp_path / "unused.sock", adapter)
    request = RPCRequest(
        "capabilities",
        "dictation:session",
        5000,
        Priority.DICTATION,
        "capabilities",
        {},
    )

    response = asyncio.run(server._dispatch(request))

    assert response.ok
    assert response.result["methods"] == ["transcribe_batch", "transcribe_pcm"]
