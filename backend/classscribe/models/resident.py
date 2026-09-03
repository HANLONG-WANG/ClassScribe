"""Supervise pinned, sandboxed resident workers used by IBus dictation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import wave
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from classscribe_protocol import (
    Priority,
    ResidentAccuracyRoute,
    ResidentWorkerManifest,
    ResidentWorkerRoute,
    RPCRequest,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.config import AppConfig
from classscribe.db.models import BenchmarkRun, BenchmarkStatus, ProfileSetting
from classscribe.errors import ClassScribeError
from classscribe.models.environment import WorkerEnvironmentProvisioner
from classscribe.models.inference import nvidia_devices
from classscribe.models.manager import ModelManager
from classscribe.models.registry import ModelEntry, ModelRegistry
from classscribe.models.worker_process import (
    WorkerProcess,
    WorkerProcessSpec,
    provisioned_worker_command,
)
from classscribe.paths import AppPaths
from classscribe.recovery import atomic_write_text
from classscribe.worker_sandbox import WorkerSandbox


class DictationWorkerSupervisor:
    """Own resident worker lifecycles while dictationd remains model-library-free."""

    def __init__(
        self,
        paths: AppPaths,
        config: AppConfig,
        registry: ModelRegistry,
        manager: ModelManager,
        provisioner: WorkerEnvironmentProvisioner,
        sessions: sessionmaker[Session],
        *,
        sandbox: WorkerSandbox | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.registry = registry
        self.manager = manager
        self.provisioner = provisioner
        self.sessions = sessions
        self.sandbox = sandbox
        self.manifest_path = paths.runtime / "resident-workers.json"
        self._processes: list[tuple[WorkerProcess, bool]] = []
        self._current_manifest: ResidentWorkerManifest | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._suspended_for_classroom = False
        self._dictation_active = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._suspended_for_classroom = False
        self._dictation_active = False
        await self.refresh()

    async def close(self) -> None:
        async with self._lock:
            self._dictation_active = False
            self._suspended_for_classroom = True
            await self._stop_locked()
            self._write_manifest(
                ResidentWorkerManifest(
                    False,
                    None,
                    (),
                    {},
                    None,
                    None,
                    None,
                    ("core worker supervisor stopped",),
                    datetime.now(UTC).isoformat(),
                )
            )

    def request_refresh(self) -> None:
        """Refresh idle workers; an active dictation keeps its route until release."""

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self.refresh()))

    def suspend_for_classroom_sync(self) -> None:
        """Move GPU ownership to a pipeline running in its background thread."""

        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("resident worker supervisor is not running")
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            raise RuntimeError("classroom GPU handoff must run outside the core event loop")
        future = asyncio.run_coroutine_threadsafe(self.suspend_for_classroom(), loop)
        future.result(timeout=180)

    async def suspend_for_classroom(self) -> ResidentWorkerManifest:
        """Unload resident GPU state before a classroom model is allowed to start."""

        async with self._lock:
            if self._dictation_active:
                raise RuntimeError("cannot start classroom inference during active dictation")
            self._suspended_for_classroom = True
            await self._stop_locked()
            manifest = self._empty_manifest(["GPU is reserved for classroom inference"])
            self._write_manifest(manifest)
            return manifest

    async def resume_for_dictation(self) -> ResidentWorkerManifest:
        """Load residents after all classroom work reaches a safe boundary."""

        async with self._lock:
            if self._dictation_active:
                raise RuntimeError("dictation worker set is already active")
            self._dictation_active = True
            self._suspended_for_classroom = False
            await self._stop_locked()
            manifest = await self._start_locked()
            self._write_manifest(manifest)
            return manifest

    async def end_dictation(self) -> ResidentWorkerManifest:
        """Release every resident process before classroom dispatch resumes."""

        async with self._lock:
            self._dictation_active = False
            self._suspended_for_classroom = True
            await self._stop_locked()
            manifest = self._empty_manifest(["resident workers released after dictation"])
            self._write_manifest(manifest)
            return manifest

    async def prepare_accuracy(self, language: str) -> ResidentWorkerManifest:
        """Replace resident GPU workers with the pinned language-specific final decoder."""

        if language not in {"zh", "ja", "en"}:
            raise ValueError("accuracy hot switch requires a concrete language")
        async with self._lock:
            if not self._dictation_active or self._current_manifest is None:
                raise RuntimeError("accuracy hot switch requires an active dictation")
            manifest = self._current_manifest
            route = manifest.accuracy_routes.get(language)
            if route is None:
                raise RuntimeError(f"no installed accuracy model for {language}")
            if route.socket_path is not None:
                return manifest
            target = self.registry.model(route.model_id)
            await self._stop_gpu_locked()
            socket = self.paths.runtime / "workers" / f"dictation-accuracy-{language}.sock"
            try:
                await self._start_entry(target, socket)
            except BaseException as exc:
                failed = replace(
                    manifest,
                    errors=(
                        *manifest.errors,
                        f"accuracy hot switch failed: {type(exc).__name__}: {exc}",
                    ),
                    generated_at=datetime.now(UTC).isoformat(),
                )
                self._write_manifest(failed)
                raise
            resident_route = ResidentWorkerRoute(target.id, target.revision, socket)
            routes = tuple(item for item in manifest.routes if item.model_id != target.id)
            routes = (*routes, resident_route)
            accuracy_routes = dict(manifest.accuracy_routes)
            for key, candidate in tuple(accuracy_routes.items()):
                if candidate.model_id == target.id:
                    accuracy_routes[key] = ResidentAccuracyRoute(
                        target.id, target.revision, socket, None
                    )
            switched = replace(
                manifest,
                routes=routes,
                accuracy_socket=socket,
                accuracy_routes=accuracy_routes,
                generated_at=datetime.now(UTC).isoformat(),
            )
            self._write_manifest(switched)
            return switched

    async def refresh(self) -> ResidentWorkerManifest:
        async with self._lock:
            if self._dictation_active:
                return self._empty_manifest(["refresh deferred until dictation is idle"])
            await self._stop_locked()
            manifest = (
                self._empty_manifest(["GPU is reserved for classroom inference"])
                if self._suspended_for_classroom
                else await self._start_locked()
            )
            self._write_manifest(manifest)
            return manifest

    def planned_profile_models(self) -> tuple[dict[str, str], tuple[ModelEntry, ...]]:
        """Plan effective auto-best routes within the configured resident VRAM budget."""

        installed: dict[str, ModelEntry] = {}
        for entry in self.registry.models:
            if not entry.enabled or "streaming" not in entry.modes or "asr" not in entry.tasks:
                continue
            try:
                path = self.manager.resolve_for_runtime(entry.id)
            except (ClassScribeError, OSError, ValueError):
                continue
            if path.name == entry.revision:
                installed[entry.id] = entry
        desired: dict[str, tuple[str, ...]] = {}
        with self.sessions() as session:
            for language in ("zh", "ja", "en", "auto"):
                for mode in ("fast", "balanced", "accuracy"):
                    key = f"ibus.{language}.{mode}"
                    desired[key] = self._profile_ranking(session, language, mode)
        configured_language = self.config.ibus.default_language.value
        if configured_language == "auto_mixed":
            configured_language = "auto"
        default_key = f"ibus.{configured_language}.{self.config.ibus.default_profile}"
        ordered_keys = (default_key, *(key for key in sorted(desired) if key != default_key))
        selected: list[ModelEntry] = []
        selected_ids: set[str] = set()
        used_vram_mb = 0
        for key in ordered_keys:
            for model_id in desired.get(key, ()):
                candidate = installed.get(model_id)
                if candidate is None or candidate.id in selected_ids:
                    continue
                requirement = (
                    candidate.resources.measured_vram_mb or candidate.resources.estimated_vram_mb
                ) + candidate.resources.safety_margin_mb
                if selected and used_vram_mb + requirement > self.config.hardware.max_vram_mb:
                    continue
                if requirement > self.config.hardware.max_vram_mb:
                    continue
                selected.append(candidate)
                selected_ids.add(candidate.id)
                used_vram_mb += requirement
                break
        routes: dict[str, str] = {}
        for key, ranking in desired.items():
            selected_for_profile = next((item for item in ranking if item in selected_ids), None)
            if selected_for_profile is None:
                selected_for_profile = next(
                    (
                        entry.id
                        for entry in selected
                        if _supports_language(entry, key.split(".")[1])
                    ),
                    None,
                )
            if selected_for_profile is not None:
                routes[key] = selected_for_profile
        return routes, tuple(selected)

    async def _start_locked(self) -> ResidentWorkerManifest:
        errors: list[str] = []
        if shutil.which("bwrap") is None:
            errors.append("Bubblewrap is unavailable")
            return self._empty_manifest(errors)
        profile_routes, entries = self.planned_profile_models()
        sockets: dict[str, Path] = {}
        routes: list[ResidentWorkerRoute] = []
        for entry in entries:
            socket = self.paths.runtime / "workers" / f"dictation-{entry.id}.sock"
            try:
                await self._start_entry(entry, socket)
            except Exception as exc:
                errors.append(f"{entry.id}: {type(exc).__name__}: {exc}")
                continue
            sockets[entry.id] = socket
            routes.append(ResidentWorkerRoute(entry.id, entry.revision, socket))
        available = set(sockets)
        profile_routes = {
            key: model_id for key, model_id in profile_routes.items() if model_id in available
        }
        default_language = self.config.ibus.default_language.value
        if default_language == "auto_mixed":
            default_language = "auto"
        default_key = f"ibus.{default_language}.{self.config.ibus.default_profile}"
        default_model = profile_routes.get(default_key)
        if default_model is None and routes:
            default_model = routes[0].model_id

        auxiliary: dict[str, Path | None] = {"vad": None, "lid": None}
        for role, model_id in (("vad", "firered_vad"), ("lid", "firered_lid")):
            entry = self.registry.model(model_id)
            socket = self.paths.runtime / "workers" / f"firered-{role}.sock"
            try:
                await self._start_entry(entry, socket, expose_gpu=False)
            except Exception as exc:
                errors.append(f"{model_id}: {type(exc).__name__}: {exc}")
            else:
                auxiliary[role] = socket
        accuracy_routes: dict[str, ResidentAccuracyRoute] = {}
        for language in ("zh", "ja", "en", "auto"):
            target = self._installed_accuracy_entry(language)
            if target is None:
                continue
            accuracy_socket_path = sockets.get(target.id)
            accuracy_routes[language] = ResidentAccuracyRoute(
                target.id,
                target.revision,
                accuracy_socket_path,
                (
                    None
                    if accuracy_socket_path is not None
                    else self._estimated_accuracy_load_ms(target)
                ),
            )
        default_accuracy = accuracy_routes.get(default_language)
        accuracy_socket = default_accuracy.socket_path if default_accuracy is not None else None
        ready = default_model is not None and auxiliary["vad"] is not None
        return ResidentWorkerManifest(
            ready,
            default_model,
            tuple(routes),
            profile_routes,
            auxiliary["vad"],
            auxiliary["lid"],
            accuracy_socket,
            tuple(errors),
            datetime.now(UTC).isoformat(),
            accuracy_routes,
        )

    async def _start_entry(
        self, entry: ModelEntry, socket: Path, *, expose_gpu: bool = True
    ) -> None:
        model_path = self.manager.resolve_for_runtime(entry.id)
        if model_path.name != entry.revision:
            raise ValueError("active revision differs from the registry")
        project = self.provisioner.resolve(entry.worker)
        worker = provisioned_worker_command(project)
        socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        socket.parent.chmod(0o700)
        bootstrap_audio = self._bootstrap_audio()
        output = self.paths.runtime / "resident-output" / entry.id
        if output.is_symlink():
            raise ValueError("resident output path may not be a symlink")
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        output.chmod(0o700)
        sandbox = self.sandbox or WorkerSandbox.detect()
        command = sandbox.command(
            worker_python=Path(worker[0]),
            worker_entrypoint=Path(worker[1]),
            model_revision=model_path,
            input_audio=bootstrap_audio,
            output_directory=output,
            socket_directory=socket.parent,
            gpu_devices=nvidia_devices() if expose_gpu else (),
        )
        process = WorkerProcess(
            WorkerProcessSpec(
                entry.worker,
                command,
                socket,
                (bootstrap_audio.parent,),
                socket_argument_path=Path("/run/classscribe") / socket.name,
                data_root_arguments=(Path("/input"),),
            )
        )
        try:
            await process.start(timeout_seconds=120)
            response = await process.call(
                RPCRequest(
                    request_id=f"resident:{entry.id}:load",
                    job_id="resident-dictation",
                    deadline_ms=120_000,
                    priority=Priority.DICTATION,
                    method="load",
                    params={
                        "model_id": entry.id,
                        "model_revision": entry.revision,
                        "model_path": "/model",
                        "device": "auto" if expose_gpu else "cpu",
                    },
                )
            )
            if not response.ok:
                raise RuntimeError(f"load failed: {response.error_code}: {response.error_detail}")
        except BaseException:
            await process.stop()
            raise
        self._processes.append((process, expose_gpu))

    async def _stop_locked(self) -> None:
        while self._processes:
            process, _uses_gpu = self._processes.pop()
            await self._stop_process(process)

    async def _stop_gpu_locked(self) -> None:
        retained: list[tuple[WorkerProcess, bool]] = []
        while self._processes:
            process, uses_gpu = self._processes.pop()
            if uses_gpu:
                await self._stop_process(process)
            else:
                retained.append((process, uses_gpu))
        self._processes.extend(reversed(retained))

    async def _stop_process(self, process: WorkerProcess) -> None:
        if process.running:
            with contextlib.suppress(Exception):
                await process.call(
                    RPCRequest(
                        request_id=f"resident:{process.spec.worker_id}:unload",
                        job_id="resident-dictation",
                        deadline_ms=30_000,
                        priority=Priority.DICTATION,
                        method="unload",
                        params={},
                    )
                )
        with contextlib.suppress(Exception):
            await process.stop()

    def _installed_accuracy_entry(self, language: str) -> ModelEntry | None:
        with self.sessions() as session:
            ranking = self._profile_ranking(session, language, "accuracy")
        for model_id in ranking:
            entry = self.registry.model(model_id)
            if (
                not entry.enabled
                or "asr" not in entry.tasks
                or not _supports_language(entry, language)
            ):
                continue
            try:
                path = self.manager.resolve_for_runtime(entry.id)
            except (ClassScribeError, OSError, ValueError):
                continue
            if path.name == entry.revision:
                return entry
        return None

    @staticmethod
    def _estimated_accuracy_load_ms(entry: ModelEntry) -> float:
        measured: list[float] = []
        for result in entry.benchmark.results.values():
            value = result.get("model_load_seconds")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                measured.append(float(value) * 1000)
        if measured:
            return round(max(measured), 1)
        vram = entry.resources.measured_vram_mb or entry.resources.estimated_vram_mb
        return float(max(2500, round(vram * 0.75)))

    def _profile_ranking(self, session: Session, language: str, mode: str) -> tuple[str, ...]:
        bootstrap = self.registry.rankings[f"ibus.{language}.{mode}"]
        if language == "auto":
            return bootstrap
        setting = session.scalar(
            select(ProfileSetting).where(
                ProfileSetting.language == language,
                ProfileSetting.scenario == f"ibus.{mode}",
            )
        )
        if setting is None or setting.benchmark_run_id is None:
            return bootstrap
        run = session.get(BenchmarkRun, setting.benchmark_run_id)
        models = setting.config_json.get("models")
        if (
            run is None
            or run.status is not BenchmarkStatus.COMPLETED
            or not isinstance(models, list)
            or not models
            or any(not isinstance(item, str) for item in models)
            or not _eligible_benchmark(run)
        ):
            return bootstrap
        ranking = next(
            (
                item
                for item in run.ranking_json
                if item.get("language") == language
                and item.get("scenario") == "ibus"
                and item.get("passed") is True
                and item.get("models") == models
            ),
            None,
        )
        if ranking is None:
            return bootstrap
        manifest_sha = run.parameters_json.get("manifest_sha256")
        calibrations = run.parameters_json.get("calibrations")
        if not isinstance(calibrations, list):
            return bootstrap
        for model_id in models:
            try:
                entry = self.registry.model(model_id)
            except KeyError:
                return bootstrap
            if not any(
                isinstance(item, Mapping)
                and item.get("model_id") == model_id
                and item.get("model_revision") == entry.revision
                and item.get("language") == language
                and item.get("scenario") == "ibus"
                and item.get("manifest_sha256") == manifest_sha
                for item in calibrations
            ):
                return bootstrap
        return tuple(models)

    def _bootstrap_audio(self) -> Path:
        path = self.paths.runtime / "resident-bootstrap.wav"
        if path.is_file() and not path.is_symlink():
            return path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(b"\0\0" * 160)
        path.chmod(0o400)
        return path

    def _empty_manifest(self, errors: list[str]) -> ResidentWorkerManifest:
        return ResidentWorkerManifest(
            False,
            None,
            (),
            {},
            None,
            None,
            None,
            tuple(errors),
            datetime.now(UTC).isoformat(),
        )

    def _write_manifest(self, manifest: ResidentWorkerManifest) -> None:
        self._current_manifest = manifest
        atomic_write_text(
            self.manifest_path,
            json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def _supports_language(entry: ModelEntry, language: str) -> bool:
    return (language == "auto" and "auto" in entry.languages) or language in entry.languages


def _eligible_benchmark(run: BenchmarkRun) -> bool:
    parameters = run.parameters_json
    digest = parameters.get("manifest_sha256")
    return (
        parameters.get("production_gold") is True
        and parameters.get("real_model_execution") is True
        and parameters.get("synthetic_gold") is False
        and isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )
