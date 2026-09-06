"""Lazy production service composition without import-time XDG mutations."""

from __future__ import annotations

import threading
from typing import Any

from classscribe.api.service import ClassScribeService
from classscribe.classroom import ClassroomPipeline, ProductionStageRunner
from classscribe.config import load_config
from classscribe.db import create_schema, create_sqlite_engine, make_session_factory
from classscribe.models import (
    DictationWorkerSupervisor,
    HuggingFaceDownloader,
    InstalledModelHealthChecker,
    ModelManager,
    SandboxedModelInvoker,
    SQLAlchemyInstallationRecorder,
    WorkerEnvironmentProvisioner,
    load_builtin_manifest_bundle,
    load_model_licenses,
    load_registry,
)
from classscribe.paths import AppPaths
from classscribe.resources import resource_path, resource_root
from classscribe.security import RestrictedCredentialEnvironment


def build_default_service() -> ClassScribeService:
    registry_path = resource_path("config/model-registry.v1.yaml")
    registry = load_registry(registry_path)
    model_licenses = load_model_licenses(resource_path("config/model-licenses.v1.json"))
    manifest_bundle = load_builtin_manifest_bundle(
        resource_path("config/model-manifests/v1/bundle.v1.json"),
        registry,
        model_licenses,
    )
    paths = AppPaths.from_environment()
    paths.ensure()
    engine = create_sqlite_engine(paths.data / "classscribe.sqlite3")
    create_schema(engine)
    sessions = make_session_factory(engine)
    manager = ModelManager(
        paths.cache / "models", recorder=SQLAlchemyInstallationRecorder(sessions)
    )
    credential_path = paths.config / "model-download.env"
    credentials = (
        RestrictedCredentialEnvironment(credential_path).load() if credential_path.exists() else {}
    )
    provisioner = WorkerEnvironmentProvisioner(resource_root(), paths.cache / "worker-environments")
    config = load_config(paths.config / "config.yaml")
    resident_workers = DictationWorkerSupervisor(
        paths, config, registry, manager, provisioner, sessions
    )
    invoker = SandboxedModelInvoker(
        manager,
        provisioner,
        paths.runtime,
        before_gpu_use=resident_workers.suspend_for_classroom_sync,
    )
    pipeline = ClassroomPipeline(
        sessions,
        ProductionStageRunner(paths, config, registry, manager, invoker),
    )
    return ClassScribeService(
        sessions,
        paths,
        registry,
        pipeline=pipeline,
        model_manager=manager,
        model_downloader=HuggingFaceDownloader(token=credentials.get("HF_TOKEN")),
        model_health_check=InstalledModelHealthChecker(provisioner, paths.runtime),
        model_licenses=model_licenses,
        manifest_bundle=manifest_bundle,
        resident_workers=resident_workers,
        allow_pending_jobs_without_pipeline=False,
    )


class LazyService:
    def __init__(self) -> None:
        self._service: ClassScribeService | None = None
        self._lock = threading.Lock()

    def get(self) -> ClassScribeService:
        if self._service is None:
            with self._lock:
                if self._service is None:
                    self._service = build_default_service()
        return self._service

    def __getattr__(self, name: str) -> Any:
        return getattr(self.get(), name)
