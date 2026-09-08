"""Supervise pinned, sandboxed resident workers used by IBus dictation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import wave
from collections.abc import Callable, Mapping
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
from classscribe.errors import ClassScribeError, public_error_detail
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
        self._arming = False
        self._background: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._loaded_route: tuple[str, str, str] | None = None
        self._requested_route = self._default_route()
        self._prepared: dict[str, tuple[Path, Path]] = {}
        self._warm_fingerprints: dict[str, tuple[tuple[str, int, int, int, int, int], ...]] = {}
        self._invalidated = False
        self._available_models: tuple[str, ...] = ()
        self.can_prewarm: Callable[[], bool] = lambda: True
        self._status: dict[str, object] = {}
        self._set_status("unloaded", "模型未加载。首次使用语音输入时准备。")

    def _default_route(self) -> tuple[str, str, str]:
        language = self.config.ibus.default_language.value
        return (
            "auto" if language == "auto_mixed" else language,
            self.config.ibus.default_profile,
            "auto_best",
        )

    @property
    def enabled(self) -> bool:
        return self.config.ibus.enabled

    def status(self) -> dict[str, object]:
        return {
            **self._status,
            "enabled": self.config.ibus.enabled,
            "prewarm_on_startup": self.config.ibus.prewarm_on_startup,
            "idle_unload_seconds": self.config.ibus.idle_unload_seconds,
            "active": self._dictation_active or self._arming,
            "loaded_models": [route.model_id for route in self._current_manifest.routes]
            if self._current_manifest is not None
            else [],
        }

    def _set_status(self, stage: str, message: str) -> None:
        self._status = {"stage": stage, "message": public_error_detail(message)}

    async def start(self) -> None:
        """Publish a cold state immediately; never wait for model preparation at startup."""
        self._loop = asyncio.get_running_loop()
        self._dictation_active = False
        self._write_manifest(self._empty_manifest([]))
        if not self.config.ibus.enabled:
            self._set_status("disabled", "IBus 已关闭。不加载语音输入模型。")
            return
        self._background = asyncio.create_task(self._startup_background())

    async def _startup_background(self) -> None:
        try:
            await self.refresh()
            if self.config.ibus.prewarm_on_startup and self.can_prewarm():
                await self.prewarm()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_status("failed", f"模型准备失败: {exc}")

    async def _cancel_background(self) -> None:
        task = self._background
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if task is not asyncio.current_task():
            self._background = None

    def _cancel_idle(self) -> None:
        if self._idle_task is not None and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None

    async def close(self) -> None:
        await self._cancel_background()
        self._cancel_idle()
        async with self._lock:
            self._dictation_active = False
            self._suspended_for_classroom = True
            await self._stop_locked()
            self._write_manifest(self._empty_manifest(["core worker supervisor stopped"]))
            self._set_status("unloaded", "模型已释放。")

    def request_refresh(self) -> None:
        """Installation changes invalidate warm state but never implicitly load weights."""

        def schedule() -> None:
            self._invalidated = True
            if not self._dictation_active and (self._background is None or self._background.done()):
                self._background = asyncio.create_task(self._refresh_background())

        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(schedule)

    async def _refresh_background(self) -> None:
        try:
            await self.refresh()
        except Exception as exc:
            self._set_status("failed", f"模型目录读取失败: {exc}")

    async def request_prewarm(self) -> dict[str, object]:
        if not self.config.ibus.enabled:
            raise RuntimeError("IBus 已关闭。不能预热模型。")
        if self._dictation_active or self._arming or not self.can_prewarm():
            raise RuntimeError("正在进行语音输入或课堂任务。请在空闲时预热。")
        if self._background is not None and not self._background.done():
            if self._status.get("stage") in {"queued", "checking", "verifying", "loading"}:
                return self.status()
            await self._cancel_background()
        self._set_status("queued", "已安排后台预热。页面可继续使用。")
        self._background = asyncio.create_task(self._prewarm_background())
        return self.status()

    async def _prewarm_background(self) -> None:
        try:
            await self.prewarm()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_status("failed", f"预热失败: {exc}")

    async def prewarm(self) -> ResidentWorkerManifest:
        async with self._lock:
            if not self.config.ibus.enabled:
                raise RuntimeError("IBus is disabled")
            if self._dictation_active or self._arming or not self.can_prewarm():
                raise RuntimeError("workers are busy")
            self._suspended_for_classroom = False
            manifest = await self._prepare_locked(self._default_route())
            self._schedule_idle_release()
            return manifest

    async def release_idle(self) -> dict[str, object]:
        if self._dictation_active or self._arming:
            raise RuntimeError("请先结束语音输入。再释放模型。")
        await self._cancel_background()
        self._cancel_idle()
        async with self._lock:
            if self._dictation_active or self._arming:
                raise RuntimeError("dictation is active")
            await self._stop_locked()
            self._write_manifest(self._empty_manifest([]))
            self._set_status("unloaded", "模型已释放。首次使用时重新准备。")
            return self.status()

    def _schedule_idle_release(self) -> None:
        self._cancel_idle()

        async def release() -> None:
            await asyncio.sleep(self.config.ibus.idle_unload_seconds)
            async with self._lock:
                if not self._dictation_active:
                    await self._stop_locked()
                    self._write_manifest(self._empty_manifest([]))
                    self._set_status("unloaded", "空闲模型已自动释放。")

        self._idle_task = asyncio.create_task(release())

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

        await self._cancel_background()
        self._cancel_idle()
        async with self._lock:
            if self._dictation_active:
                raise RuntimeError("cannot start classroom inference during active dictation")
            self._suspended_for_classroom = True
            await self._stop_locked()
            manifest = self._empty_manifest(["GPU is reserved for classroom inference"])
            self._write_manifest(manifest)
            self._set_status("unloaded", "模型已释放。计算资源交给课堂任务。")
            return manifest

    async def resume_for_dictation(
        self,
        language: str | None = None,
        profile: str | None = None,
        model_id: str = "auto_best",
    ) -> ResidentWorkerManifest:
        if not self.config.ibus.enabled:
            raise RuntimeError("IBus is disabled")
        defaults = self._default_route()
        route = (language or defaults[0], profile or defaults[1], model_id)
        if route[0] not in {"zh", "ja", "en", "auto"} or route[1] not in {
            "fast",
            "balanced",
            "accuracy",
        }:
            raise ValueError("invalid dictation route")
        if self._dictation_active or self._arming:
            raise RuntimeError("dictation worker set is already active")
        self._arming = True
        self._cancel_idle()
        try:
            async with self._lock:
                self._suspended_for_classroom = False
                try:
                    manifest = await self._prepare_locked(route)
                    self._dictation_active = manifest.ready
                    if manifest.ready:
                        self._set_status("active", "语音输入模型正在使用。")
                    return manifest
                except BaseException:
                    self._dictation_active = False
                    raise
        finally:
            self._arming = False

    async def _prepare_locked(self, route: tuple[str, str, str]) -> ResidentWorkerManifest:
        if (
            self._loaded_route == route
            and not self._invalidated
            and self._current_manifest is not None
            and self._current_manifest.ready
            and all(process.running for process, _ in self._processes)
            and await asyncio.to_thread(self._warm_files_unchanged)
        ):
            self._set_status("ready", "模型已就绪。相同配置可直接复用。")
            return self._current_manifest
        await self._stop_locked()
        self._write_manifest(self._empty_manifest([]))
        self._invalidated = False
        self._requested_route = route
        manifest = await self._start_locked()
        self._write_manifest(manifest)
        if manifest.ready:
            self._loaded_route = route
            self._set_status("ready", "模型已就绪。相同配置可直接复用。")
        return manifest

    async def end_dictation(self) -> ResidentWorkerManifest:
        async with self._lock:
            self._dictation_active = False
            if (
                not self.config.ibus.enabled
                or self._invalidated
                or self._loaded_route is None
                or self.config.ibus.idle_unload_seconds == 0
            ):
                await self._stop_locked()
                manifest = self._empty_manifest(["resident workers released after dictation"])
                self._write_manifest(manifest)
                self._set_status("unloaded", "模型已释放。")
                return manifest
            self._set_status("ready", "模型暂时保留供下次复用。空闲后自动释放。")
            self._schedule_idle_release()
            assert self._current_manifest is not None
            return self._current_manifest

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
            self._loaded_route = None
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
                self._invalidated = True
                return self._empty_manifest(["refresh deferred until dictation is idle"])
            self._cancel_idle()
            await self._stop_locked()
            self._available_models = (
                await asyncio.to_thread(self._catalog) if self.config.ibus.enabled else ()
            )
            manifest = self._empty_manifest([])
            self._write_manifest(manifest)
            self._set_status(
                "unloaded" if self.config.ibus.enabled else "disabled",
                "模型未加载。首次使用时准备。"
                if self.config.ibus.enabled
                else "IBus 已关闭。不加载模型。",
            )
            return manifest

    def _catalog(self) -> tuple[str, ...]:
        result: list[str] = []
        for entry in self.registry.models:
            if not entry.enabled or "asr" not in entry.tasks or "streaming" not in entry.modes:
                continue
            try:
                if self.manager.installed_revision_metadata(entry.id).name == entry.revision:
                    result.append(entry.id)
            except (ClassScribeError, OSError, ValueError):
                continue
        return tuple(result)

    def planned_profile_models(self) -> tuple[dict[str, str], tuple[ModelEntry, ...]]:
        language, profile, model_id = self._requested_route
        with self.sessions() as session:
            ranking = (
                (model_id,)
                if model_id != "auto_best"
                else tuple(
                    dict.fromkeys(
                        (
                            *self._profile_ranking(session, language, profile),
                            *self._profile_ranking(session, language, "balanced"),
                            *self._profile_ranking(session, language, "fast"),
                        )
                    )
                )
            )
        for identifier in ranking:
            entry = self.registry.model(identifier)
            if (
                not entry.enabled
                or "asr" not in entry.tasks
                or "streaming" not in entry.modes
                or not _supports_language(entry, language)
            ):
                continue
            requirement = (
                entry.resources.measured_vram_mb or entry.resources.estimated_vram_mb
            ) + entry.resources.safety_margin_mb
            if requirement > self.config.hardware.max_vram_mb:
                continue
            try:
                if self.manager.installed_revision_metadata(entry.id).name != entry.revision:
                    continue
            except (ClassScribeError, OSError, ValueError):
                continue
            # One current-route streaming model; accuracy alternatives are loaded only on demand.
            return {
                f"ibus.{language}.{mode}": entry.id for mode in ("fast", "balanced", "accuracy")
            }, (entry,)
        return {}, ()

    def _preflight_models(
        self,
    ) -> tuple[
        dict[str, str], tuple[ModelEntry, ...], dict[str, ModelEntry], dict[str, tuple[Path, Path]]
    ]:
        routes, entries = self.planned_profile_models()
        if not entries:
            raise RuntimeError("没有已安装且支持当前语言的流式模型。")
        language, profile, _model_id = self._requested_route
        required = [self.registry.model("firered_vad"), *entries]
        if language == "auto":
            required.append(self.registry.model("firered_lid"))
        accuracy: dict[str, ModelEntry] = {}
        if profile == "accuracy":
            for code in ("zh", "ja", "en", "auto") if language == "auto" else (language,):
                target = self._installed_accuracy_entry(code)
                if target is not None:
                    accuracy[code] = target
            if language not in accuracy:
                raise RuntimeError("没有已安装的当前语言高精度确认模型。")
        # Check every required installation/environment before any expensive weight scan or load.
        prepared: dict[str, tuple[Path, Path]] = {}
        for entry in (*required, *accuracy.values()):
            if entry.id in prepared:
                continue
            path = self.manager.installed_revision_metadata(entry.id)
            if path.name != entry.revision:
                raise ValueError(f"{entry.id}: active revision differs from registry")
            prepared[entry.id] = (path, self.provisioner.resolve(entry.worker))
        return routes, entries, accuracy, {entry.id: prepared[entry.id] for entry in required}

    def _verify_prepared(
        self, prepared: dict[str, tuple[Path, Path]]
    ) -> tuple[
        dict[str, tuple[Path, Path]], dict[str, tuple[tuple[str, int, int, int, int, int], ...]]
    ]:
        verified: dict[str, tuple[Path, Path]] = {}
        fingerprints = {}
        for model_id, (expected_path, project) in prepared.items():
            before = self.manager.revision_fingerprint(model_id)
            path = self.manager.resolve_for_runtime(model_id)
            if path != expected_path:
                raise ValueError("active model revision changed during preparation")
            if before != self.manager.revision_fingerprint(model_id):
                raise ValueError("model files changed during verification")
            verified[model_id] = (path, project)
            fingerprints[model_id] = before
        return verified, fingerprints

    def _warm_files_unchanged(self) -> bool:
        try:
            return bool(self._warm_fingerprints) and all(
                self.manager.revision_fingerprint(model_id) == previous
                for model_id, previous in self._warm_fingerprints.items()
            )
        except (ClassScribeError, OSError, ValueError):
            return False

    async def _start_locked(self) -> ResidentWorkerManifest:
        if not self.config.ibus.enabled:
            self._set_status("disabled", "IBus 已关闭。不加载模型。")
            return self._empty_manifest(["IBus is disabled"])
        try:
            self._set_status("checking", "正在检查本次所需模型和运行环境。")
            if shutil.which("bwrap") is None:
                raise RuntimeError("Bubblewrap is unavailable")
            profile_routes, entries, accuracy, prepared = await asyncio.to_thread(
                self._preflight_models
            )
            self._set_status("verifying", "依赖齐全。正在校验本次所需模型文件。")
            self._prepared, self._warm_fingerprints = await asyncio.to_thread(
                self._verify_prepared, prepared
            )
            language, _profile, _model_id = self._requested_route
            auxiliary: dict[str, Path | None] = {"vad": None, "lid": None}
            for role in ("vad", "lid") if language == "auto" else ("vad",):
                entry = self.registry.model(f"firered_{role}")
                socket = self.paths.runtime / "workers" / f"firered-{role}.sock"
                self._set_status("loading", f"正在加载 {entry.display_name}。")
                await self._start_entry(entry, socket, expose_gpu=False)
                auxiliary[role] = socket
            routes: list[ResidentWorkerRoute] = []
            for entry in entries:
                socket = self.paths.runtime / "workers" / f"dictation-{entry.id}.sock"
                self._set_status(
                    "loading", f"正在加载 {entry.display_name}。首次准备可能需要一些时间。"
                )
                await self._start_entry(entry, socket)
                routes.append(ResidentWorkerRoute(entry.id, entry.revision, socket))
            sockets = {route.model_id: route.socket_path for route in routes}
            accuracy_routes = {
                code: ResidentAccuracyRoute(
                    entry.id,
                    entry.revision,
                    sockets.get(entry.id),
                    None if entry.id in sockets else self._estimated_accuracy_load_ms(entry),
                )
                for code, entry in accuracy.items()
            }
            default_accuracy = accuracy_routes.get(language)
            return ResidentWorkerManifest(
                True,
                entries[0].id,
                tuple(routes),
                profile_routes,
                auxiliary["vad"],
                auxiliary["lid"],
                default_accuracy.socket_path if default_accuracy is not None else None,
                (),
                datetime.now(UTC).isoformat(),
                accuracy_routes,
            )
        except BaseException as exc:
            await self._stop_locked()
            detail = "模型准备已取消。" if isinstance(exc, asyncio.CancelledError) else str(exc)
            manifest = self._empty_manifest([detail])
            self._write_manifest(manifest)
            self._set_status(
                "unloaded" if isinstance(exc, asyncio.CancelledError) else "failed", detail
            )
            if not isinstance(exc, Exception):
                raise
            return manifest
        finally:
            self._prepared.clear()

    async def _start_entry(
        self, entry: ModelEntry, socket: Path, *, expose_gpu: bool = True
    ) -> None:
        prepared = self._prepared.pop(entry.id, None)
        if prepared is None:
            model_path = await asyncio.to_thread(self.manager.resolve_for_runtime, entry.id)
            project = await asyncio.to_thread(self.provisioner.resolve, entry.worker)
        else:
            model_path, project = prepared
        if model_path.name != entry.revision:
            raise ValueError("active revision differs from the registry")
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
                        **({"streaming": True} if entry.id == "firered_vad" else {}),
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
        self._loaded_route = None
        self._warm_fingerprints.clear()
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
                path = self.manager.installed_revision_metadata(entry.id)
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
            self.manifest_path.with_name("resident-model-catalog.json"),
            json.dumps({"schema_version": 1, "models": list(self._available_models)}) + "\n",
        )
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
