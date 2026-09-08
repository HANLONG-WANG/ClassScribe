"""Versioned loopback-only core API and packaged WebUI application."""

from __future__ import annotations

import asyncio
import html
import json
import os
import stat
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from classscribe_protocol import (
    PROTOCOL_VERSION,
    DictationIPCClient,
    DictationIPCError,
    default_dictation_socket,
)
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from classscribe.api.routes import create_api_router
from classscribe.api.runtime import LazyService
from classscribe.api.security import LocalSecurityMiddleware
from classscribe.api.service import ClassScribeService
from classscribe.config import AppConfig
from classscribe.diagnostics import (
    DiagnosticCollector,
    DiagnosticSnapshot,
    diagnostic_bundle_bytes,
    snapshot_payload,
)
from classscribe.errors import ClassScribeError, ErrorCode, public_error_detail
from classscribe.paths import AppPaths
from classscribe.scheduler import GPULeaseIPCServer, GPULeaseManager
from classscribe.version import __version__

WEBUI_TOKEN_PLACEHOLDER = "__CLASSSCRIBE_API_TOKEN__"


def health_status() -> dict[str, str | int]:
    return {
        "service": "classscribe-core",
        "status": "ok",
        "version": __version__,
        "protocol_version": PROTOCOL_VERSION,
    }


def create_app(
    *,
    config: AppConfig | None = None,
    api_token: str | None = None,
    csrf_token: str | None = None,
    diagnostic_provider: Callable[[], DiagnosticSnapshot] | None = None,
    service: ClassScribeService | None = None,
    enable_scheduler_ipc: bool = False,
    runtime_paths: AppPaths | None = None,
    static_directory: Path | None = None,
) -> FastAPI:
    api_service = service or LazyService(config)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        lease_server: GPULeaseIPCServer | None = None
        resident_workers: Any | None = None
        try:
            concrete = api_service.get() if isinstance(api_service, LazyService) else api_service
            pipeline = concrete.pipeline
            if enable_scheduler_ipc:
                paths = runtime_paths or AppPaths.from_environment()
                paths.ensure()
                resident_workers = concrete.resident_workers

                async def prepare_dictation(options: Mapping[str, str] | None = None) -> None:
                    if resident_workers is not None and not getattr(
                        resident_workers, "enabled", True
                    ):
                        raise RuntimeError("IBus is disabled")
                    if pipeline is not None:
                        await pipeline.arm_dictation_at_safe_boundary()
                    if resident_workers is not None:
                        manifest = (
                            await resident_workers.resume_for_dictation(
                                language=options.get("language"),
                                profile=options.get("profile"),
                                model_id=options.get("model_id", "auto_best"),
                            )
                            if options
                            else await resident_workers.resume_for_dictation()
                        )
                        if not manifest.ready:
                            raise RuntimeError(
                                "; ".join(manifest.errors)
                                or "resident dictation workers are unavailable"
                            )

                async def finish_dictation() -> None:
                    if resident_workers is not None:
                        await resident_workers.end_dictation()
                    if pipeline is not None:
                        pipeline.resume_preempted()

                async def prepare_accuracy(language: str) -> None:
                    if resident_workers is None:
                        raise RuntimeError("resident worker supervisor is unavailable")
                    await resident_workers.prepare_accuracy(language)

                lease_server = GPULeaseIPCServer(
                    paths.runtime / "gpu-lease.sock",
                    GPULeaseManager(),
                    on_prepare=prepare_dictation,
                    on_end=finish_dictation,
                    on_prepare_accuracy=prepare_accuracy,
                )
                await lease_server.start()
            if pipeline is not None:
                pipeline.start_recovered_jobs()
            if resident_workers is not None:
                await resident_workers.start()
            yield
        finally:
            if lease_server is not None:
                await lease_server.close()
            if resident_workers is not None:
                await resident_workers.close()

    application = FastAPI(
        title="ClassScribe Core",
        version=__version__,
        description="Versioned, loopback-only local classroom transcription API.",
        lifespan=lifespan,
    )
    application.add_middleware(LocalSecurityMiddleware, api_token=api_token, csrf_token=csrf_token)
    provide_diagnostics = diagnostic_provider or DiagnosticCollector().collect

    @application.exception_handler(ClassScribeError)
    async def classscribe_error(_request: Request, exc: ClassScribeError) -> JSONResponse:
        http_status = 404 if exc.code is ErrorCode.INVALID_FILE_ID else 409
        return JSONResponse(
            status_code=http_status,
            content={
                "error": {
                    "code": exc.code.value,
                    "detail": public_error_detail(exc.detail),
                }
            },
        )

    @application.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str | int]:
        return health_status()

    @application.get("/api/v1/diagnostics")
    async def diagnostics() -> dict[str, Any]:
        result = snapshot_payload(provide_diagnostics())
        result["ibus"] = await _ibus_status(runtime_paths)
        return result

    @application.get("/api/v1/diagnostics/bundle")
    async def diagnostic_bundle() -> Response:
        return Response(
            diagnostic_bundle_bytes(provide_diagnostics()),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="classscribe-diagnostics.zip"'},
        )

    @application.get("/api/v1/ibus/status")
    async def ibus_status() -> dict[str, object]:
        return await _ibus_status(runtime_paths)

    def worker_supervisor() -> Any:
        supervisor = api_service.resident_workers
        if supervisor is None:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "语音输入模型管理器不可用。")
        return supervisor

    @application.get("/api/v1/ibus/workers")
    async def ibus_workers() -> dict[str, object]:
        return dict(worker_supervisor().status())

    @application.post("/api/v1/ibus/workers/prepare", status_code=202)
    async def prewarm_ibus_workers() -> dict[str, object]:
        try:
            return dict(await worker_supervisor().request_prewarm())
        except RuntimeError as exc:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, str(exc)) from exc

    @application.post("/api/v1/ibus/workers/release")
    async def release_ibus_workers() -> dict[str, object]:
        try:
            return dict(await worker_supervisor().release_idle())
        except RuntimeError as exc:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, str(exc)) from exc

    application.include_router(create_api_router(api_service))  # type: ignore[arg-type]
    if static_directory is not None:
        index_path = static_directory / "index.html"
        if static_directory.is_symlink() or not index_path.is_file() or index_path.is_symlink():
            raise ValueError("WebUI directory must contain a non-symlink index.html")
        if api_token is None:
            raise ValueError("WebUI requires an API token")
        index_template = index_path.read_text(encoding="utf-8")
        if index_template.count(WEBUI_TOKEN_PLACEHOLDER) != 1:
            raise ValueError("WebUI index.html must contain exactly one API token placeholder")
        rendered_index = index_template.replace(
            WEBUI_TOKEN_PLACEHOLDER,
            html.escape(api_token, quote=True),
        )

        @application.get("/", include_in_schema=False)
        @application.get("/index.html", include_in_schema=False)
        async def webui_index() -> HTMLResponse:
            return HTMLResponse(rendered_index)

        application.mount("/", StaticFiles(directory=static_directory, html=True), name="webui")

    return application


app = create_app()


async def _ibus_status(paths: AppPaths | None) -> dict[str, object]:
    socket_path = (
        paths.runtime / "dictationd.sock" if paths is not None else default_dictation_socket()
    )
    try:
        response = await asyncio.to_thread(DictationIPCClient(socket_path).request, "status")
        current = response.get("current", {})
        config = response.get("config", {})
        models = response.get("available_models", [])
        dictation: dict[str, object] = {
            "available": True,
            "current": current if isinstance(current, dict) else {},
            "config": config if isinstance(config, dict) else {},
            "available_models": models if isinstance(models, list) else [],
        }
    except (DictationIPCError, OSError, ValueError) as exc:
        dictation = {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    portal = _portal_status(socket_path.parent / "portal-status.json")
    return {
        "dictationd": dictation,
        "portal": portal,
        "ibus_input_source_fallback": True,
    }


def _portal_status(path: Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > 16_384
        ):
            raise ValueError("portal diagnostic file is unsafe")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("portal diagnostic file is invalid")
        return value
    except (OSError, ValueError) as exc:
        return {
            "available": False,
            "diagnostic": (
                f"Portal status unavailable ({type(exc).__name__}); use the IBus input source"
            ),
        }
