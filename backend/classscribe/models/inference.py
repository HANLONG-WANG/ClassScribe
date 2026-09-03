"""Offline, sandboxed one-model-at-a-time inference for production pipelines."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from classscribe_protocol import Priority, ProtocolError, RPCRequest, RPCResponse

from classscribe.models.environment import WorkerEnvironmentProvisioner
from classscribe.models.manager import ModelManager
from classscribe.models.registry import ModelEntry
from classscribe.models.worker_process import (
    WorkerProcess,
    WorkerProcessSpec,
    provisioned_worker_command,
)
from classscribe.worker_sandbox import WorkerSandbox


class SandboxedModelInvoker:
    """Run a complete load/infer/unload lifecycle inside Bubblewrap."""

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
        self._gpu_lock = threading.Lock()

    async def __call__(self, entry: ModelEntry, request: RPCRequest) -> RPCResponse:
        if self.before_gpu_use is not None:
            await asyncio.to_thread(self.before_gpu_use)
        await asyncio.to_thread(self._gpu_lock.acquire)
        try:
            return await self._invoke_locked(entry, request)
        finally:
            self._gpu_lock.release()

    async def _invoke_locked(self, entry: ModelEntry, request: RPCRequest) -> RPCResponse:
        model_path = self.manager.resolve_for_runtime(entry.id)
        if model_path.name != entry.revision:
            raise ProtocolError("active model revision differs from the registry route")
        project = self.provisioner.resolve(entry.worker)
        worker = provisioned_worker_command(project)
        self.runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.runtime_directory.chmod(0o700)
        identity = f"inference-{entry.id}-{uuid4().hex}"
        socket_path = self.runtime_directory / f"{identity}.sock"
        output_directory = self.runtime_directory / f"{identity}-output"
        output_directory.mkdir(mode=0o700)
        input_audio, remove_input = self._input_audio(request, identity)
        sandbox = self.sandbox or WorkerSandbox.detect()
        command = sandbox.command(
            worker_python=Path(worker[0]),
            worker_entrypoint=Path(worker[1]),
            model_revision=model_path,
            input_audio=input_audio,
            output_directory=output_directory,
            socket_directory=self.runtime_directory,
            gpu_devices=nvidia_devices(),
        )
        process = WorkerProcess(
            WorkerProcessSpec(
                entry.worker,
                command,
                socket_path,
                (input_audio.parent,),
                socket_argument_path=Path("/run/classscribe") / socket_path.name,
                data_root_arguments=(Path("/input"),),
            )
        )
        try:
            await process.start(timeout_seconds=120)
            loaded = await process.call(
                RPCRequest(
                    request_id=f"{request.request_id}:load",
                    job_id=request.job_id,
                    deadline_ms=request.deadline_ms,
                    priority=request.priority,
                    method="load",
                    params={
                        "model_id": entry.id,
                        "model_revision": entry.revision,
                        "model_path": "/model",
                        "device": "auto",
                    },
                )
            )
            if not loaded.ok:
                raise ProtocolError(
                    f"model load failed: {loaded.error_code}: {loaded.error_detail}"
                )
            return await process.call(_sandbox_request(request))
        finally:
            if process.running:
                with contextlib.suppress(Exception):
                    await process.call(
                        RPCRequest(
                            request_id=f"{request.request_id}:unload",
                            job_id=request.job_id,
                            deadline_ms=30_000,
                            priority=Priority.BACKGROUND,
                            method="unload",
                            params={},
                        )
                    )
            await process.stop()
            if output_directory.exists() and not output_directory.is_symlink():
                shutil.rmtree(output_directory)
            if remove_input:
                input_audio.unlink(missing_ok=True)

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
