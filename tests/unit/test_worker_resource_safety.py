from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from classscribe.errors import ClassScribeError
from classscribe.models.environment import WorkerEnvironmentProvisioner, acquire_environment_lease
from classscribe.models.worker_process import STDERR_TAIL_BYTES, WorkerProcess, WorkerProcessSpec
from classscribe_protocol import Priority, ProtocolError, RPCRequest, RPCServer
from classscribe_protocol.adapter import run_blocking
from classscribe_protocol.batch_audio import pronunciation_context

from tests.unit.test_worker_environment import legacy_environment, source_tree

ROOT = Path(__file__).resolve().parents[2]


def _adapter(worker: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        f"resource_safety_{worker}", ROOT / "workers" / worker / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_adapter()


@pytest.mark.parametrize("action", ["deadline", "cancel"])
def test_native_cancel_retains_execution_gate_until_thread_exits(
    tmp_path: Path, action: str
) -> None:
    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        calls: list[str] = []

        def native() -> dict[str, object]:
            started.set()
            assert release.wait(5), "test native operation did not release"
            calls.append("native-finished")
            finished.set()
            return {}

        class Adapter:
            worker_id = "test"
            model_id = "test"
            model_revision = "a" * 40
            capabilities = ("streaming",)

            async def dispatch(
                self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
            ) -> Mapping[str, Any]:
                if method == "stream_flush":
                    return await run_blocking(native)
                assert method == "unload"
                assert finished.is_set(), "unload entered while native code still runs"
                calls.append("unload")
                return {}

        server = RPCServer(tmp_path / "unused.sock", Adapter())
        first = asyncio.create_task(
            server._dispatch(
                RPCRequest(
                    "native",
                    "job",
                    80 if action == "deadline" else 5000,
                    Priority.DICTATION,
                    "stream_flush",
                    {"stream_id": "s"},
                )
            )
        )
        second: asyncio.Task[Any] | None = None
        try:
            async with asyncio.timeout(2):
                while not started.is_set():
                    await asyncio.sleep(0.001)
            if action == "cancel":
                await server._dispatch(
                    RPCRequest(
                        "cancel",
                        "job",
                        1000,
                        Priority.DICTATION,
                        "cancel",
                        {"target_request_id": "native"},
                    )
                )
            response = await first
            assert response.error_code == (
                "deadline_exceeded" if action == "deadline" else "cancelled"
            )
            assert "native" in server._tasks and not finished.is_set()
            # Repeated cancellation must not release ownership early either.
            await server._dispatch(
                RPCRequest(
                    "cancel-again",
                    "job",
                    1000,
                    Priority.DICTATION,
                    "cancel",
                    {"target_request_id": "native"},
                )
            )
            second = asyncio.create_task(
                server._dispatch(
                    RPCRequest(
                        "unload",
                        "job",
                        2000,
                        Priority.DICTATION,
                        "unload",
                        {},
                    )
                )
            )
            await asyncio.sleep(0.02)
            assert not second.done() and not calls
            release.set()
            assert (await second).ok
            await asyncio.sleep(0)
            assert calls == ["native-finished", "unload"]
            assert not server._tasks
        finally:
            release.set()
            await first
            if second is not None:
                await second
            await server.close()

    asyncio.run(scenario())


def test_context_capacity_matches_config_and_pcm_bridge() -> None:
    for count in range(1, 6):
        values = [f"context {n}" for n in range(count)]
        params = {
            "pcm_s16le": b"\0\0" * 160,
            "absolute_start_sample": 0,
            "core_start_sample": 0,
            "end_sample": 160,
            "sample_rate": 16000,
            "language": "en",
            "rolling_context": values,
        }
        assert RPCRequest("context", "job", 1000, Priority.DICTATION, "transcribe_pcm", params)
        rendered = pronunciation_context({"rolling_context": values})
        assert all(value in rendered for value in values)
    params["rolling_context"] = ["one"] * 6
    with pytest.raises(ProtocolError, match="rolling context"):
        RPCRequest("bad", "job", 1000, Priority.DICTATION, "transcribe_pcm", params)
    schema = json.loads((ROOT / "protocol/schema/v1/worker-request.schema.json").read_text())
    pcm = next(
        item
        for item in schema["allOf"]
        if item.get("if", {}).get("properties", {}).get("method", {}).get("const")
        == "transcribe_pcm"
    )
    assert pcm["then"]["properties"]["params"]["properties"]["rolling_context"]["maxItems"] == 5


def test_stream_vad_retains_only_overlapping_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = _adapter("firered")
    adapter._model_kind = "stream_vad"
    adapter._loaded_path = tmp_path
    windows: list[bytes] = []

    def detect(frame: bytes) -> SimpleNamespace:
        windows.append(frame)
        return SimpleNamespace(is_speech=True, raw_prob=0.8, smoothed_prob=0.8)

    numpy = ModuleType("numpy")
    monkeypatch.setattr(numpy, "frombuffer", lambda value, **_kwargs: value, raising=False)
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    adapter._model = SimpleNamespace(detect_frame=detect, reset=lambda: None)
    pcm = b"\1\0" * 16000 * 3

    async def scenario() -> None:
        await adapter.dispatch(
            "stream_open", {"stream_id": "vad", "sample_rate": 16000}, asyncio.Event()
        )
        for start in range(0, len(pcm), 320):
            await adapter.dispatch(
                "stream_push",
                {"stream_id": "vad", "pcm_s16le": pcm[start : start + 320]},
                asyncio.Event(),
            )
            assert len(adapter._streams["vad"]) < 800
        assert windows == [pcm[start : start + 800] for start in range(0, len(pcm) - 799, 320)]
        assert adapter._vad_offsets["vad"] == len(windows) * 160

    asyncio.run(scenario())


def test_qwen_batch_fallback_coalesces_interim_without_losing_final_audio(tmp_path: Path) -> None:
    adapter = _adapter("qwen")
    adapter._loaded_path = tmp_path
    adapter._model_kind = "asr"
    inputs: list[int] = []

    async def decode(pcm: bytes, **kwargs: Any) -> dict[str, object]:
        inputs.append(len(pcm) // 2)
        return {
            "raw_text": "whole transcript",
            "normalized_text": "whole transcript",
            "segments": [],
        }

    adapter._decode_stream = decode

    async def scenario() -> None:
        await adapter.dispatch(
            "stream_open",
            {"stream_id": "asr", "sample_rate": 16000, "chunk_ms": 560},
            asyncio.Event(),
        )
        frame = b"\0\0" * 800
        for start in range(0, 16000 * 25, 800):
            await adapter.dispatch(
                "stream_push",
                {
                    "stream_id": "asr",
                    "pcm_s16le": frame,
                    "absolute_start_sample": start,
                    "language": "en",
                },
                asyncio.Event(),
            )
        result = await adapter.dispatch(
            "stream_flush",
            {"stream_id": "asr", "confirmation": "balanced", "language": "en"},
            asyncio.Event(),
        )
        assert result["normalized_text"] == "whole transcript"
        assert inputs[0] <= 9600  # preserve the initial ~560 ms update
        assert inputs[-1] == 16000 * 25
        assert sum(inputs) < 6 * 16000 * 25
        assert len(inputs) < 20

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_environment_interrupt_always_cleans_unpublished_staging(
    tmp_path: Path, interruption: type[BaseException]
) -> None:
    def abort(_command: Sequence[str], cwd: Path, _environment: dict[str, str]) -> None:
        (cwd / "partial.bin").write_bytes(b"payload")
        raise interruption()

    provisioner = WorkerEnvironmentProvisioner(
        source_tree(tmp_path / "source"), tmp_path / "cache", runner=abort
    )
    with pytest.raises(interruption):
        provisioner.ensure("qwen")
    assert not list((tmp_path / "cache").rglob("partial.bin"))
    assert provisioner.cache_status("qwen")["staging"] == 0


def _provisioner(tmp_path: Path) -> tuple[WorkerEnvironmentProvisioner, Path]:
    source = source_tree(tmp_path / "source")

    def runner(_command: Sequence[str], _cwd: Path, environment: dict[str, str]) -> None:
        python = Path(environment["UV_PROJECT_ENVIRONMENT"]) / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    return WorkerEnvironmentProvisioner(source, tmp_path / "cache", runner=runner), source


def test_environment_cleanup_preserves_current_and_live_versions(tmp_path: Path) -> None:
    provisioner, source = _provisioner(tmp_path)
    first = provisioner.ensure("qwen")
    lease = acquire_environment_lease(first)
    try:
        (source / "workers/qwen/adapter.py").write_text("VERSION = 2\n")
        second = provisioner.ensure("qwen")
        (source / "workers/qwen/adapter.py").write_text("VERSION = 3\n")
        current = provisioner.ensure("qwen")
        assert provisioner.cache_status("qwen")["versions"] == 3
        result = provisioner.cleanup("qwen", keep_versions=1)
        assert first.is_dir() and current.is_dir() and not second.exists()
        assert result["skipped_in_use"] == 1 and result["in_use"] == 1
        assert result["versions"] == 2 and result["size_complete"] is True
        assert "path" not in json.dumps(result)
    finally:
        os.close(lease)
    assert provisioner.cleanup("qwen", keep_versions=1)["versions"] == 1
    assert not first.exists()


def test_legacy_cleanup_never_removes_current_nested_environment(tmp_path: Path) -> None:
    provisioner, source = _provisioner(tmp_path)
    old = legacy_environment(source, tmp_path / "cache", "qwen")
    (source / "workers/qwen/adapter.py").write_text("VERSION = 4\n")
    current = provisioner.ensure("qwen")
    assert current.is_relative_to(old.parents[1])
    result = provisioner.cleanup("qwen", keep_versions=1)
    assert result["removed_count"] == 1
    assert not old.exists()
    assert provisioner.resolve("qwen") == current


def test_repair_never_moves_live_environment_and_bounds_backups(tmp_path: Path) -> None:
    provisioner, _source = _provisioner(tmp_path)
    project = provisioner.ensure("qwen")
    lease = acquire_environment_lease(project)
    marker = project.parents[1] / "complete.json"
    try:
        marker.write_text("[]")
        with pytest.raises(ClassScribeError, match="release workers"):
            provisioner.ensure("qwen", repair=True)
        assert project.is_dir()
    finally:
        os.close(lease)
    for _ in range(3):
        marker.write_text("[]")
        assert provisioner.ensure("qwen", repair=True) == project
        assert provisioner.cache_status("qwen")["damaged"] == 1


def test_worker_stderr_is_continuously_drained_into_bounded_tail(tmp_path: Path) -> None:
    async def scenario() -> None:
        worker = WorkerProcess(WorkerProcessSpec("test", ("unused",), tmp_path / "unused.sock", ()))
        reader = asyncio.StreamReader()
        task = asyncio.create_task(worker._drain_stderr(reader))
        for _ in range(40):
            reader.feed_data(b"x" * 4096)
            await asyncio.sleep(0)
            assert len(worker._stderr_tail) <= STDERR_TAIL_BYTES
        reader.feed_data(b"FINAL FAILURE")
        reader.feed_eof()
        tail = await task
        assert len(tail) == STDERR_TAIL_BYTES and tail.endswith(b"FINAL FAILURE")
        detail = await worker.stderr()
        assert "omitted" in detail and detail.endswith("FINAL FAILURE")
        assert worker._stderr_dropped_bytes == 40 * 4096 + len(b"FINAL FAILURE") - STDERR_TAIL_BYTES

    asyncio.run(scenario())


class _PendingProcess:
    def __init__(self, *, immediate_exit: bool = False) -> None:
        self.returncode: int | None = None
        self.stderr = None
        self.terminated = asyncio.Event()
        self.exited = asyncio.Event()
        self.immediate_exit = immediate_exit

    def terminate(self) -> None:
        self.terminated.set()
        if self.immediate_exit:
            self.returncode = -15
            self.exited.set()

    def kill(self) -> None:
        self.returncode = -9
        self.exited.set()

    async def wait(self) -> int:
        await self.exited.wait()
        assert self.returncode is not None
        return self.returncode


def test_cancelled_stop_keeps_lease_until_process_is_reaped(tmp_path: Path) -> None:
    provisioner, source = _provisioner(tmp_path)
    old = provisioner.ensure("qwen")
    lease = acquire_environment_lease(old)
    (source / "workers/qwen/adapter.py").write_text("VERSION = 9\n")
    provisioner.ensure("qwen")

    async def scenario() -> None:
        worker = WorkerProcess(WorkerProcessSpec("qwen", ("unused",), tmp_path / "unused.sock", ()))
        process = _PendingProcess()
        worker.process = cast(asyncio.subprocess.Process, process)
        worker._environment_lease_fd = lease
        stop = asyncio.create_task(worker.stop(grace_seconds=0.06))
        await process.terminated.wait()
        stop.cancel()
        await asyncio.sleep(0.01)
        assert not stop.done()
        assert provisioner.cleanup("qwen", keep_versions=1)["skipped_in_use"] == 1
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert process.returncode == -9
        assert worker.process is None and worker._environment_lease_fd is None
        assert provisioner.cleanup("qwen", keep_versions=1)["removed_count"] == 1

    asyncio.run(scenario())


def test_cancelled_start_reaps_process_created_after_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provisioner, _source = _provisioner(tmp_path)
    project = provisioner.ensure("qwen")

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        process = _PendingProcess(immediate_exit=True)

        async def create(*_args: object, **_kwargs: object) -> asyncio.subprocess.Process:
            entered.set()
            await release.wait()
            return cast(asyncio.subprocess.Process, process)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        worker = WorkerProcess(
            WorkerProcessSpec(
                "qwen",
                ("unused",),
                tmp_path / "unused.sock",
                (),
                environment_project=project,
            )
        )
        starting = asyncio.create_task(worker.start())
        await entered.wait()
        starting.cancel()
        await asyncio.sleep(0.01)
        assert not starting.done() and worker._environment_lease_fd is not None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert process.terminated.is_set()
        assert worker.process is None and worker._environment_lease_fd is None
        assert worker._socket_directory_fd is None
        assert provisioner.cache_status("qwen")["in_use"] == 0

    asyncio.run(scenario())


def _atomic_directory_exchange(first: Path, second: Path) -> None:
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    result = library.renameat2(-100, os.fsencode(first), -100, os.fsencode(second), 2)
    if result != 0:
        raise OSError(ctypes.get_errno(), "directory exchange failed")


def _manifest_inputs(tmp_path: Path) -> tuple[Path, Any, Any]:
    from tests.unit.test_model_manifests import _controlled_inputs, _write_controlled_bundle

    path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()
    (tmp_path / "config/model-revisions.lock.json").write_text(json.dumps(revisions))
    return path, registry, licenses


def test_manifest_reader_retries_atomic_generation_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import shutil

    import classscribe.models.manifests as manifests

    path, registry, licenses = _manifest_inputs(tmp_path)
    stage = path.parent.with_name("v1-next")
    shutil.copytree(path.parent, stage)
    index = json.loads((stage / path.name).read_text())
    member_path = stage / index["manifests"][0]["path"]
    member = json.loads(member_path.read_text())
    member["files"][0]["sha256"] = "b" * 64
    content = (json.dumps(member, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    member_path.write_bytes(content)
    index["manifests"][0]["sha256"] = hashlib.sha256(content).hexdigest()
    (stage / path.name).write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    original = manifests._load_json_document
    reads = 0

    def read(*args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        result = original(*args, **kwargs)
        if args[1].name == "bundle.v1.json":
            reads += 1
            if reads == 1:
                # First read gets the old index, following member reads get the
                # new directory. The resulting hash mismatch must be retried.
                _atomic_directory_exchange(path.parent, stage)
        return result

    monkeypatch.setattr(manifests, "_load_json_document", read)
    loaded = manifests.load_builtin_manifest_bundle(path, registry, licenses)
    assert loaded.manifests[0].files[0].sha256 == "b" * 64
    assert reads == 2


def test_manifest_reader_never_retries_stable_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import classscribe.models.manifests as manifests

    path, registry, licenses = _manifest_inputs(tmp_path)
    index = json.loads(path.read_text())
    (path.parent / index["manifests"][0]["path"]).write_text("{}")
    original = manifests.load_manifest_bundle
    calls = 0

    def read(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(manifests, "load_manifest_bundle", read)
    with pytest.raises(ValueError, match="SHA-256 differs"):
        manifests.load_builtin_manifest_bundle(path, registry, licenses)
    assert calls == 1


def test_manifest_reader_bounds_repeated_generation_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import classscribe.models.manifests as manifests

    path, registry, licenses = _manifest_inputs(tmp_path)
    stage = path.parent.with_name("v1-next")
    shutil.copytree(path.parent, stage)
    original = manifests.load_manifest_bundle
    calls = 0

    def read(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        _atomic_directory_exchange(path.parent, stage)
        return result

    monkeypatch.setattr(manifests, "load_manifest_bundle", read)
    with pytest.raises(ValueError, match="changed repeatedly"):
        manifests.load_builtin_manifest_bundle(path, registry, licenses)
    assert calls == 3


def test_cancelled_qwen_interim_cannot_mark_old_cache_as_current(tmp_path: Path) -> None:
    adapter = _adapter("qwen")
    adapter._loaded_path = tmp_path
    adapter._model_kind = "asr"
    calls = 0

    async def decode(pcm: bytes, **_kwargs: Any) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError
        text = "old" if calls == 1 else "complete"
        return {"normalized_text": text, "segments": []}

    adapter._decode_stream = decode

    async def scenario() -> None:
        await adapter.dispatch(
            "stream_open", {"stream_id": "asr", "sample_rate": 16000}, asyncio.Event()
        )
        frame = {"stream_id": "asr", "pcm_s16le": b"\0\0" * 16000, "language": "en"}
        await adapter.dispatch("stream_push", frame, asyncio.Event())
        with pytest.raises(asyncio.CancelledError):
            await adapter.dispatch("stream_push", frame, asyncio.Event())
        assert adapter._stream_decoded_samples["asr"] == 16000
        result = await adapter.dispatch(
            "stream_flush",
            {"stream_id": "asr", "confirmation": "fast", "language": "en"},
            asyncio.Event(),
        )
        assert result["normalized_text"] == "complete" and calls == 3

    asyncio.run(scenario())


def test_cancelled_vad_commits_native_frame_without_refeeding_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter("firered")
    adapter._loaded_path = tmp_path
    adapter._model_kind = "stream_vad"
    entered = threading.Event()
    release = threading.Event()
    windows: list[bytes] = []

    def detect(frame: bytes) -> SimpleNamespace:
        windows.append(frame)
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(is_speech=True, raw_prob=0.8, smoothed_prob=0.8)

    numpy = ModuleType("numpy")
    monkeypatch.setattr(numpy, "frombuffer", lambda value, **_kwargs: value, raising=False)
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    adapter._model = SimpleNamespace(reset=lambda: None, detect_frame=detect)
    pcm = b"".join(n.to_bytes(2, "little") for n in range(560))

    async def scenario() -> None:
        await adapter.dispatch(
            "stream_open", {"stream_id": "vad", "sample_rate": 16000}, asyncio.Event()
        )
        pushing = asyncio.create_task(
            adapter.dispatch(
                "stream_push", {"stream_id": "vad", "pcm_s16le": pcm[:800]}, asyncio.Event()
            )
        )
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            pushing.cancel()
            await asyncio.sleep(0.01)
            assert not pushing.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pushing
            assert adapter._vad_offsets["vad"] == 160
            assert bytes(adapter._streams["vad"]) == pcm[320:800]
            await adapter.dispatch(
                "stream_push", {"stream_id": "vad", "pcm_s16le": pcm[800:]}, asyncio.Event()
            )
            assert windows == [pcm[:800], pcm[320:1120]]
            assert adapter._vad_offsets["vad"] == 320
        finally:
            release.set()
            if not pushing.done():
                await asyncio.gather(pushing, return_exceptions=True)

    asyncio.run(scenario())
