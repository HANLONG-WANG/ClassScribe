"""Same-user UDS bridge from dictationd to the core GPU lease manager."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import socket
import stat
import struct
from collections.abc import Callable, Mapping
from pathlib import Path

from classscribe.scheduler.gpu import GPULeaseManager

MAX_LEASE_MESSAGE = 4096


class GPULeaseIPCServer:
    def __init__(
        self,
        socket_path: Path,
        manager: GPULeaseManager,
        *,
        lease_timeout_seconds: float = 45.0,
        on_begin: Callable[[], object] | None = None,
        on_prepare: Callable[[Mapping[str, str]], object] | None = None,
        on_end: Callable[[], object] | None = None,
        on_prepare_accuracy: Callable[[str], object] | None = None,
    ) -> None:
        if lease_timeout_seconds <= 0:
            raise ValueError("lease timeout must be positive")
        self.lease_timeout_seconds = lease_timeout_seconds
        self._deadlines: dict[str, float] = {}
        self._preparations: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._expiry: asyncio.Task[None] | None = None
        self._cleanup_pending = False
        self.socket_path = socket_path
        self.manager = manager
        self.on_begin = on_begin
        self.on_prepare = on_prepare
        self.on_end = on_end
        self.on_prepare_accuracy = on_prepare_accuracy
        self._sessions: set[str] = set()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_path.parent.chmod(0o700)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            if not stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                raise RuntimeError("refusing to replace non-socket GPU lease path")
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, self.socket_path)
        self.socket_path.chmod(0o600)
        self._expiry = asyncio.create_task(self._expire_sessions())

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._expiry is not None:
            self._expiry.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._expiry
            self._expiry = None
        for session_id in tuple(self._deadlines):
            await self._end_session(session_id)
        async with self._lock:
            await self._finish_locked()
        if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.lstat().st_mode):
            self.socket_path.unlink()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self._require_same_uid(writer)
            raw = await reader.readline()
            if not raw or len(raw) > MAX_LEASE_MESSAGE:
                raise ValueError("invalid GPU lease control frame")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("GPU lease request must be an object")
            action = request.get("action")
            session_id = request.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("GPU lease session ID is invalid")
            if action == "begin_dictation":
                options: dict[str, str] = {}
                for key in ("language", "profile", "model_id"):
                    if key in request:
                        value = request[key]
                        if not isinstance(value, str) or not value or len(value) > 128:
                            raise ValueError("invalid dictation preparation options")
                        options[key] = value
                if "language" in options and options["language"] not in {"zh", "ja", "en", "auto"}:
                    raise ValueError("invalid dictation language")
                if "profile" in options and options["profile"] not in {
                    "fast",
                    "balanced",
                    "accuracy",
                }:
                    raise ValueError("invalid dictation profile")
                self._deadlines[session_id] = (
                    asyncio.get_running_loop().time() + self.lease_timeout_seconds
                )
                task = self._preparations.get(session_id)
                if task is None:
                    task = asyncio.create_task(self._begin_session(session_id, options))
                    self._preparations[session_id] = task
                try:
                    await asyncio.shield(task)
                finally:
                    if task.done() and self._preparations.get(session_id) is task:
                        self._preparations.pop(session_id, None)
            elif action == "renew_dictation":
                if session_id not in self._deadlines:
                    raise ValueError("cannot renew an expired dictation lease")
                self._deadlines[session_id] = (
                    asyncio.get_running_loop().time() + self.lease_timeout_seconds
                )
            elif action == "end_dictation":
                await self._end_session(session_id)
            elif action == "prepare_accuracy":
                language = request.get("language")
                if session_id not in self._sessions:
                    raise ValueError("accuracy preparation requires an active session")
                if language not in {"zh", "ja", "en"}:
                    raise ValueError("accuracy preparation requires a concrete language")
                if self.on_prepare_accuracy is None:
                    raise RuntimeError("accuracy hot switching is unavailable")
                result = self.on_prepare_accuracy(str(language))
                if inspect.isawaitable(result):
                    await result
            else:
                raise ValueError("unsupported GPU lease action")
            payload: dict[str, object] = {
                "ok": True,
                "dictation_sessions": len(self._sessions),
            }
        except asyncio.CancelledError:
            payload = {"ok": False, "error": "dictation preparation cancelled or expired"}
        except Exception as exc:
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        writer.write(json.dumps(payload).encode("utf-8") + b"\n")
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    async def _begin_session(
        self, session_id: str, options: Mapping[str, str] | None = None
    ) -> None:
        async with self._lock:
            if session_id in self._sessions:
                return
            was_empty = not self._sessions
            self._sessions.add(session_id)
            await self.manager.begin_dictation()
            try:
                if was_empty and (self.on_prepare is not None or self.on_begin is not None):
                    if self.on_prepare is not None:
                        result = self.on_prepare(options or {})
                    else:
                        assert self.on_begin is not None
                        result = self.on_begin()
                    if inspect.isawaitable(result):
                        await result
            except BaseException:
                await self._remove_locked(session_id)
                raise

    async def _end_session(self, session_id: str) -> None:
        task = self._preparations.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._preparations.pop(session_id, None)
        async with self._lock:
            await self._remove_locked(session_id)

    async def _remove_locked(self, session_id: str) -> None:
        self._deadlines.pop(session_id, None)
        if session_id in self._sessions:
            self._sessions.remove(session_id)
            await self.manager.end_dictation()
            if not self._sessions:
                self._cleanup_pending = True
        await self._finish_locked()

    async def _finish_locked(self) -> None:
        if self._cleanup_pending and not self._sessions:
            if self.on_end is not None:
                result = self.on_end()
                if inspect.isawaitable(result):
                    await result
            self._cleanup_pending = False

    async def _expire_sessions(self) -> None:
        while True:
            await asyncio.sleep(min(1.0, self.lease_timeout_seconds / 3))
            now = asyncio.get_running_loop().time()
            for session_id, deadline in tuple(self._deadlines.items()):
                if deadline <= now:
                    with contextlib.suppress(Exception):
                        await self._end_session(session_id)
            if self._cleanup_pending and not self._sessions:
                async with self._lock:
                    with contextlib.suppress(Exception):
                        await self._finish_locked()

    @staticmethod
    def _require_same_uid(writer: asyncio.StreamWriter) -> None:
        connection = writer.get_extra_info("socket")
        if connection is None or not hasattr(socket, "SO_PEERCRED"):
            return
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _pid, uid, _gid = struct.unpack("3i", credentials)
        if uid != os.getuid():
            raise PermissionError("GPU lease peer belongs to another user")
