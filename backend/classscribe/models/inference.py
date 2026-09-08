"""Offline model sessions, with one GPU slot and an independent CPU slot."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import shutil
import threading
import wave
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from classscribe_protocol import Priority, ProtocolError, RPCRequest, RPCResponse

from classscribe.activity import report_activity
from classscribe.models.environment import WorkerEnvironmentProvisioner
from classscribe.models.manager import ModelManager
from classscribe.models.registry import ModelEntry
from classscribe.models.worker_process import (
    WorkerProcess,
    WorkerProcessSpec,
    provisioned_worker_command,
)
from classscribe.worker_sandbox import WorkerSandbox

_T = TypeVar("_T")
_CPU_MODELS = frozenset({"firered_vad", "firered_lid"})


@dataclass
class _ModelSession:
    entry: ModelEntry
    job_id: str
    model_path: Path
    project: Path
    audio: Path
    audio_identity: tuple[int, int, int, int, int]
    output: Path
    remove_audio: bool
    process: WorkerProcess
    device: str
    calls: int = 0


class SandboxedModelInvoker:
    """Keep leased classroom models on a stable event loop across checkpoints.

    Unleased callers retain the one-shot load/infer/unload contract. A job lease
    must be closed in a finally block. No process, audio binding or model state is
    reused across jobs. CPU VAD/LID never acquire the GPU slot or preempt IBus.
    """

    def __init__(
        self,
        manager: ModelManager,
        provisioner: WorkerEnvironmentProvisioner,
        runtime_directory: Path,
        *,
        sandbox: WorkerSandbox | None = None,
        before_gpu_use: Callable[[], None] | None = None,
    ) -> None:
        self.manager = manager
        self.provisioner = provisioner
        self.runtime_directory = runtime_directory
        self.sandbox = sandbox
        self.before_gpu_use = before_gpu_use
        self._guard = threading.Lock()
        self._gpu_busy = threading.Event()
        self._jobs: set[str] = set()
        self._boundaries: dict[str, Callable[[], None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._inflight = 0
        self._sessions: dict[str, _ModelSession] = {}
        self._retiring: dict[str, _ModelSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def open_job(self, job_id: str, *, boundary: Callable[[], None] | None = None) -> None:
        with self._guard:
            self._jobs.add(job_id)
            if boundary is not None:
                self._boundaries[job_id] = boundary

    def _submit(self, operation: Coroutine[Any, Any, _T]) -> concurrent.futures.Future[_T]:
        with self._guard:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                self._locks = {"gpu": asyncio.Lock(), "cpu": asyncio.Lock()}

                def run() -> None:
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_forever()
                    finally:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.run_until_complete(loop.shutdown_default_executor())
                        loop.close()

                self._loop = loop
                self._thread = threading.Thread(
                    target=run, name="classscribe-model-sessions", daemon=True
                )
                self._thread.start()
            self._inflight += 1
            return asyncio.run_coroutine_threadsafe(operation, self._loop)

    def _finished(self) -> None:
        thread = None
        with self._guard:
            self._inflight -= 1
            if not self._jobs and self._inflight == 0 and self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop = None
                thread, self._thread = self._thread, None
        if thread is not None:
            thread.join()

    def close_job(self, job_id: str) -> None:
        with self._guard:
            self._jobs.discard(job_id)
            self._boundaries.pop(job_id, None)
            exists = self._loop is not None
        if exists:
            future = self._submit(self._release_owned(job_id))
            try:
                future.result()
            finally:
                self._finished()

    def release_cpu(self, job_id: str) -> None:
        with self._guard:
            exists = self._loop is not None
        if exists:
            future = self._submit(self._release_owned(job_id, lanes=("cpu",)))
            try:
                future.result()
            finally:
                self._finished()

    def close(self) -> None:
        with self._guard:
            self._jobs.clear()
            self._boundaries.clear()
            exists = self._loop is not None
        if exists:
            future = self._submit(self._release_owned(None))
            try:
                future.result()
            finally:
                self._finished()

    def uses_gpu(self) -> bool:
        return self._gpu_busy.is_set()

    def _sync_gpu_state(self) -> None:
        # Called only on the owner loop, after releasing a slot lock. Queued
        # requests must pass their boundary check before using a newly free GPU.
        if self._sessions.get("gpu") or self._retiring.get("gpu") or self._locks["gpu"].locked():
            self._gpu_busy.set()
        else:
            self._gpu_busy.clear()

    async def _release_owned(
        self, job_id: str | None, *, lanes: tuple[str, ...] = ("gpu", "cpu")
    ) -> None:
        try:
            for lane in lanes:
                async with self._locks[lane]:
                    current = self._sessions.get(lane)
                    if current is not None and (job_id is None or current.job_id == job_id):
                        await self._drop(lane)
        finally:
            self._sync_gpu_state()

    async def __call__(self, entry: ModelEntry, request: RPCRequest) -> RPCResponse:
        future = self._submit(self._execute(entry, request))
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # Let the bounded RPC and its cleanup finish before stopping the owner loop.
            with contextlib.suppress(Exception):
                await asyncio.shield(wrapped)
            raise
        finally:
            self._finished()

    async def _execute(self, entry: ModelEntry, request: RPCRequest) -> RPCResponse:
        lane = "cpu" if entry.id in _CPU_MODELS else "gpu"
        report_activity(
            "waiting_resource",
            model_id=entry.id,
            model_name=entry.display_name,
            device="cpu" if lane == "cpu" else "auto",
            request_id=request.request_id,
            force=True,
            **{
                k: request.params[k]
                for k in ("start_sample", "end_sample", "language")
                if k in request.params
            },
        )
        try:
            async with self._locks[lane]:
                if lane == "gpu":
                    self._gpu_busy.set()
                # A task can be paused/cancelled while queued for this slot. Check
                # again before touching the model retained by another job.
                with self._guard:
                    boundary = self._boundaries.get(request.job_id)
                if boundary is not None:
                    await asyncio.to_thread(boundary)
                observer = asyncio.create_task(self._observe(lane))
                try:
                    current = self._sessions.get(lane)
                    if current is not None and await self._reusable(current, entry, request):
                        report_activity(
                            "reuse_model",
                            model_id=entry.id,
                            model_name=entry.display_name,
                            device=current.device,
                            session_calls=current.calls,
                            force=True,
                        )
                    else:
                        await self._drop(lane)
                        if lane == "gpu" and self.before_gpu_use is not None:
                            await asyncio.to_thread(self.before_gpu_use)
                        current = await self._load(lane, entry, request)
                    report_activity(
                        "process_audio",
                        model_id=entry.id,
                        model_name=entry.display_name,
                        device=current.device,
                        timeout_seconds=request.deadline_ms / 1000,
                        internal_progress=False,
                    )
                    response = await current.process.call(_sandbox_request(request))
                    current.calls += 1
                    if not response.ok:
                        report_activity("model_error", force=True)
                        await self._drop(lane)
                    else:
                        report_activity("audio_processed", force=True)
                        with self._guard:
                            retained = request.job_id in self._jobs
                        if retained:
                            report_activity(
                                "model_retained", session_calls=current.calls, force=True
                            )
                        else:
                            await self._drop(lane)
                    return response
                except BaseException:
                    await self._drop(lane)
                    raise
                finally:
                    observer.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await observer
        finally:
            self._sync_gpu_state()

    async def _reusable(
        self, current: _ModelSession, entry: ModelEntry, request: RPCRequest
    ) -> bool:
        if (
            current.job_id != request.job_id
            or current.entry.id != entry.id
            or current.entry.revision != entry.revision
            or not current.process.running
        ):
            return False
        raw = request.params.get("audio_path")
        if isinstance(raw, str):
            audio, _ = self._input_audio(request, "reuse")
            if audio != current.audio or _file_identity(audio) != current.audio_identity:
                return False
        elif not current.remove_audio:
            return False
        # Inspect the active revision and pinned source identity, never rehash weights here.
        path = await asyncio.to_thread(self.manager.installed_revision_metadata, entry.id)
        project = await asyncio.to_thread(self.provisioner.resolve, entry.worker)
        return path == current.model_path and project == current.project

    async def _load(self, lane: str, entry: ModelEntry, request: RPCRequest) -> _ModelSession:
        report_activity("verify_model", model_id=entry.id, model_name=entry.display_name)
        model_path = await asyncio.to_thread(self.manager.resolve_for_runtime, entry.id)
        if model_path.name != entry.revision:
            raise ProtocolError("active model revision differs from the registry route")
        report_activity("prepare_environment")
        project = await asyncio.to_thread(self.provisioner.resolve, entry.worker)
        worker = provisioned_worker_command(project)
        self.runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.runtime_directory.chmod(0o700)
        identity = f"inference-{entry.id}-{uuid4().hex}"
        socket_path = self.runtime_directory / f"{identity}.sock"
        output = self.runtime_directory / f"{identity}-output"
        output.mkdir(mode=0o700)
        audio: Path | None = None
        remove_audio = False
        try:
            audio, remove_audio = self._input_audio(request, identity)
            devices = nvidia_devices() if lane == "gpu" else ()
            device = (
                "cpu"
                if lane == "cpu"
                else (
                    "cuda:0"
                    if entry.worker == "qwen"
                    and any(item.name.removeprefix("nvidia").isdigit() for item in devices)
                    else "auto"
                )
            )
            command = (self.sandbox or WorkerSandbox.detect()).command(
                worker_python=Path(worker[0]),
                worker_entrypoint=Path(worker[1]),
                model_revision=model_path,
                input_audio=audio,
                output_directory=output,
                socket_directory=self.runtime_directory,
                gpu_devices=devices,
            )
            process = WorkerProcess(
                WorkerProcessSpec(
                    entry.worker,
                    command,
                    socket_path,
                    (audio.parent,),
                    socket_argument_path=Path("/run/classscribe") / socket_path.name,
                    data_root_arguments=(Path("/input"),),
                )
            )
            current = _ModelSession(
                entry,
                request.job_id,
                model_path,
                project,
                audio,
                _file_identity(audio),
                output,
                remove_audio,
                process,
                device,
            )
            self._sessions[lane] = current
            report_activity("load_model", timeout_seconds=120)
            await process.start(timeout_seconds=120)
            report_activity(
                "load_model",
                force=True,
                timeout_seconds=request.deadline_ms / 1000,
                started_at=datetime.now(UTC).isoformat(),
            )
            loaded = await process.call(
                RPCRequest(
                    f"{request.request_id}:load",
                    request.job_id,
                    request.deadline_ms,
                    request.priority,
                    "load",
                    {
                        "model_id": entry.id,
                        "model_revision": entry.revision,
                        "model_path": "/model",
                        "device": device,
                    },
                )
            )
            if not loaded.ok:
                raise ProtocolError(
                    f"model load failed: {loaded.error_code}: {loaded.error_detail}"
                )
            current.device = str(loaded.result.get("device", device))
            return current
        except BaseException:
            await self._drop(lane)
            if output.exists() and not output.is_symlink():
                shutil.rmtree(output)
            if remove_audio and audio is not None:
                audio.unlink(missing_ok=True)
            raise

    async def _drop(self, lane: str) -> None:
        current = self._sessions.pop(lane, None)
        if current is None:
            return
        self._retiring[lane] = current
        report_activity("release_model", model_id=current.entry.id, timeout_seconds=30)
        try:
            if current.process.running:
                with contextlib.suppress(Exception):
                    await current.process.call(
                        RPCRequest(
                            f"release-{uuid4().hex}",
                            current.job_id,
                            30000,
                            Priority.BACKGROUND,
                            "unload",
                            {},
                        )
                    )
        finally:
            try:
                await current.process.stop()
            finally:
                if current.output.exists() and not current.output.is_symlink():
                    shutil.rmtree(current.output)
                if current.remove_audio:
                    current.audio.unlink(missing_ok=True)
                self._retiring.pop(lane, None)
                report_activity(
                    "model_released", model_id=current.entry.id, process_alive=False, force=True
                )

    async def _observe(self, lane: str) -> None:
        while True:
            current = self._sessions.get(lane) or self._retiring.get(lane)
            report_activity(
                measured=False,
                process_alive=bool(current and current.process.running),
                process_checked_at=datetime.now(UTC).isoformat(),
            )
            await asyncio.sleep(1)

    def _input_audio(self, request: RPCRequest, identity: str) -> tuple[Path, bool]:
        raw = request.params.get("audio_path")
        if isinstance(raw, str):
            candidate = Path(raw)
            absolute = candidate.absolute()
            resolved = candidate.resolve(strict=True)
            if absolute != resolved or candidate.is_symlink() or not resolved.is_file():
                raise ProtocolError("inference audio must be a canonical non-symlink file")
            return resolved, False
        path = self.runtime_directory / f"{identity}-silence.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(b"\0\0" * 160)
        path.chmod(0o400)
        return path, True


def _sandbox_request(request: RPCRequest) -> RPCRequest:
    params = dict(request.params)
    if "audio_path" in params:
        params["audio_path"] = "/input/audio"
    return RPCRequest(
        request.request_id,
        request.job_id,
        request.deadline_ms,
        request.priority,
        request.method,
        params,
    )


def nvidia_devices() -> tuple[Path, ...]:
    """Expose only canonical NVIDIA character devices; CPU-only systems expose none."""

    return tuple(
        path
        for path in sorted(Path("/dev").glob("nvidia*"))
        if not path.is_symlink() and path.is_char_device()
    )


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
