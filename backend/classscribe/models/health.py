"""Load-and-infer health checks for newly installed model revisions."""

from __future__ import annotations

import asyncio
import shutil
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from classscribe_protocol import Priority, RPCRequest, RPCResponse

from classscribe.models.environment import WorkerEnvironmentProvisioner
from classscribe.models.inference import nvidia_devices
from classscribe.models.manager import HealthCheckOutcome
from classscribe.models.registry import ModelEntry
from classscribe.models.worker_process import (
    WorkerProcess,
    WorkerProcessSpec,
    provisioned_worker_command,
)
from classscribe.worker_sandbox import WorkerSandbox


class WorkerClient(Protocol):
    async def start(self, *, timeout_seconds: float = 15.0) -> None: ...

    async def call(self, request: RPCRequest) -> RPCResponse: ...

    async def stop(self, *, grace_seconds: float = 3.0) -> None: ...


class InstalledModelHealthChecker:
    """Provision one pinned worker, load the candidate, and run actual inference."""

    def __init__(
        self,
        provisioner: WorkerEnvironmentProvisioner,
        runtime_directory: Path,
        *,
        sandbox: WorkerSandbox | None = None,
    ) -> None:
        self.provisioner = provisioner
        self.runtime_directory = runtime_directory
        self.sandbox = sandbox

    def __call__(
        self,
        entry: ModelEntry,
        model_path: Path,
        audio_path: Path,
        environment: Mapping[str, str],
        language: str,
        transcript: str | None,
    ) -> HealthCheckOutcome:
        if language not in entry.languages and "auto" not in entry.languages:
            return HealthCheckOutcome(False, None, "health language is unsupported", {})
        project = self.provisioner.ensure(entry.worker)
        worker_command = provisioned_worker_command(project)
        self.runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.runtime_directory.chmod(0o700)
        identity = f"h-{uuid4().hex[:16]}"
        socket_path = self.runtime_directory / f"{identity}.sock"
        output_directory = self.runtime_directory / f"{identity}-output"
        output_directory.mkdir(mode=0o700)
        sandbox = self.sandbox or WorkerSandbox.detect()
        command = sandbox.command(
            worker_python=Path(worker_command[0]),
            worker_entrypoint=Path(worker_command[1]),
            model_revision=model_path,
            input_audio=audio_path,
            output_directory=output_directory,
            socket_directory=self.runtime_directory,
            gpu_devices=nvidia_devices(),
        )
        spec = WorkerProcessSpec(
            entry.worker,
            command,
            socket_path,
            (audio_path.parent,),
            socket_argument_path=Path("/run/classscribe") / socket_path.name,
            data_root_arguments=(Path("/input"),),
        )
        try:
            return asyncio.run(
                self._run(spec, entry, audio_path, environment, language, transcript)
            )
        except Exception as exc:
            return HealthCheckOutcome(
                False,
                None,
                f"worker inference failed: {type(exc).__name__}: {exc}",
                {"worker": entry.worker, "runtime_offline": True},
            )
        finally:
            if output_directory.exists() and not output_directory.is_symlink():
                shutil.rmtree(output_directory)

    async def _run(
        self,
        spec: WorkerProcessSpec,
        entry: ModelEntry,
        audio_path: Path,
        environment: Mapping[str, str],
        language: str,
        transcript: str | None,
    ) -> HealthCheckOutcome:
        process: WorkerClient = WorkerProcess(spec)
        await process.start(timeout_seconds=120)
        loaded = False
        try:
            load = await process.call(
                RPCRequest(
                    request_id=f"health-load-{uuid4().hex}",
                    job_id="model-install-health",
                    deadline_ms=600_000,
                    priority=Priority.INTERACTIVE,
                    method="load",
                    params={
                        "model_id": entry.id,
                        "model_revision": entry.revision,
                        "model_path": "/model",
                        "device": environment.get("device", "auto"),
                    },
                )
            )
            if not load.ok:
                return _failure(load, entry, "model load failed")
            loaded = True
            response = await _run_inference(
                process,
                entry,
                audio_path,
                language,
                transcript,
                request_audio_path=Path("/input/audio"),
            )
            if not response.ok:
                return _failure(response, entry, "model inference failed")
            meaningful = _meaningful_result(entry, response)
            peak = response.metrics.get("peak_vram_mb")
            measured = (
                round(float(peak))
                if isinstance(peak, (int, float)) and not isinstance(peak, bool)
                else None
            )
            return HealthCheckOutcome(
                meaningful,
                measured,
                "actual short-audio inference completed"
                if meaningful
                else "inference returned no meaningful model output",
                {
                    "worker": entry.worker,
                    "backend": response.metrics.get("backend", "unknown"),
                    "runtime_offline": all(
                        environment.get(name) == "1"
                        for name in (
                            "HF_HUB_OFFLINE",
                            "TRANSFORMERS_OFFLINE",
                            "HF_DATASETS_OFFLINE",
                        )
                    ),
                },
            )
        finally:
            try:
                if loaded:
                    unload = await process.call(
                        RPCRequest(
                            request_id=f"health-unload-{uuid4().hex}",
                            job_id="model-install-health",
                            deadline_ms=10_000,
                            priority=Priority.INTERACTIVE,
                            method="unload",
                            params={},
                        )
                    )
                    if not unload.ok:
                        raise RuntimeError(
                            f"model unload failed: {unload.error_code}: {unload.error_detail}"
                        )
            finally:
                await process.stop()


def _inference_request(
    entry: ModelEntry,
    audio_path: Path,
    language: str,
    transcript: str | None,
    *,
    request_audio_path: Path | None = None,
) -> RPCRequest:
    with wave.open(str(audio_path), "rb") as recording:
        frames = recording.getnframes()
    base: dict[str, object] = {
        "audio_path": str(request_audio_path or audio_path),
        "start_sample": 0,
        "end_sample": frames,
        "sample_rate": 16_000,
    }
    method = "health"
    if "asr" in entry.tasks and "batch" in entry.modes:
        method = "transcribe_batch"
        base.update(
            {
                "request_contract": "body-asr-v1",
                "core_start_sample": 0,
                "core_end_sample": frames,
                "language": language,
                "manual_language": True,
                "candidate_role": "course_expert" if entry.experimental else "primary",
                "experimental_enabled": entry.experimental,
                "hints": [],
                "batch_items": 1,
                "decode": {
                    "temperature": 0.0,
                    "do_sample": False,
                    "batch_size": 1,
                    "mixed_length_batch": False,
                    "max_new_tokens": max(32, min(512, frames // 160)),
                    "max_output_characters": max(64, min(2048, frames // 40)),
                    "seed": 0,
                },
            }
        )
    elif "asr" in entry.tasks and "streaming" in entry.modes:
        method = "stream_open"
        base = {"stream_id": "install-health", "sample_rate": 16_000, "channels": 1}
    elif "alignment" in entry.tasks:
        if not transcript or not transcript.strip():
            raise ValueError("an exact transcript is required to health-check an aligner")
        method = "align"
        base.update(
            {
                "alignment_contract": "final-align-v1",
                "text": transcript.strip(),
                "language": language,
                "manual_language": True,
                "safe_max_seconds": 30.0,
                "quality_gate": {
                    "no_decode_loop": True,
                    "no_missing_text": True,
                    "normal_character_rate": True,
                    "language_matches": True,
                    "coverage_ratio": 1.0,
                    "voiced_seconds": frames / 16_000,
                },
            }
        )
    elif "diarization" in entry.tasks:
        method = "diarize"
        base.update({"min_speakers": 1, "max_speakers": 4, "return_embeddings": True})
    elif "vad" in entry.tasks:
        method = "vad"
    elif "lid" in entry.tasks:
        method = "lid"
    elif "punctuation" in entry.tasks:
        method = "punctuate"
        base = {
            "punctuation_contract": "strict-punctuation-v1",
            "text": transcript.strip()
            if transcript and transcript.strip()
            else "health check text",
            "language": language,
            "manual_language": True,
        }
    return RPCRequest(
        request_id=f"health-infer-{uuid4().hex}",
        job_id="model-install-health",
        deadline_ms=600_000,
        priority=Priority.INTERACTIVE,
        method=method,
        params=base,
    )


async def _run_inference(
    process: WorkerClient,
    entry: ModelEntry,
    audio_path: Path,
    language: str,
    transcript: str | None,
    *,
    request_audio_path: Path | None = None,
) -> RPCResponse:
    if "asr" not in entry.tasks or "batch" in entry.modes:
        return await process.call(
            _inference_request(
                entry,
                audio_path,
                language,
                transcript,
                request_audio_path=request_audio_path,
            )
        )
    if "streaming" not in entry.modes:
        raise ValueError("model has no health-checkable inference mode")
    stream_id = f"install-health-{uuid4().hex}"
    opened = await process.call(
        RPCRequest(
            request_id=f"health-stream-open-{uuid4().hex}",
            job_id="model-install-health",
            deadline_ms=600_000,
            priority=Priority.INTERACTIVE,
            method="stream_open",
            params={
                "stream_id": stream_id,
                "sample_rate": 16_000,
                "channels": 1,
                "language": language,
                "chunk_ms": 560,
            },
        )
    )
    if not opened.ok:
        return opened
    with wave.open(str(audio_path), "rb") as recording:
        pcm = recording.readframes(recording.getnframes())
    try:
        for offset in range(0, len(pcm), 32_000):
            pushed = await process.call(
                RPCRequest(
                    request_id=f"health-stream-push-{uuid4().hex}",
                    job_id="model-install-health",
                    deadline_ms=600_000,
                    priority=Priority.INTERACTIVE,
                    method="stream_push",
                    params={
                        "stream_id": stream_id,
                        "pcm_s16le": pcm[offset : offset + 32_000],
                        "sample_rate": 16_000,
                        "language": language,
                        "absolute_start_sample": offset // 2,
                    },
                )
            )
            if not pushed.ok:
                return pushed
        return await process.call(
            RPCRequest(
                request_id=f"health-stream-flush-{uuid4().hex}",
                job_id="model-install-health",
                deadline_ms=600_000,
                priority=Priority.INTERACTIVE,
                method="stream_flush",
                params={
                    "stream_id": stream_id,
                    "language": language,
                    "rolling_context": [transcript] if transcript else [],
                    "confirmation": "quality",
                },
            )
        )
    finally:
        await process.call(
            RPCRequest(
                request_id=f"health-stream-close-{uuid4().hex}",
                job_id="model-install-health",
                deadline_ms=10_000,
                priority=Priority.INTERACTIVE,
                method="stream_close",
                params={"stream_id": stream_id},
            )
        )


def _meaningful_result(entry: ModelEntry, response: RPCResponse) -> bool:
    if "asr" in entry.tasks:
        return bool(
            response.raw_text.strip() or response.normalized_text.strip() or response.segments
        )
    if "punctuation" in entry.tasks:
        return bool(response.normalized_text.strip())
    if "alignment" in entry.tasks or "diarization" in entry.tasks:
        return bool(response.segments)
    return bool(response.metrics or response.result or response.language)


def _failure(response: RPCResponse, entry: ModelEntry, prefix: str) -> HealthCheckOutcome:
    return HealthCheckOutcome(
        False,
        None,
        f"{prefix}: {response.error_code}: {response.error_detail}",
        {"worker": entry.worker, "runtime_offline": True},
    )
