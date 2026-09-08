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
        self.verifications = 0

    def resolve_for_runtime(self, _model_id: str) -> Path:
        self.verifications += 1
        return self.path

    def installed_revision_metadata(self, _model_id: str) -> Path:
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
    assert process.calls[0].params["device"] == "cuda:0"
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
    monkeypatch.setattr(inference, "nvidia_devices", lambda: ())
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
    assert Process.instances[0].calls[0].params["device"] == "auto"
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


def test_process_heartbeat_is_observed_without_fabricating_model_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.activity import ActivityReporter, activity_scope
    from classscribe.models import inference

    class SlowProcess(Process):
        async def call(self, request: RPCRequest) -> RPCResponse:
            if request.method == "transcribe_batch":
                await asyncio.sleep(1.1)
            return await super().call(request)

    SlowProcess.load_ok = True
    monkeypatch.setattr(inference, "WorkerProcess", SlowProcess)
    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    model = tmp_path / entry.revision
    model.mkdir()
    invoker = SandboxedModelInvoker(
        cast(ModelManager, InstalledModel(model)),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, Sandbox()),
    )
    events: list[dict[str, Any]] = []
    with activity_scope(ActivityReporter(lambda **payload: events.append(payload))):
        asyncio.run(
            invoker(
                entry,
                RPCRequest(
                    "request",
                    "job",
                    5000,
                    Priority.BACKGROUND,
                    "transcribe_batch",
                    {
                        "audio_path": str(audio_file(tmp_path / "input.wav")),
                        "start_sample": 0,
                        "end_sample": 160,
                        "sample_rate": 16000,
                    },
                ),
            )
        )
    operations = [event["operation"] for event in events]
    for operation in (
        "waiting_resource",
        "verify_model",
        "prepare_environment",
        "load_model",
        "process_audio",
        "release_model",
        "model_released",
    ):
        assert operation in operations
    processing = [event for event in events if event["operation"] == "process_audio"]
    assert len(processing) >= 2
    assert processing[-1]["process_alive"] is True
    assert processing[-1]["progress_at"] == processing[0]["progress_at"]
    assert all("completed" not in event for event in processing)
    assert events[-1]["process_alive"] is False


def test_job_lease_reuses_a_model_across_event_loops_and_closes_at_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.activity import ActivityReporter, activity_scope
    from classscribe.models import inference

    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    model = tmp_path / entry.revision
    model.mkdir()
    manager = InstalledModel(model)
    Process.load_ok = True
    Process.instances.clear()
    monkeypatch.setattr(inference, "WorkerProcess", Process)
    handoffs: list[str] = []
    invoker = SandboxedModelInvoker(
        cast(ModelManager, manager),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, Sandbox()),
        before_gpu_use=lambda: handoffs.append("gpu"),
    )
    audio = audio_file(tmp_path / "lecture.wav")
    events: list[dict[str, Any]] = []
    invoker.open_job("job")
    try:
        for index in range(4):
            with activity_scope(ActivityReporter(lambda **payload: events.append(payload))):
                response = asyncio.run(
                    invoker(
                        entry,
                        RPCRequest(
                            str(index),
                            "job",
                            5000,
                            Priority.BACKGROUND,
                            "transcribe_batch",
                            {
                                "audio_path": str(audio),
                                "start_sample": 0,
                                "end_sample": 160,
                                "sample_rate": 16000,
                            },
                        ),
                    )
                )
                assert response.ok
        assert manager.verifications == 1
        assert handoffs == ["gpu"]
        assert len(Process.instances) == 1
        assert [r.method for r in Process.instances[0].calls] == ["load"] + ["transcribe_batch"] * 4
        assert sum(e["operation"] == "reuse_model" for e in events) == 3
        assert not Process.instances[0].stopped
        assert invoker.uses_gpu()
    finally:
        invoker.close_job("job")
    assert Process.instances[0].calls[-1].method == "unload"
    assert Process.instances[0].stopped
    assert invoker._loop is None
    assert not invoker.uses_gpu()


def test_reuse_checks_revision_and_releases_stale_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.models import inference

    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    model = tmp_path / entry.revision
    model.mkdir()
    manager = InstalledModel(model)
    Process.load_ok = True
    Process.instances.clear()
    monkeypatch.setattr(inference, "WorkerProcess", Process)
    invoker = SandboxedModelInvoker(
        cast(ModelManager, manager),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, Sandbox()),
    )
    request = RPCRequest("test", "job", 5000, Priority.BACKGROUND, "health", {})
    invoker.open_job("job")
    try:
        asyncio.run(invoker(entry, request))
        manager.path = tmp_path / ("0" * 40)
        manager.path.mkdir()
        with pytest.raises(ProtocolError, match="revision differs"):
            asyncio.run(invoker(entry, request))
        assert Process.instances[0].stopped
        assert not invoker._sessions
    finally:
        invoker.close_job("job")


def test_cpu_vad_does_not_wait_for_or_preempt_the_gpu_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from classscribe.models import inference

    registry = load_registry(Path("config/model-registry.v1.yaml"))
    gpu = registry.model("qwen3_asr_1_7b")
    cpu = registry.model("firered_vad")
    gpu_started = threading.Event()
    release = threading.Event()

    class Manager:
        def resolve_for_runtime(self, model_id: str) -> Path:
            path = tmp_path / registry.model(model_id).revision
            path.mkdir(exist_ok=True)
            return path

    class BusyProcess(Process):
        async def call(self, request: RPCRequest) -> RPCResponse:
            if request.method == "transcribe_batch":
                gpu_started.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
            return await super().call(request)

    Process.load_ok = True
    monkeypatch.setattr(inference, "WorkerProcess", BusyProcess)
    handoffs: list[str] = []
    sandbox = Sandbox()
    invoker = SandboxedModelInvoker(
        cast(ModelManager, Manager()),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, sandbox),
        before_gpu_use=lambda: handoffs.append("gpu"),
    )
    params = {
        "audio_path": str(audio_file(tmp_path / "lecture.wav")),
        "start_sample": 0,
        "end_sample": 160,
        "sample_rate": 16000,
    }

    async def scenario() -> None:
        task = asyncio.create_task(
            invoker(
                gpu, RPCRequest("gpu", "job", 5000, Priority.BACKGROUND, "transcribe_batch", params)
            )
        )
        try:
            async with asyncio.timeout(2):
                while not gpu_started.is_set():
                    await asyncio.sleep(0.01)
            result = await asyncio.wait_for(
                invoker(cpu, RPCRequest("cpu", "job", 5000, Priority.BACKGROUND, "vad", params)),
                timeout=2,
            )
            assert result.ok
            assert sandbox.arguments["gpu_devices"] == ()
            assert handoffs == ["gpu"]
            cpu_process = invoker._sessions["cpu"].process
            gpu_process = invoker._sessions["gpu"].process
            invoker.release_cpu("job")
            assert not cpu_process.running
            assert gpu_process.running
        finally:
            release.set()
            await task

    invoker.open_job("job")
    try:
        asyncio.run(scenario())
    finally:
        invoker.close_job("job")
    assert invoker._loop is None


def test_different_jobs_do_not_share_model_state_and_failed_rpc_evicts_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.models import inference

    class FailingProcess(Process):
        async def call(self, request: RPCRequest) -> RPCResponse:
            if request.request_id == "bad":
                return RPCResponse(
                    request.request_id,
                    request.job_id,
                    False,
                    "fixture",
                    "f" * 40,
                    error_code="internal",
                )
            return await super().call(request)

    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    model = tmp_path / entry.revision
    model.mkdir()
    manager = InstalledModel(model)
    Process.load_ok = True
    Process.instances.clear()
    monkeypatch.setattr(inference, "WorkerProcess", FailingProcess)
    invoker = SandboxedModelInvoker(
        cast(ModelManager, manager),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, Sandbox()),
    )
    invoker.open_job("first")
    invoker.open_job("second")
    try:
        for identifier in ("first", "second"):
            asyncio.run(
                invoker(
                    entry,
                    RPCRequest(identifier, identifier, 5000, Priority.BACKGROUND, "health", {}),
                )
            )
        assert manager.verifications == 2
        assert Process.instances[0].stopped
        invoker.close_job("first")
        assert not Process.instances[1].stopped
        response = asyncio.run(
            invoker(entry, RPCRequest("bad", "second", 5000, Priority.BACKGROUND, "health", {}))
        )
        assert not response.ok
        assert Process.instances[1].stopped
        asyncio.run(
            invoker(entry, RPCRequest("retry", "second", 5000, Priority.BACKGROUND, "health", {}))
        )
        assert manager.verifications == 3
    finally:
        invoker.close()
    assert all(process.stopped for process in Process.instances)
    assert invoker._loop is None
    assert not tuple((tmp_path / "runtime").glob("*-output"))
    assert not tuple((tmp_path / "runtime").glob("*-silence.wav"))


def test_cancelled_gpu_waiter_does_not_load_or_evict_another_jobs_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from classscribe.jobs.state_machine import CheckpointInterrupted
    from classscribe.models import inference

    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    class Busy(Process):
        async def call(self, request: RPCRequest) -> RPCResponse:
            if request.request_id == "first":
                started.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
            return await super().call(request)

    def boundary() -> None:
        if cancelled.is_set():
            raise CheckpointInterrupted()

    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    model = tmp_path / entry.revision
    model.mkdir()
    manager = InstalledModel(model)
    Process.load_ok = True
    Process.instances.clear()
    monkeypatch.setattr(inference, "WorkerProcess", Busy)
    invoker = SandboxedModelInvoker(
        cast(ModelManager, manager),
        cast(WorkerEnvironmentProvisioner, ProvisionedEnvironment(environment(tmp_path))),
        tmp_path / "runtime",
        sandbox=cast(Any, Sandbox()),
    )
    invoker.open_job("first")
    invoker.open_job("second", boundary=boundary)

    async def scenario() -> None:
        first = asyncio.create_task(
            invoker(entry, RPCRequest("first", "first", 5000, Priority.BACKGROUND, "health", {}))
        )
        try:
            async with asyncio.timeout(2):
                while not started.is_set():
                    await asyncio.sleep(0.01)
            second = asyncio.create_task(
                invoker(
                    entry, RPCRequest("second", "second", 5000, Priority.BACKGROUND, "health", {})
                )
            )
            await asyncio.sleep(0.02)
            cancelled.set()
            release.set()
            await first
            with pytest.raises(CheckpointInterrupted):
                await second
            assert manager.verifications == 1
            assert Process.instances[0].running
        finally:
            release.set()
            await first

    try:
        asyncio.run(scenario())
    finally:
        invoker.close()
