"""Session lifecycle and bounded same-user IPC for the dictation daemon."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import stat
import struct
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from classscribe_protocol import (
    MAX_CONTROL_BYTES,
    DictationConfig,
    DictationState,
    DictationStatus,
)

from classscribe_dictationd.runtime import EnergyVAD
from classscribe_dictationd.session import DictationController


class AudioSource(Protocol):
    def start(self, callback: Any, error_callback: Any) -> None: ...

    def stop(self) -> None: ...


class DictationLease(Protocol):
    async def begin(self, session_id: str) -> None: ...

    async def end(self, session_id: str) -> None: ...

    async def prepare_accuracy(self, session_id: str, language: str) -> None: ...


class StreamingVAD(Protocol):
    async def open(self, session_id: str) -> None: ...

    async def voiced(self, pcm_s16le: bytes) -> bool: ...

    async def close(self) -> None: ...


class NullDictationLease:
    """Explicit CPU/test mode; production CLI uses the core scheduler socket."""

    async def begin(self, session_id: str) -> None:
        del session_id

    async def end(self, session_id: str) -> None:
        del session_id

    async def prepare_accuracy(self, session_id: str, language: str) -> None:
        del session_id, language


class SchedulerLeaseClient:
    """Notify the core scheduler without importing core code into dictationd."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path

    async def begin(self, session_id: str) -> None:
        await self._request("begin_dictation", session_id)

    async def end(self, session_id: str) -> None:
        await self._request("end_dictation", session_id)

    async def prepare_accuracy(self, session_id: str, language: str) -> None:
        await self._request("prepare_accuracy", session_id, language=language)

    async def _request(self, action: str, session_id: str, *, language: str | None = None) -> None:
        metadata = self.socket_path.lstat()
        if (
            self.socket_path.is_symlink()
            or not stat.S_ISSOCK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
        ):
            raise RuntimeError("core GPU lease path is not a same-user socket")
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self.socket_path), timeout=1.0
        )
        try:
            payload = {"action": action, "session_id": session_id}
            if language is not None:
                payload["language"] = language
            writer.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
            response_timeout = (
                180.0
                if action == "prepare_accuracy"
                else (35.0 if action == "begin_dictation" else 2.0)
            )
            raw = await asyncio.wait_for(reader.readline(), timeout=response_timeout)
            if not raw.endswith(b"\n") or len(raw) > 4096:
                raise RuntimeError("core returned an invalid GPU lease response")
            response = json.loads(raw)
            if not isinstance(response, dict) or response.get("ok") is not True:
                raise RuntimeError("core rejected dictation GPU lease")
        finally:
            writer.close()
            await writer.wait_closed()


class DictationService:
    def __init__(
        self,
        controller: DictationController,
        source: AudioSource,
        lease: DictationLease,
        *,
        vad: StreamingVAD | None = None,
        available_models: tuple[str, ...] = (),
        available_models_provider: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        self.controller = controller
        self.source = source
        self.lease = lease
        self.vad = vad or EnergyVAD()
        if any(not item or item == "auto_best" for item in available_models):
            raise ValueError("available model IDs must be concrete and non-empty")
        self._available_models = tuple(dict.fromkeys(available_models))
        self._available_models_provider = available_models_provider
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue(maxsize=256)
        self._consumer: asyncio.Task[None] | None = None
        self._candidate_timer: asyncio.Task[None] | None = None
        self._overflow_task: asyncio.Task[None] | None = None
        self._session_id: str | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> DictationStatus:
        async with self._lock:
            if self.controller.current.state is not DictationState.IDLE:
                return self.controller.current
            self._loop = asyncio.get_running_loop()
            session_id = str(uuid4())
            try:
                self.controller.prepare(session_id)
                await self.lease.begin(session_id)
                self._session_id = session_id
                await self.vad.open(session_id)
                status = await self.controller.activate()
                if status.state is DictationState.IDLE:
                    await self._abort_recognizer()
                    await self.vad.close()
                    await self._release_lease()
                    return status
                self._consumer = asyncio.create_task(self._consume(), name="dictation-audio")
                self.source.start(self._from_audio_thread, self._from_audio_error_thread)
                return status
            except Exception as exc:
                self.source.stop()
                await self._abort_recognizer()
                with contextlib.suppress(Exception):
                    await self.vad.close()
                await self._release_lease()
                return self.controller.fail(f"microphone/GPU unavailable: {type(exc).__name__}")

    async def stop(self) -> DictationStatus:
        async with self._lock:
            if self.controller.current.state is DictationState.IDLE:
                return self.controller.current
            self.source.stop()
            try:
                await self._drain_consumer()
                await self.vad.close()
                if self.controller.accuracy_hot_switch_required():
                    session_id = self._session_id
                    if session_id is None:
                        raise RuntimeError("dictation lease was lost before accuracy confirmation")
                    language = await self.controller.accuracy_language()
                    await self.lease.prepare_accuracy(session_id, language)
                    await self.controller.prepare_accuracy()
                status = await self.controller.release(defer_candidates=True)
            except Exception as exc:
                await self._abort_recognizer()
                with contextlib.suppress(Exception):
                    await self.vad.close()
                await self._release_lease()
                return self.controller.fail(f"dictation finalization failed: {type(exc).__name__}")
            if status.state is DictationState.CANDIDATE_SELECT:
                self._candidate_timer = asyncio.create_task(self._auto_select_candidate())
            else:
                await self._release_lease()
            return status

    async def select_candidate(self, index: int) -> DictationStatus:
        async with self._lock:
            if self._candidate_timer is not None:
                self._candidate_timer.cancel()
                self._candidate_timer = None
            status = self.controller.select_candidate(index)
            await self._release_lease()
            return status

    async def cancel(self) -> DictationStatus:
        async with self._lock:
            self.source.stop()
            if self._candidate_timer is not None:
                self._candidate_timer.cancel()
                self._candidate_timer = None
            await self._stop_consumer()
            with contextlib.suppress(Exception):
                await self.vad.close()
            status = await self.controller.cancel()
            await self._release_lease()
            return status

    def configure(self, config: DictationConfig) -> DictationStatus:
        if config.model_id != "auto_best" and config.model_id not in self.available_models:
            raise ValueError("requested dictation model is not installed and preloaded")
        return self.controller.configure(config)

    @property
    def available_models(self) -> tuple[str, ...]:
        if self._available_models_provider is not None:
            return tuple(dict.fromkeys(self._available_models_provider()))
        return self._available_models

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.cancel()

    def _from_audio_thread(self, frame: bytes) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._enqueue, frame)

    def _from_audio_error_thread(self, error: Exception) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._enqueue, error)

    def _enqueue(self, frame: bytes | Exception) -> None:
        if self._queue.full():
            if self._overflow_task is None or self._overflow_task.done():
                self._overflow_task = asyncio.create_task(self._overflow())
            return
        self._queue.put_nowait(frame)

    async def _overflow(self) -> None:
        try:
            self.source.stop()
            self.controller.fail("microphone consumer overflow")
            await self._abort_recognizer()
            await self._cancel_consumer()
            with contextlib.suppress(Exception):
                await self.vad.close()
            await self._release_lease()
        finally:
            self._overflow_task = None

    async def _auto_select_candidate(self) -> None:
        try:
            await asyncio.sleep(0.4)
            async with self._lock:
                if self.controller.current.state is DictationState.CANDIDATE_SELECT:
                    self.controller.select_candidate(0)
                    await self._release_lease()
        except asyncio.CancelledError:
            return
        finally:
            self._candidate_timer = None

    async def _consume(self) -> None:
        while True:
            frame = await self._queue.get()
            try:
                if frame is None:
                    return
                if isinstance(frame, Exception):
                    status = self.controller.fail(
                        f"microphone disconnected: {type(frame).__name__}: {frame}"
                    )
                else:
                    try:
                        status = await self.controller.feed(
                            frame, voiced=await self.vad.voiced(frame)
                        )
                    except Exception as exc:
                        status = self.controller.fail(
                            f"microphone/VAD stream failed: {type(exc).__name__}"
                        )
                if status.state is DictationState.IDLE and status.message:
                    self.source.stop()
                    await self._abort_recognizer()
                    with contextlib.suppress(Exception):
                        await self.vad.close()
                    await self._release_lease()
                    return
            finally:
                self._queue.task_done()

    async def _drain_consumer(self) -> None:
        await self._queue.join()
        await self._stop_consumer()

    async def _stop_consumer(self) -> None:
        task = self._consumer
        if task is None:
            return
        if not task.done():
            await self._queue.put(None)
            await task
        self._consumer = None
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

    async def _cancel_consumer(self) -> None:
        task = self._consumer
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._consumer = None
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

    async def _release_lease(self) -> None:
        session_id = self._session_id
        self._session_id = None
        if session_id is not None:
            with contextlib.suppress(Exception):
                await self.lease.end(session_id)

    async def _abort_recognizer(self) -> None:
        with contextlib.suppress(Exception):
            await self.controller.recognizer.cancel()


class DictationControlServer:
    def __init__(self, socket_path: Path, service: DictationService) -> None:
        self.socket_path = socket_path
        self.service = service
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_path.parent.chmod(0o700)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            if not stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                raise RuntimeError("refusing to replace a non-socket dictation control path")
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, self.socket_path)
        self.socket_path.chmod(0o600)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.service.close()
        if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.lstat().st_mode):
            self.socket_path.unlink()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_id: object = None
        try:
            _require_same_uid(writer)
            raw = await reader.readline()
            if not raw or len(raw) > MAX_CONTROL_BYTES or not raw.endswith(b"\n"):
                raise ValueError("invalid dictation control frame")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("dictation request must be an object")
            request_id = request.get("request_id")
            action = request.get("action")
            params = request.get("params", {})
            if not isinstance(request_id, str) or not isinstance(action, str):
                raise ValueError("dictation request identity is invalid")
            if not isinstance(params, Mapping):
                raise ValueError("dictation params must be an object")
            response = await self._dispatch(action, params)
            payload = {"ok": True, "request_id": request_id, **response}
        except Exception as exc:
            payload = {
                "ok": False,
                "request_id": request_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
        writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    async def _dispatch(self, action: str, params: Mapping[str, object]) -> dict[str, object]:
        since = params.get("since_revision", 0)
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise ValueError("since_revision must be a non-negative integer")
        if action == "begin":
            await self.service.start()
        elif action == "release":
            await self.service.stop()
        elif action == "cancel":
            await self.service.cancel()
        elif action == "toggle":
            if self.service.controller.current.state is DictationState.IDLE:
                await self.service.start()
            else:
                await self.service.stop()
        elif action == "configure":
            value = params.get("config")
            if not isinstance(value, Mapping):
                raise ValueError("configure requires a config object")
            self.service.configure(DictationConfig.from_mapping(value))
        elif action == "select_candidate":
            index = params.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("candidate index must be an integer")
            await self.service.select_candidate(index)
        elif action != "status":
            raise ValueError("unsupported dictation action")
        controller = self.service.controller
        return {
            "current": controller.current.as_dict(),
            "events": [item.as_dict() for item in controller.events_after(since)],
            "config": controller.config.as_dict(),
            "available_models": list(self.service.available_models),
        }


def _require_same_uid(writer: asyncio.StreamWriter) -> None:
    connection = writer.get_extra_info("socket")
    if connection is None or not hasattr(socket, "SO_PEERCRED"):
        return
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = struct.unpack("3i", credentials)
    if uid != os.getuid():
        raise PermissionError("dictation control peer belongs to another user")
