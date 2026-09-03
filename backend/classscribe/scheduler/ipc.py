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
from collections.abc import Callable
from pathlib import Path

from classscribe.scheduler.gpu import GPULeaseManager

MAX_LEASE_MESSAGE = 4096


class GPULeaseIPCServer:
    def __init__(
        self,
        socket_path: Path,
        manager: GPULeaseManager,
        *,
        on_begin: Callable[[], object] | None = None,
        on_end: Callable[[], object] | None = None,
        on_prepare_accuracy: Callable[[str], object] | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.manager = manager
        self.on_begin = on_begin
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

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        had_sessions = bool(self._sessions)
        while self._sessions:
            self._sessions.pop()
            await self.manager.end_dictation()
        if had_sessions and self.on_end is not None:
            result = self.on_end()
            if inspect.isawaitable(result):
                await result
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
                if session_id not in self._sessions:
                    was_empty = not self._sessions
                    self._sessions.add(session_id)
                    await self.manager.begin_dictation()
                    try:
                        if was_empty and self.on_begin is not None:
                            result = self.on_begin()
                            if inspect.isawaitable(result):
                                await result
                    except BaseException:
                        self._sessions.remove(session_id)
                        await self.manager.end_dictation()
                        if not self._sessions and self.on_end is not None:
                            rollback = self.on_end()
                            if inspect.isawaitable(rollback):
                                await rollback
                        raise
            elif action == "end_dictation":
                if session_id in self._sessions:
                    self._sessions.remove(session_id)
                    await self.manager.end_dictation()
                    if not self._sessions and self.on_end is not None:
                        result = self.on_end()
                        if inspect.isawaitable(result):
                            await result
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
        except Exception as exc:
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        writer.write(json.dumps(payload).encode("utf-8") + b"\n")
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    @staticmethod
    def _require_same_uid(writer: asyncio.StreamWriter) -> None:
        connection = writer.get_extra_info("socket")
        if connection is None or not hasattr(socket, "SO_PEERCRED"):
            return
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _pid, uid, _gid = struct.unpack("3i", credentials)
        if uid != os.getuid():
            raise PermissionError("GPU lease peer belongs to another user")
