"""Supervise isolated worker processes without importing their model environments."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from classscribe_protocol import Priority, ProtocolError, RPCClient, RPCRequest, RPCResponse

from classscribe.models.environment import acquire_environment_lease

STDERR_TAIL_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class WorkerProcessSpec:
    worker_id: str
    command: tuple[str, ...]
    socket_path: Path
    data_roots: tuple[Path, ...]
    socket_argument_path: Path | None = None
    data_root_arguments: tuple[Path, ...] | None = None
    environment_project: Path | None = None

    def __post_init__(self) -> None:
        if not self.worker_id or not self.command:
            raise ValueError("worker identity and command must not be empty")
        if not self.socket_path.is_absolute() or any(
            not path.is_absolute() for path in self.data_roots
        ):
            raise ValueError("worker socket and data roots must be absolute")
        if self.environment_project is not None and not self.environment_project.is_absolute():
            raise ValueError("worker environment project must be absolute")
        if self.socket_argument_path is not None and not self.socket_argument_path.is_absolute():
            raise ValueError("worker socket argument must be absolute")
        if self.data_root_arguments is not None and any(
            not path.is_absolute() for path in self.data_root_arguments
        ):
            raise ValueError("worker data-root arguments must be absolute")


class WorkerProcess:
    """Own one subprocess; transport failures remain data, never core-process failures."""

    def __init__(self, spec: WorkerProcessSpec) -> None:
        self.spec = spec
        self.process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[bytes] | None = None
        self._socket_directory_fd: int | None = None
        self._transport_path: Path | None = None
        self._environment_lease_fd: int | None = None
        self._stderr_tail = bytearray()
        self._stderr_dropped_bytes = 0

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @property
    def returncode(self) -> int | None:
        return None if self.process is None else self.process.returncode

    async def start(self, *, timeout_seconds: float = 15.0) -> None:
        if self.running:
            return
        self.spec.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.spec.socket_path.parent.chmod(0o700)
        socket_directory_fd = os.open(
            self.spec.socket_path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        self._socket_directory_fd = socket_directory_fd
        self._transport_path = (
            Path(f"/proc/self/fd/{socket_directory_fd}") / self.spec.socket_path.name
        )
        socket_argument = self.spec.socket_argument_path or self._transport_path
        data_root_arguments = self.spec.data_root_arguments or self.spec.data_roots
        command = [*self.spec.command, "--socket", str(socket_argument)]
        for root in data_root_arguments:
            command.extend(("--data-root", str(root)))
        environment = dict(os.environ)
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "NO_PROXY": "*",
            }
        )
        self._stderr_tail.clear()
        self._stderr_dropped_bytes = 0
        try:
            if self.spec.environment_project is not None:
                self._environment_lease_fd = acquire_environment_lease(
                    self.spec.environment_project
                )

            async def spawn() -> None:
                self.process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                    start_new_session=True,
                    pass_fds=()
                    if self.spec.socket_argument_path is not None
                    else (socket_directory_fd,),
                )
                if self.process is not None and self.process.stderr is not None:
                    self._stderr_task = asyncio.create_task(self._drain_stderr(self.process.stderr))

            await _wait_owned(asyncio.create_task(spawn()))
        except BaseException:
            if self.process is not None:
                await self.stop()
            else:
                self._close_environment_lease()
                self._close_socket_directory()
            raise

        assert self.process is not None
        try:
            async with asyncio.timeout(timeout_seconds):
                while not self._transport_path.exists():
                    if self.process.returncode is not None:
                        detail = await self.stderr()
                        raise ProtocolError(
                            f"worker {self.spec.worker_id} exited during startup: {detail}"
                        )
                    await asyncio.sleep(0.01)
                if not stat.S_ISSOCK(self._transport_path.lstat().st_mode):
                    raise ProtocolError("worker created a non-socket transport path")
        except BaseException:
            await self.stop()
            raise

    async def call(self, request: RPCRequest) -> RPCResponse:
        if not self.running:
            raise ProtocolError(f"worker {self.spec.worker_id} is not running")
        if self._transport_path is None:
            raise ProtocolError(f"worker {self.spec.worker_id} transport is unavailable")
        return await RPCClient(self._transport_path).call(request)

    async def cancel(
        self,
        *,
        request_id: str,
        target_request_id: str,
        job_id: str,
        deadline_ms: int = 1000,
    ) -> RPCResponse:
        return await self.call(
            RPCRequest(
                request_id=request_id,
                job_id=job_id,
                deadline_ms=deadline_ms,
                priority=Priority.DICTATION,
                method="cancel",
                params={"target_request_id": target_request_id},
            )
        )

    async def stop(self, *, grace_seconds: float = 3.0) -> None:
        # Caller cancellation must not cancel subprocess teardown or drop its
        # shared environment lease before the actual process has exited.
        await _wait_owned(asyncio.create_task(self._stop_impl(grace_seconds=grace_seconds)))

    async def _stop_impl(self, *, grace_seconds: float = 3.0) -> None:
        process = self.process
        try:
            if process is not None:
                if process.returncode is None:
                    process.terminate()
                    try:
                        async with asyncio.timeout(grace_seconds):
                            await process.wait()
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                self.process = None
                if self._stderr_task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._stderr_task
                    self._stderr_task = None
            if (
                self._transport_path is not None
                and self._transport_path.exists()
                and stat.S_ISSOCK(self._transport_path.lstat().st_mode)
            ):
                self._transport_path.unlink()
        finally:
            # A failed termination must keep the environment protected while
            # the subprocess is still alive; a subsequent stop can retry it.
            if self.process is None or self.process.returncode is not None:
                self._close_environment_lease()
            self._close_socket_directory()

    def _close_socket_directory(self) -> None:
        if self._socket_directory_fd is not None:
            os.close(self._socket_directory_fd)
            self._socket_directory_fd = None
        self._transport_path = None

    def _close_environment_lease(self) -> None:
        if self._environment_lease_fd is not None:
            fcntl.flock(self._environment_lease_fd, fcntl.LOCK_UN)
            os.close(self._environment_lease_fd)
            self._environment_lease_fd = None

    async def _drain_stderr(self, reader: asyncio.StreamReader) -> bytes:
        while chunk := await reader.read(4096):
            self._stderr_tail.extend(chunk)
            excess = max(0, len(self._stderr_tail) - STDERR_TAIL_BYTES)
            if excess:
                del self._stderr_tail[:excess]
                self._stderr_dropped_bytes += excess
        return bytes(self._stderr_tail)

    async def stderr(self) -> str:
        detail = self._stderr_tail.decode("utf-8", errors="replace").strip()
        if self._stderr_dropped_bytes:
            return f"[{self._stderr_dropped_bytes} earlier stderr bytes omitted]\n{detail}"
        return detail


async def _wait_owned(task: asyncio.Task[None]) -> None:
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


def worker_command(repository_root: Path, worker_id: str) -> tuple[str, ...]:
    """Resolve only declared worker IDs and their independent virtual environments."""

    if not worker_id.replace("_", "").isalnum():
        raise ValueError("unsafe worker ID")
    root = repository_root.resolve(strict=True)
    worker_root = (root / "workers" / worker_id).resolve(strict=True)
    if not worker_root.is_relative_to(root / "workers"):
        raise ValueError("worker escaped repository root")
    python = worker_root / ".venv" / "bin" / "python"
    entry = worker_root / "worker.py"
    if not python.is_file() or not entry.is_file():
        raise FileNotFoundError(f"worker environment is incomplete: {worker_id}")
    return (str(python), str(entry))


def provisioned_worker_command(project_root: Path) -> tuple[str, ...]:
    """Resolve an atomically provisioned, copied worker environment."""

    root = project_root.resolve(strict=True)
    python = root / ".venv" / "bin" / "python"
    entry = root / "worker.py"
    if python.is_symlink() or entry.is_symlink() or not python.is_file() or not entry.is_file():
        raise FileNotFoundError("provisioned worker environment is incomplete")
    return (str(python), str(entry))


def requests_for_candidates(
    candidate_ids: Sequence[str],
    *,
    job_id: str,
    method: str,
    params: Mapping[str, object],
    priority: Priority,
    deadline_ms: int,
) -> tuple[RPCRequest, ...]:
    """Build a sequential candidate batch; callers dispatch one only after the prior unloads."""

    return tuple(
        RPCRequest(
            request_id=f"{job_id}:{index}:{model_id}",
            job_id=job_id,
            deadline_ms=deadline_ms,
            priority=priority,
            method=method,
            params={**params, "candidate_model_id": model_id},
        )
        for index, model_id in enumerate(candidate_ids)
    )
