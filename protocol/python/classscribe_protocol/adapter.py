"""Worker adapter contract and a safe dependency-light base implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from classscribe_protocol.messages import RPCErrorCode


class AdapterError(RuntimeError):
    def __init__(self, code: RPCErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class WorkerAdapter(Protocol):
    worker_id: str
    model_id: str
    model_revision: str

    @property
    def capabilities(self) -> tuple[str, ...]: ...

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]: ...


Handler = Callable[[Mapping[str, Any], asyncio.Event], Awaitable[Mapping[str, Any]]]


class StatefulAdapter:
    """Shared state lifecycle; concrete model families register inference handlers."""

    def __init__(
        self,
        worker_id: str,
        *,
        capabilities: tuple[str, ...],
        supported_methods: tuple[str, ...],
    ) -> None:
        self.worker_id = worker_id
        self.model_id = f"{worker_id}_unloaded"
        self.model_revision = "0" * 40
        self._capabilities = capabilities
        self._supported = frozenset(supported_methods)
        self._handlers: dict[str, Handler] = {}
        self._loaded_path: Path | None = None
        self._streams: dict[str, bytearray] = {}

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self._capabilities

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if cancelled.is_set():
            raise asyncio.CancelledError
        if method == "health":
            return {"status": "ok", "loaded": self._loaded_path is not None}
        if method == "capabilities":
            return {
                "worker_id": self.worker_id,
                "capabilities": list(self._capabilities),
                "methods": sorted(self._supported),
            }
        if method == "load":
            path = Path(str(params["model_path"]))
            if path.is_symlink() or not path.exists():
                raise AdapterError(RPCErrorCode.MODEL_LOAD_FAILED, "model_path is unavailable")
            self.model_id = str(params["model_id"])
            self.model_revision = str(params["model_revision"])
            self._loaded_path = path
            return {"loaded": True}
        if method == "unload":
            self._streams.clear()
            self._loaded_path = None
            return {"unloaded": True}
        if self._loaded_path is None:
            raise AdapterError(RPCErrorCode.MODEL_NOT_LOADED, "load must succeed before inference")
        if method not in self._supported:
            raise AdapterError(
                RPCErrorCode.METHOD_NOT_SUPPORTED,
                f"{self.worker_id} does not implement {method}",
            )
        if method in self._handlers:
            return await self._handlers[method](params, cancelled)
        if method == "stream_open":
            stream_id = str(params["stream_id"])
            if stream_id in self._streams:
                raise AdapterError(RPCErrorCode.INVALID_REQUEST, "stream already exists")
            self._streams[stream_id] = bytearray()
            return {"stream_id": stream_id, "opened": True}
        if method == "stream_push":
            stream_id = str(params["stream_id"])
            if stream_id not in self._streams:
                raise AdapterError(RPCErrorCode.INVALID_REQUEST, "unknown stream")
            self._streams[stream_id].extend(params["pcm_s16le"])
            return {"stream_id": stream_id, "accepted_samples": len(params["pcm_s16le"]) // 2}
        if method == "stream_flush":
            stream_id = str(params["stream_id"])
            if stream_id not in self._streams:
                raise AdapterError(RPCErrorCode.INVALID_REQUEST, "unknown stream")
            return {"stream_id": stream_id, "flushed": True}
        if method == "stream_close":
            stream_id = str(params["stream_id"])
            pcm = self._streams.pop(stream_id, None)
            if pcm is None:
                raise AdapterError(RPCErrorCode.INVALID_REQUEST, "unknown stream")
            return {"stream_id": stream_id, "closed": True, "total_samples": len(pcm) // 2}
        raise AdapterError(
            RPCErrorCode.METHOD_NOT_SUPPORTED,
            f"{self.worker_id} has no handler for {method}",
        )
