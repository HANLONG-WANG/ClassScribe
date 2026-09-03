from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest
from classscribe.models import load_registry
from classscribe.models.health import _inference_request, _run_inference
from classscribe_protocol import RPCRequest, RPCResponse


def wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x01\x00" * 8_000)
    path.chmod(0o400)
    return path


def registry() -> object:
    return load_registry(Path("config/model-registry.v1.yaml"))


def test_health_requests_execute_real_model_methods_with_strict_contracts(tmp_path: Path) -> None:
    models = load_registry(Path("config/model-registry.v1.yaml"))
    audio = wav(tmp_path / "health.wav")
    asr = _inference_request(models.model("qwen3_asr_0_6b"), audio, "ja", None)
    assert asr.method == "transcribe_batch"
    assert asr.params["request_contract"] == "body-asr-v1"
    assert asr.params["manual_language"] is True
    assert asr.params["decode"] == {
        "temperature": 0.0,
        "do_sample": False,
        "batch_size": 1,
        "mixed_length_batch": False,
        "max_new_tokens": 50,
        "max_output_characters": 200,
        "seed": 0,
    }
    diarization = _inference_request(models.model("pyannote_community_1"), audio, "en", None)
    assert diarization.method == "diarize"
    with pytest.raises(ValueError, match="exact transcript"):
        _inference_request(models.model("qwen3_forced_aligner_0_6b"), audio, "ja", None)
    alignment = _inference_request(
        models.model("qwen3_forced_aligner_0_6b"), audio, "ja", "音声確認"
    )
    assert alignment.method == "align"
    assert alignment.params["quality_gate"]["no_decode_loop"] is True


class StreamingWorker:
    def __init__(self, revision: str) -> None:
        self.revision = revision
        self.calls: list[RPCRequest] = []

    async def call(self, request: RPCRequest) -> RPCResponse:
        self.calls.append(request)
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id="voxtral_mini_4b_realtime_2602",
            model_revision=self.revision,
            raw_text="health output" if request.method == "stream_flush" else "",
            normalized_text="health output" if request.method == "stream_flush" else "",
            result={"accepted": True},
        )

    async def start(self, *, timeout_seconds: float = 15.0) -> None:
        del timeout_seconds

    async def stop(self, *, grace_seconds: float = 3.0) -> None:
        del grace_seconds


def test_streaming_only_health_check_pushes_audio_and_flushes(tmp_path: Path) -> None:
    entry = load_registry(Path("config/model-registry.v1.yaml")).model(
        "voxtral_mini_4b_realtime_2602"
    )
    worker = StreamingWorker(entry.revision)
    response = asyncio.run(_run_inference(worker, entry, wav(tmp_path / "health.wav"), "ja", None))
    assert response.raw_text == "health output"
    methods = [item.method for item in worker.calls]
    assert methods[0] == "stream_open"
    assert "stream_push" in methods
    assert methods[-2:] == ["stream_flush", "stream_close"]
