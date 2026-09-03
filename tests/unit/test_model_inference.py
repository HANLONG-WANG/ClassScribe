from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from classscribe.models import (
    ModelManager,
    SandboxedModelInvoker,
    WorkerEnvironmentProvisioner,
    load_registry,
)
from classscribe.models.worker_process import WorkerProcessSpec
from classscribe_protocol import Priority, ProtocolError, RPCRequest, RPCResponse


class InstalledModel:
    def __init__(self, path: Path) -> None:
        self.path = path

    def resolve_for_runtime(self, _model_id: str) -> Path:
        return self.path


class ProvisionedEnvironment:
    def __init__(self, path: Path) -> None:
        self.path = path

    def resolve(self, _worker_id: str) -> Path:
        return self.path


class Sandbox:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def command(self, **kwargs: Any) -> tuple[str, ...]:
        self.arguments = kwargs
        return ("/bin/true",)


class Process:
    instances: ClassVar[list[Process]] = []
    load_ok: ClassVar[bool] = True

    def __init__(self, spec: WorkerProcessSpec) -> None:
        self.spec = spec
        self.running = False
        self.calls: list[RPCRequest] = []
        self.stopped = False
        type(self).instances.append(self)

    async def start(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds == 120
        self.running = True

    async def call(self, request: RPCRequest) -> RPCResponse:
        self.calls.append(request)
        if request.method == "load" and not self.load_ok:
            return RPCResponse(
                request.request_id,
                request.job_id,
                False,
                "fixture",
                "f" * 40,
                error_code="model_load_failed",
                error_detail="fixture failure",
            )
        return RPCResponse(
            request.request_id,
            request.job_id,
            True,
            "fixture",
            "f" * 40,
            normalized_text="verified",
        )

    async def stop(self) -> None:
        self.running = False
        self.stopped = True


def environment(tmp_path: Path) -> Path:
    project = tmp_path / "environment"
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("python", encoding="utf-8")
    (project / "worker.py").write_text("worker", encoding="utf-8")
    return project


def audio_file(path: Path) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * 160)
    return path


def test_sandboxed_invoker_runs_complete_offline_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import classscribe.models.inference as inference

    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    model = tmp_path / entry.revision
    model.mkdir()
    audio = audio_file(tmp_path / "audio.wav")
    sandbox = Sandbox()
    handoffs: list[str] = []
    Process.instances.clear()
    Process.load_ok = True
    monkeypatch.setattr(inference, "WorkerProcess", Process)
    monkeypatch.setattr(inference, "nvidia_devices", lambda: (Path("/dev/nvidia0"),))
    invoker = SandboxedModelInvoker(
        cast(ModelManager, InstalledModel(model)),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, sandbox),
        before_gpu_use=lambda: handoffs.append("ready"),
    )
    request = RPCRequest(
        "request",
        "job",
        5000,
        Priority.CLASSROOM_PRIMARY,
        "transcribe_batch",
        {
            "audio_path": str(audio),
            "start_sample": 0,
            "end_sample": 160,
            "sample_rate": 16_000,
        },
    )

    response = asyncio.run(invoker(entry, request))

    assert response.normalized_text == "verified"
    assert handoffs == ["ready"]
    process = Process.instances[0]
    assert [item.method for item in process.calls] == ["load", "transcribe_batch", "unload"]
    assert process.calls[1].params["audio_path"] == "/input/audio"
    assert process.stopped
    assert sandbox.arguments["model_revision"] == model
    assert sandbox.arguments["input_audio"] == audio
    assert sandbox.arguments["gpu_devices"] == (Path("/dev/nvidia0"),)
    assert not any((tmp_path / "runtime").glob("*-output"))


def test_invoker_cleans_synthetic_input_and_reports_load_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import classscribe.models.inference as inference

    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    model = tmp_path / entry.revision
    model.mkdir()
    Process.instances.clear()
    Process.load_ok = False
    monkeypatch.setattr(inference, "WorkerProcess", Process)
    invoker = SandboxedModelInvoker(
        cast(ModelManager, InstalledModel(model)),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, Sandbox()),
    )
    request = RPCRequest("health", "job", 1000, Priority.BACKGROUND, "health", {})

    with pytest.raises(ProtocolError, match="fixture failure"):
        asyncio.run(invoker(entry, request))

    assert [item.method for item in Process.instances[0].calls] == ["load", "unload"]
    assert Process.instances[0].stopped
    assert not tuple((tmp_path / "runtime").glob("*-silence.wav"))
    assert not tuple((tmp_path / "runtime").glob("*-output"))


def test_invoker_rejects_revision_drift_and_noncanonical_audio(tmp_path: Path) -> None:
    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    wrong = tmp_path / ("0" * 40)
    wrong.mkdir()
    invoker = SandboxedModelInvoker(
        cast(ModelManager, InstalledModel(wrong)),
        cast(WorkerEnvironmentProvisioner, object()),
        tmp_path / "runtime",
    )
    request = RPCRequest("health", "job", 1000, Priority.BACKGROUND, "health", {})
    with pytest.raises(ProtocolError, match="revision differs"):
        asyncio.run(invoker(entry, request))

    canonical = audio_file(tmp_path / "canonical.wav")
    linked = tmp_path / "linked.wav"
    linked.symlink_to(canonical)
    with pytest.raises(ProtocolError, match="canonical"):
        invoker._input_audio(
            RPCRequest(
                "audio",
                "job",
                1000,
                Priority.BACKGROUND,
                "vad",
                {
                    "audio_path": str(linked),
                    "start_sample": 0,
                    "end_sample": 160,
                    "sample_rate": 16_000,
                },
            ),
            "fixture",
        )
