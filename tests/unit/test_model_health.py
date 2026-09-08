from __future__ import annotations

import asyncio
import os
import wave
from pathlib import Path
from typing import Any, cast

import pytest
from classscribe.models import WorkerEnvironmentProvisioner, load_registry
from classscribe.models.health import (
    InstalledModelHealthChecker,
    _inference_request,
    _run_inference,
)
from classscribe.models.worker_process import WorkerProcessSpec
from classscribe.worker_sandbox import WorkerSandbox
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


class LifecycleWorker(StreamingWorker):
    def __init__(self, revision: str) -> None:
        super().__init__(revision)
        self.stopped = False

    async def call(self, request: RPCRequest) -> RPCResponse:
        self.calls.append(request)
        text = "real health output" if request.method == "transcribe_batch" else ""
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id="whisper_tiny_reference",
            model_revision=self.revision,
            raw_text=text,
            normalized_text=text,
            segments=(({"start_sample": 0, "end_sample": 8_000, "text": text},) if text else ()),
            metrics={"backend": "faster_whisper_cpu_int8"},
            result={"loaded": request.method == "load"},
        )

    async def stop(self, *, grace_seconds: float = 3.0) -> None:
        del grace_seconds
        self.stopped = True


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


def test_installed_health_checker_explicitly_loads_infers_and_unloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = load_registry(Path("config/model-registry.v1.yaml")).model("whisper_tiny_reference")
    audio = wav(tmp_path / "health.wav")
    worker = LifecycleWorker(entry.revision)
    monkeypatch.setattr("classscribe.models.health.WorkerProcess", lambda _spec: worker)
    checker = object.__new__(InstalledModelHealthChecker)
    spec = WorkerProcessSpec(
        worker_id="moss_en",
        command=("worker",),
        socket_path=(tmp_path / "worker.sock").absolute(),
        data_roots=(tmp_path.absolute(),),
    )

    result = asyncio.run(
        checker._run(
            spec,
            entry,
            audio,
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
            },
            "en",
            None,
        )
    )

    assert result.healthy is True
    assert result.environment["runtime_offline"] is True
    assert [request.method for request in worker.calls] == [
        "load",
        "transcribe_batch",
        "unload",
    ]
    assert worker.stopped is True


def test_health_checker_uses_bounded_socket_name_without_model_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = load_registry(Path("config/model-registry.v1.yaml")).model("whisper_tiny_reference")
    project = tmp_path / "worker"
    (project / ".venv/bin").mkdir(parents=True)
    (project / ".venv/bin/python").write_bytes(b"python")
    (project / "worker.py").write_text("worker", encoding="utf-8")
    audio = wav(tmp_path / "health.wav")
    model = tmp_path / "model"
    model.mkdir()
    worker = LifecycleWorker(entry.revision)
    captured: list[WorkerProcessSpec] = []

    class Provisioner:
        def inspect(self, _worker_id: str) -> dict[str, str]:
            return {"lock_sha256": "lock", "source_sha256": "source"}

        def ensure(self, _worker_id: str) -> Path:
            return project

    class Sandbox:
        def command(self, **_kwargs: Any) -> tuple[str, ...]:
            return ("worker",)

    def process(spec: WorkerProcessSpec) -> LifecycleWorker:
        captured.append(spec)
        return worker

    monkeypatch.setattr("classscribe.models.health.WorkerProcess", process)
    runtime = tmp_path / ("r" * 50)
    checker = InstalledModelHealthChecker(
        cast(WorkerEnvironmentProvisioner, Provisioner()),
        runtime,
        sandbox=cast(WorkerSandbox, Sandbox()),
    )

    result = checker(
        entry,
        model,
        audio,
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        },
        "en",
        None,
    )

    assert result.healthy is True
    assert result.environment["worker_source_sha256"] == "source"
    assert result.environment["worker_lock_sha256"] == "lock"
    assert result.environment["requested_device"] == "auto"
    assert len(captured) == 1
    assert captured[0].socket_path.name.startswith("h-")
    assert "whisper_tiny_reference" not in captured[0].socket_path.name
    assert len(os.fsencode(captured[0].socket_path.name)) == len("h-.sock") + 16
