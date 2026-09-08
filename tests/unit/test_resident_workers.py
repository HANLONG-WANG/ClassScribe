from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from classscribe.config import load_config
from classscribe.models import (
    DictationWorkerSupervisor,
    ModelManager,
    SandboxedModelInvoker,
    WorkerEnvironmentProvisioner,
    load_registry,
)
from classscribe.paths import AppPaths
from classscribe_protocol import (
    Priority,
    ResidentAccuracyRoute,
    ResidentWorkerManifest,
    ResidentWorkerRoute,
    RPCRequest,
    RPCResponse,
    load_resident_worker_manifest,
)
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


class InstalledModels:
    def __init__(self, root: Path, revisions: dict[str, str]) -> None:
        self.root = root
        self.revisions = revisions
        self.verifications: list[str] = []
        for model_id, revision in revisions.items():
            (root / model_id / revision).mkdir(parents=True)

    def resolve_for_runtime(self, model_id: str) -> Path:
        self.verifications.append(model_id)
        return self.installed_revision_metadata(model_id)

    def installed_revision_metadata(self, model_id: str) -> Path:
        try:
            revision = self.revisions[model_id]
        except KeyError as exc:
            raise FileNotFoundError(model_id) from exc
        return self.root / model_id / revision

    def revision_fingerprint(
        self, model_id: str
    ) -> tuple[tuple[str, int, int, int, int, int], ...]:
        path = self.installed_revision_metadata(model_id)
        metadata = path.stat()
        return (
            (
                str(path),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            ),
        )


def test_resident_manifest_is_same_user_bounded_and_strict(tmp_path: Path) -> None:
    workers = tmp_path / "workers"
    workers.mkdir()
    socket_path = workers / "qwen.sock"
    transport = socket.socket(socket.AF_UNIX)
    transport.bind(str(socket_path))
    try:
        manifest = ResidentWorkerManifest(
            True,
            "qwen",
            (ResidentWorkerRoute("qwen", "a" * 40, socket_path),),
            {"ibus.ja.balanced": "qwen"},
            socket_path,
            None,
            socket_path,
            (),
            "2026-09-04T00:00:00+00:00",
            {"ja": ResidentAccuracyRoute("qwen", "a" * 40, socket_path, None)},
        )
        path = tmp_path / "resident-workers.json"
        path.write_text(json.dumps(manifest.as_dict()), encoding="utf-8")
        path.chmod(0o600)
        assert load_resident_worker_manifest(path) == manifest

        unsafe = tmp_path / "linked.json"
        unsafe.symlink_to(path)
        with pytest.raises(ValueError, match="unsafe"):
            load_resident_worker_manifest(unsafe)
        with pytest.raises(ValueError, match="unavailable route"):
            ResidentWorkerManifest(
                False,
                None,
                (),
                {"ibus.ja.fast": "missing"},
                None,
                None,
                None,
                (),
                "now",
            )
        invalid_version = manifest.as_dict()
        invalid_version["protocol_version"] = True
        with pytest.raises(ValueError, match="versions must be integers"):
            ResidentWorkerManifest.from_dict(invalid_version)
        invalid_timestamp = manifest.as_dict()
        invalid_timestamp["generated_at"] = None
        with pytest.raises(ValueError, match="generation time"):
            ResidentWorkerManifest.from_dict(invalid_timestamp)
        hot_switch = ResidentAccuracyRoute("granite", "b" * 40, None, 4200.0)
        assert ResidentAccuracyRoute.from_dict(hot_switch.as_dict()) == hot_switch
        with pytest.raises(ValueError, match="positive load estimate"):
            ResidentAccuracyRoute("granite", "b" * 40, None, None)
        with pytest.raises(ValueError, match="must not advertise"):
            ResidentAccuracyRoute("granite", "b" * 40, socket_path, 1.0)
        malformed = hot_switch.as_dict()
        malformed["unexpected"] = "field"
        with pytest.raises(ValueError, match="unknown accuracy route fields"):
            ResidentAccuracyRoute.from_dict(malformed)
    finally:
        transport.close()


def test_resident_planner_prefers_default_profile_and_enforces_vram_budget(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, sessions, _ = database
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    qwen = registry.model("qwen3_asr_1_7b")
    nemotron = registry.model("nemotron_3_5_asr_streaming_0_6b")
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    manager = InstalledModels(
        tmp_path / "models",
        {qwen.id: qwen.revision, nemotron.id: nemotron.revision},
    )
    supervisor = DictationWorkerSupervisor(
        paths,
        load_config(environment={}),
        registry,
        cast(ModelManager, manager),
        cast(WorkerEnvironmentProvisioner, object()),
        sessions,
    )
    routes, selected = supervisor.planned_profile_models()
    assert [item.id for item in selected] == [qwen.id]
    assert routes["ibus.ja.balanced"] == qwen.id
    assert routes["ibus.ja.fast"] == qwen.id


def test_resident_workers_are_unloaded_for_classroom_and_refresh_waits_for_idle(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    supervisor = DictationWorkerSupervisor(
        paths,
        load_config(environment={}),
        load_registry(Path("config/model-registry.v1.yaml")),
        cast(ModelManager, object()),
        cast(WorkerEnvironmentProvisioner, object()),
        sessions,
    )
    started = 0

    async def start_workers() -> ResidentWorkerManifest:
        nonlocal started
        started += 1
        socket_path = paths.runtime / "workers" / "qwen.sock"
        return ResidentWorkerManifest(
            True,
            "qwen",
            (ResidentWorkerRoute("qwen", "a" * 40, socket_path),),
            {"ibus.ja.fast": "qwen"},
            socket_path,
            None,
            socket_path,
            (),
            "2026-09-04T00:00:00+00:00",
        )

    monkeypatch.setattr(supervisor, "_start_locked", start_workers)
    monkeypatch.setattr(supervisor, "_catalog", lambda: ())

    async def scenario() -> None:
        suspended = await supervisor.suspend_for_classroom()
        assert not suspended.ready
        assert "classroom" in suspended.errors[0]
        assert not (await supervisor.refresh()).ready

        active = await supervisor.resume_for_dictation()
        assert active.ready
        assert started == 1
        deferred = await supervisor.refresh()
        assert not deferred.ready
        assert "deferred" in deferred.errors[0]
        assert started == 1
        with pytest.raises(RuntimeError, match="active dictation"):
            await supervisor.suspend_for_classroom()

        released = await supervisor.end_dictation()
        assert not released.ready
        assert "released" in released.errors[0]

    asyncio.run(scenario())


def test_production_invoker_hands_off_resident_gpu_before_model_resolution(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    invoker = SandboxedModelInvoker(
        cast(ModelManager, InstalledModels(tmp_path / "models", {})),
        cast(WorkerEnvironmentProvisioner, object()),
        tmp_path / "runtime",
        before_gpu_use=lambda: calls.append("resident-unloaded"),
    )
    entry = load_registry(Path("config/model-registry.v1.yaml")).model("qwen3_asr_1_7b")
    request = RPCRequest(
        "request",
        "job",
        1000,
        Priority.CLASSROOM_PRIMARY,
        "health",
        {},
    )

    async def scenario() -> None:
        with pytest.raises(FileNotFoundError):
            await invoker(entry, request)

    asyncio.run(scenario())
    assert calls == ["resident-unloaded"]


def test_accuracy_hot_switch_replaces_gpu_residents_and_publishes_ready_route(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sessions, _ = database
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    target = registry.model("granite_speech_4_1_2b")
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    supervisor = DictationWorkerSupervisor(
        paths,
        load_config(environment={}),
        registry,
        cast(ModelManager, object()),
        cast(WorkerEnvironmentProvisioner, object()),
        sessions,
    )
    primary_socket = paths.runtime / "workers" / "primary.sock"
    manifest = ResidentWorkerManifest(
        True,
        "primary",
        (ResidentWorkerRoute("primary", "a" * 40, primary_socket),),
        {"ibus.ja.accuracy": "primary"},
        paths.runtime / "workers" / "vad.sock",
        None,
        None,
        (),
        "2026-09-04T00:00:00+00:00",
        {"ja": ResidentAccuracyRoute(target.id, target.revision, None, 4200.0)},
    )
    supervisor._dictation_active = True
    supervisor._current_manifest = manifest
    events: list[str] = []

    async def stop_gpu() -> None:
        events.append("streaming-unloaded")

    async def start_entry(entry: Any, socket_path: Path, *, expose_gpu: bool = True) -> None:
        assert expose_gpu
        assert entry.id == target.id
        assert socket_path.name == "dictation-accuracy-ja.sock"
        events.append("accuracy-loaded")

    monkeypatch.setattr(supervisor, "_stop_gpu_locked", stop_gpu)
    monkeypatch.setattr(supervisor, "_start_entry", start_entry)
    switched = asyncio.run(supervisor.prepare_accuracy("ja"))
    route = switched.accuracy_routes["ja"]
    assert events == ["streaming-unloaded", "accuracy-loaded"]
    assert route.socket_path is not None
    assert route.expected_load_ms is None
    assert switched.accuracy_socket == route.socket_path
    assert switched.routes[-1].model_id == target.id


def test_accuracy_hot_switch_fails_closed_and_records_load_failure(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sessions, _ = database
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    target = registry.model("granite_speech_4_1_2b")
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    supervisor = DictationWorkerSupervisor(
        paths,
        load_config(environment={}),
        registry,
        cast(ModelManager, object()),
        cast(WorkerEnvironmentProvisioner, object()),
        sessions,
    )

    with pytest.raises(ValueError, match="concrete language"):
        asyncio.run(supervisor.prepare_accuracy("auto"))
    with pytest.raises(RuntimeError, match="active dictation"):
        asyncio.run(supervisor.prepare_accuracy("ja"))

    supervisor._dictation_active = True
    supervisor._current_manifest = supervisor._empty_manifest([])
    with pytest.raises(RuntimeError, match="no installed accuracy model"):
        asyncio.run(supervisor.prepare_accuracy("ja"))

    resident_socket = paths.runtime / "workers" / "granite.sock"
    resident = ResidentWorkerManifest(
        True,
        target.id,
        (ResidentWorkerRoute(target.id, target.revision, resident_socket),),
        {"ibus.ja.accuracy": target.id},
        paths.runtime / "workers" / "vad.sock",
        None,
        resident_socket,
        (),
        "2026-09-04T00:00:00+00:00",
        {"ja": ResidentAccuracyRoute(target.id, target.revision, resident_socket, None)},
    )
    supervisor._current_manifest = resident
    assert asyncio.run(supervisor.prepare_accuracy("ja")) is resident

    pending = ResidentWorkerManifest(
        False,
        None,
        (),
        {},
        None,
        None,
        None,
        (),
        "2026-09-04T00:00:00+00:00",
        {"ja": ResidentAccuracyRoute(target.id, target.revision, None, 4200.0)},
    )
    supervisor._current_manifest = pending

    async def stop_gpu() -> None:
        return None

    async def fail_start(_entry: Any, _socket: Path, *, expose_gpu: bool = True) -> None:
        assert expose_gpu
        raise RuntimeError("fixture load failure")

    monkeypatch.setattr(supervisor, "_stop_gpu_locked", stop_gpu)
    monkeypatch.setattr(supervisor, "_start_entry", fail_start)
    with pytest.raises(RuntimeError, match="fixture load failure"):
        asyncio.run(supervisor.prepare_accuracy("ja"))
    assert supervisor._current_manifest is not None
    assert "accuracy hot switch failed" in supervisor._current_manifest.errors[-1]


def test_accuracy_route_selection_and_load_estimate_use_installed_pinned_models(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, sessions, _ = database
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    qwen = registry.model("qwen3_asr_1_7b")
    granite = registry.model("granite_speech_4_1_2b")
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    manager = InstalledModels(tmp_path / "models", {qwen.id: qwen.revision})
    supervisor = DictationWorkerSupervisor(
        paths,
        load_config(environment={}),
        registry,
        cast(ModelManager, manager),
        cast(WorkerEnvironmentProvisioner, object()),
        sessions,
    )

    assert supervisor._installed_accuracy_entry("ja") == qwen
    assert supervisor._installed_accuracy_entry("auto") == qwen
    measured = qwen.model_copy(
        update={
            "benchmark": qwen.benchmark.model_copy(
                update={
                    "results": {
                        "cold": {"model_load_seconds": 1.25},
                        "invalid": {"model_load_seconds": False},
                    }
                }
            )
        }
    )
    assert supervisor._estimated_accuracy_load_ms(measured) == 1250.0
    assert supervisor._estimated_accuracy_load_ms(granite) == 5400.0


def test_gpu_stop_retains_cpu_auxiliary_worker_and_unloads_gpu_worker(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    class Process:
        def __init__(self, worker_id: str, *, running: bool = True) -> None:
            self.spec = type("Spec", (), {"worker_id": worker_id})()
            self.running = running
            self.calls: list[RPCRequest] = []
            self.stopped = False

        async def call(self, request: RPCRequest) -> RPCResponse:
            self.calls.append(request)
            return RPCResponse(request.request_id, request.job_id, True, "fixture", "f" * 40)

        async def stop(self) -> None:
            self.stopped = True

    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    supervisor = DictationWorkerSupervisor(
        paths,
        load_config(environment={}),
        load_registry(Path("config/model-registry.v1.yaml")),
        cast(ModelManager, object()),
        cast(WorkerEnvironmentProvisioner, object()),
        sessions,
    )
    cpu = Process("vad")
    gpu = Process("streaming")
    supervisor._processes = [
        (cast(Any, cpu), False),
        (cast(Any, gpu), True),
    ]

    asyncio.run(supervisor._stop_gpu_locked())

    assert len(supervisor._processes) == 1
    assert cast(Any, supervisor._processes[0][0]) is cpu
    assert supervisor._processes[0][1] is False
    assert [request.method for request in gpu.calls] == ["unload"]
    assert gpu.stopped
    assert not cpu.calls and not cpu.stopped


def test_resident_start_loads_only_current_route_and_required_auxiliaries(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, manager, processes = lifecycle_fixture(database, tmp_path, monkeypatch)
    manifest = asyncio.run(supervisor._start_locked())
    assert manifest.ready
    assert manifest.default_model_id == "qwen3_asr_1_7b"
    assert manifest.lid_socket is None
    assert manifest.accuracy_routes == {}
    assert [process.model_id for process in processes] == ["firered_vad", "qwen3_asr_1_7b"]
    assert sorted(manager.verifications) == ["firered_vad", "qwen3_asr_1_7b"]
    monkeypatch.setattr("classscribe.models.resident.shutil.which", lambda _name: None)
    unavailable = asyncio.run(supervisor._start_locked())
    assert not unavailable.ready
    assert unavailable.errors == ("Bubblewrap is unavailable",)
    assert not supervisor._processes


@pytest.mark.parametrize("model_id", ["qwen3_asr_1_7b", "firered_vad"])
def test_resident_entry_loads_and_unloads_in_sandbox(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import classscribe.models.resident as resident

    class Environment:
        def resolve(self, _worker: str) -> Path:
            project = tmp_path / "environment"
            python = project / ".venv/bin/python"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("python", encoding="utf-8")
            (project / "worker.py").write_text("worker", encoding="utf-8")
            return project

    class Sandbox:
        def __init__(self) -> None:
            self.arguments: dict[str, Any] = {}

        def command(self, **kwargs: Any) -> tuple[str, ...]:
            self.arguments = kwargs
            return ("/bin/true",)

    class Process:
        instances: ClassVar[list[Process]] = []
        load_ok: ClassVar[bool] = True

        def __init__(self, spec: Any) -> None:
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
                    error_detail="load fixture failed",
                )
            return RPCResponse(
                request.request_id,
                request.job_id,
                True,
                "fixture",
                "f" * 40,
            )

        async def stop(self) -> None:
            self.running = False
            self.stopped = True

    _, sessions, _ = database
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    qwen = registry.model(model_id)
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    manager = InstalledModels(tmp_path / "models", {qwen.id: qwen.revision})
    sandbox = Sandbox()
    supervisor = DictationWorkerSupervisor(
        paths,
        load_config(environment={}),
        registry,
        cast(ModelManager, manager),
        cast(WorkerEnvironmentProvisioner, Environment()),
        sessions,
        sandbox=cast(Any, sandbox),
    )
    monkeypatch.setattr(resident, "WorkerProcess", Process)
    monkeypatch.setattr(resident, "nvidia_devices", lambda: (Path("/dev/nvidia0"),))
    socket_path = paths.runtime / "workers/qwen.sock"

    async def scenario() -> None:
        await supervisor._start_entry(qwen, socket_path)
        assert supervisor._bootstrap_audio() == paths.runtime / "resident-bootstrap.wav"
        await supervisor._stop_locked()

    Process.instances.clear()
    Process.load_ok = True
    asyncio.run(scenario())
    process = Process.instances[0]
    assert [request.method for request in process.calls] == ["load", "unload"]
    assert process.calls[0].params["device"] == "auto"
    assert process.calls[0].params.get("streaming", False) is (model_id == "firered_vad")
    assert process.stopped
    assert sandbox.arguments["gpu_devices"] == (Path("/dev/nvidia0"),)
    assert not supervisor._processes

    Process.instances.clear()
    Process.load_ok = False
    with pytest.raises(RuntimeError, match="load fixture failed"):
        asyncio.run(supervisor._start_entry(qwen, socket_path, expose_gpu=False))
    assert Process.instances[0].calls[0].params["device"] == "cpu"
    assert Process.instances[0].stopped
    assert not supervisor._processes


class LifecycleProcess:
    def __init__(self, spec: Any, processes: list[LifecycleProcess], failures: set[str]) -> None:
        self.spec = spec
        self.running = False
        self.model_id = ""
        self.failures = failures
        self.stopped = False
        processes.append(self)

    async def start(self, *, timeout_seconds: float) -> None:
        self.running = True

    async def call(self, request: RPCRequest) -> RPCResponse:
        if request.method == "load":
            self.model_id = str(request.params["model_id"])
        failed = request.method == "load" and self.model_id in self.failures
        return RPCResponse(
            request.request_id,
            request.job_id,
            not failed,
            self.model_id,
            "a" * 40,
            error_code="model_load_failed" if failed else None,
            error_detail="fixture load failure" if failed else None,
        )

    async def stop(self) -> None:
        self.running = False
        self.stopped = True


def lifecycle_fixture(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: tuple[str, ...] = ("qwen3_asr_1_7b", "firered_vad"),
    enabled: bool = True,
    prewarm: bool = False,
    idle: int = 60,
    failures: set[str] | None = None,
) -> tuple[DictationWorkerSupervisor, InstalledModels, list[LifecycleProcess]]:
    from types import SimpleNamespace

    registry = load_registry(Path("config/model-registry.v1.yaml"))
    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    manager = InstalledModels(
        tmp_path / "models", {key: registry.model(key).revision for key in installed}
    )
    config = load_config(environment={})
    config = config.model_copy(
        update={
            "ibus": config.ibus.model_copy(
                update={
                    "enabled": enabled,
                    "prewarm_on_startup": prewarm,
                    "idle_unload_seconds": idle,
                }
            )
        }
    )
    processes: list[LifecycleProcess] = []
    supervisor = DictationWorkerSupervisor(
        paths,
        config,
        registry,
        cast(ModelManager, manager),
        cast(
            WorkerEnvironmentProvisioner,
            SimpleNamespace(resolve=lambda _: tmp_path / "environment"),
        ),
        sessions,
        sandbox=cast(Any, SimpleNamespace(command=lambda **_: ("fixture",))),
    )
    monkeypatch.setattr("classscribe.models.resident.shutil.which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(
        "classscribe.models.resident.provisioned_worker_command",
        lambda _: ("/bin/python", "/worker.py"),
    )
    monkeypatch.setattr("classscribe.models.resident.nvidia_devices", lambda: ())
    monkeypatch.setattr(
        "classscribe.models.resident.WorkerProcess",
        lambda spec: LifecycleProcess(spec, processes, failures if failures is not None else set()),
    )
    return supervisor, manager, processes


def test_cold_start_never_loads_or_hashes_models_and_disable_blocks_all_preparation(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, manager, processes = lifecycle_fixture(database, tmp_path, monkeypatch)

    async def scenario() -> None:
        await supervisor.start()
        assert not processes and not manager.verifications
        assert supervisor._background is not None
        await supervisor._background
        assert supervisor.status()["stage"] == "unloaded"
        assert not processes and not manager.verifications
        await supervisor.close()
        supervisor.config = supervisor.config.model_copy(
            update={"ibus": supervisor.config.ibus.model_copy(update={"enabled": False})}
        )
        await supervisor.start()
        assert supervisor.status()["stage"] == "disabled"
        with pytest.raises(RuntimeError, match="disabled"):
            await supervisor.resume_for_dictation()
        with pytest.raises(RuntimeError):
            await supervisor.request_prewarm()
        await supervisor.refresh()
        assert not processes and not manager.verifications
        await supervisor.close()

    asyncio.run(scenario())


def test_prewarm_is_reused_then_released_on_classroom_handoff(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, manager, processes = lifecycle_fixture(
        database, tmp_path, monkeypatch, prewarm=True
    )

    async def scenario() -> None:
        await supervisor.start()
        assert supervisor._background is not None
        await supervisor._background
        assert supervisor.status()["stage"] == "ready"
        assert len(processes) == 2
        await supervisor.resume_for_dictation()
        await supervisor.end_dictation()
        await supervisor.resume_for_dictation()
        assert len(processes) == 2
        assert len(manager.verifications) == 2
        await supervisor.end_dictation()
        await supervisor.suspend_for_classroom()
        assert all(process.stopped for process in processes)
        assert not supervisor._processes
        await supervisor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["ja", "auto"])
def test_missing_required_components_fail_before_hashing_or_loading(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
) -> None:
    installed = ("qwen3_asr_1_7b",) if language == "ja" else ("qwen3_asr_1_7b", "firered_vad")
    supervisor, manager, processes = lifecycle_fixture(
        database, tmp_path, monkeypatch, installed=installed
    )

    async def scenario() -> None:
        result = await supervisor.resume_for_dictation(language=language)
        assert not result.ready
        assert not supervisor._dictation_active
        assert supervisor.status()["stage"] == "failed"
        assert not processes and not manager.verifications
        await supervisor.close()

    asyncio.run(scenario())


def test_partial_load_failure_cleans_up_and_allows_retry(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failures = {"qwen3_asr_1_7b"}
    supervisor, manager, processes = lifecycle_fixture(
        database, tmp_path, monkeypatch, failures=failures
    )

    async def scenario() -> None:
        assert not (await supervisor.resume_for_dictation()).ready
        assert all(process.stopped for process in processes)
        assert not supervisor._processes
        failures.clear()
        assert (await supervisor.resume_for_dictation()).ready
        assert len(manager.verifications) == 4
        await supervisor.end_dictation()
        await supervisor.release_idle()
        assert all(process.stopped for process in processes)
        await supervisor.close()

    asyncio.run(scenario())


def test_idle_models_release_after_timeout(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, _manager, processes = lifecycle_fixture(database, tmp_path, monkeypatch, idle=1)

    async def scenario() -> None:
        await supervisor.resume_for_dictation()
        await supervisor.end_dictation()
        assert any(process.running for process in processes)
        await asyncio.sleep(1.1)
        assert all(process.stopped for process in processes)
        assert supervisor.status()["stage"] == "unloaded"
        await supervisor.close()

    asyncio.run(scenario())


def test_file_changes_invalidate_warm_reuse_and_rerun_full_verification(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, manager, processes = lifecycle_fixture(database, tmp_path, monkeypatch)

    async def scenario() -> None:
        await supervisor.prewarm()
        path = manager.installed_revision_metadata("qwen3_asr_1_7b")
        (path / "changed").write_bytes(b"new file")
        await supervisor.resume_for_dictation()
        assert len(processes) == 4
        assert len(manager.verifications) == 4
        assert all(process.stopped for process in processes[:2])
        await supervisor.end_dictation()
        await supervisor.close()

    asyncio.run(scenario())


def test_actual_dictation_preparation_rejects_competing_manual_prewarm(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, _manager, _processes = lifecycle_fixture(database, tmp_path, monkeypatch)

    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_start() -> ResidentWorkerManifest:
            entered.set()
            await release.wait()
            return supervisor._empty_manifest([])

        monkeypatch.setattr(supervisor, "_start_locked", slow_start)
        task = asyncio.create_task(supervisor.resume_for_dictation())
        await entered.wait()
        assert supervisor.status()["active"] is True
        with pytest.raises(RuntimeError):
            await supervisor.request_prewarm()
        with pytest.raises(RuntimeError):
            await supervisor.release_idle()
        release.set()
        await task
        assert supervisor.status()["active"] is False
        await supervisor.close()

    asyncio.run(scenario())
