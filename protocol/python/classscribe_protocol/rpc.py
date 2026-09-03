"""Async UDS RPC client/server with timeouts, cancellation, and peer isolation."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import stat
import struct
import tempfile
import time
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from classscribe_protocol.adapter import AdapterError, WorkerAdapter
from classscribe_protocol.envelope import ProtocolError
from classscribe_protocol.framing import read_frame, write_frame
from classscribe_protocol.messages import (
    RPCErrorCode,
    RPCRequest,
    RPCResponse,
    validate_readonly_audio_path,
)


class RPCClient:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path

    async def call(self, request: RPCRequest) -> RPCResponse:
        try:
            async with asyncio.timeout(request.deadline_ms / 1000):
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
                try:
                    await write_frame(writer, request)
                    response = await read_frame(reader, RPCResponse)
                    if (
                        response.request_id != request.request_id
                        or response.job_id != request.job_id
                    ):
                        raise ProtocolError("response correlation IDs do not match request")
                    return response
                finally:
                    writer.close()
                    await writer.wait_closed()
        except TimeoutError as exc:
            raise ProtocolError("RPC deadline exceeded") from exc
        except OSError as exc:
            raise ProtocolError(f"worker transport failed: {exc}") from exc


class RPCServer:
    def __init__(
        self,
        socket_path: Path,
        adapter: WorkerAdapter,
        *,
        allowed_data_roots: tuple[Path, ...] = (),
    ) -> None:
        self.socket_path = socket_path
        self.adapter = adapter
        self.allowed_data_roots = allowed_data_roots
        self._server: asyncio.AbstractServer | None = None
        self._tasks: dict[str, tuple[asyncio.Task[Mapping[str, Any]], asyncio.Event]] = {}

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_path.parent.chmod(0o700)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            mode = self.socket_path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise ProtocolError("refusing to replace a non-socket RPC path")
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_connection, self.socket_path)
        self.socket_path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task, event in self._tasks.values():
            event.set()
            task.cancel()
        if self._tasks:
            await asyncio.gather(
                *(item[0] for item in self._tasks.values()), return_exceptions=True
            )
        self._tasks.clear()
        if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.lstat().st_mode):
            self.socket_path.unlink()

    async def __aenter__(self) -> RPCServer:
        await self.start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request: RPCRequest | None = None
        try:
            self._require_same_uid(writer)
            request = await read_frame(reader, RPCRequest)
            response = await self._dispatch(request)
            await write_frame(writer, response)
        except (ProtocolError, AdapterError) as exc:
            if request is not None:
                code = (
                    exc.code.value
                    if isinstance(exc, AdapterError)
                    else RPCErrorCode.INVALID_REQUEST.value
                )
                await write_frame(writer, self._error_response(request, code, str(exc)))
        except Exception as exc:  # keep an isolated worker failure from reaching core
            if request is not None:
                with contextlib.suppress(Exception):
                    await write_frame(
                        writer,
                        self._error_response(
                            request, RPCErrorCode.INTERNAL.value, f"{type(exc).__name__}: {exc}"
                        ),
                    )
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _dispatch(self, request: RPCRequest) -> RPCResponse:
        if request.method == "cancel":
            target_id = str(request.params["target_request_id"])
            running = self._tasks.get(target_id)
            if running is not None:
                running[1].set()
                running[0].cancel()
            return self._success_response(request, {"cancelled": running is not None})
        if request.method in {"transcribe_batch", "align", "vad", "lid", "diarize"}:
            validate_readonly_audio_path(str(request.params["audio_path"]), self.allowed_data_roots)
        cancelled = asyncio.Event()
        task = asyncio.create_task(self._dispatch_adapter(request, cancelled))
        self._tasks[request.request_id] = (task, cancelled)
        started = time.perf_counter()
        try:
            async with asyncio.timeout(request.deadline_ms / 1000):
                result = dict(await task)
        except TimeoutError:
            cancelled.set()
            task.cancel()
            return self._error_response(
                request, RPCErrorCode.DEADLINE_EXCEEDED.value, "worker deadline exceeded"
            )
        except asyncio.CancelledError:
            return self._error_response(request, RPCErrorCode.CANCELLED.value, "request cancelled")
        except AdapterError as exc:
            return self._error_response(request, exc.code.value, exc.detail)
        finally:
            self._tasks.pop(request.request_id, None)
        metrics = dict(result.pop("metrics", {}))
        metrics.setdefault("worker_wall_ms", round((time.perf_counter() - started) * 1000, 3))
        segments = tuple(result.pop("segments", ()))
        raw_text = str(result.pop("raw_text", ""))
        normalized_text = str(result.pop("normalized_text", raw_text))
        language = result.pop("language", None)
        warnings = tuple(str(item) for item in result.pop("warnings", ()))
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id=self.adapter.model_id,
            model_revision=self.adapter.model_revision,
            raw_text=raw_text,
            normalized_text=normalized_text,
            language=str(language) if language is not None else None,
            segments=segments,
            metrics=metrics,
            warnings=warnings,
            result=result,
        )

    async def _dispatch_adapter(
        self, request: RPCRequest, cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if request.method != "transcribe_pcm":
            result = await self.adapter.dispatch(request.method, request.params, cancelled)
            if request.method == "capabilities":
                methods = result.get("methods")
                if isinstance(methods, list) and "transcribe_batch" in methods:
                    return {
                        **result,
                        "methods": sorted({*(str(item) for item in methods), "transcribe_pcm"}),
                    }
            return result
        params = request.params
        pcm = bytes(params["pcm_s16le"])
        absolute_start = int(params["absolute_start_sample"])
        core_start = int(params["core_start_sample"])
        total_samples = len(pcm) // 2
        duration_seconds = total_samples / 16_000
        with tempfile.TemporaryDirectory(prefix="classscribe-final-") as temporary:
            audio = Path(temporary) / "utterance.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(pcm)
            audio.chmod(0o400)
            batch_params: dict[str, Any] = {
                "request_contract": "body-asr-v1",
                "audio_path": str(audio),
                "start_sample": 0,
                "end_sample": total_samples,
                "core_start_sample": core_start - absolute_start,
                "core_end_sample": total_samples,
                "sample_rate": 16_000,
                "language": params["language"],
                "manual_language": True,
                "candidate_role": "primary",
                "experimental_enabled": False,
                "hints": [],
                "rolling_context": list(params.get("rolling_context", [])),
                "decode": {
                    "temperature": 0.0,
                    "do_sample": False,
                    "batch_size": 1,
                    "mixed_length_batch": False,
                    "seed": 0,
                    "max_new_tokens": max(32, min(512, round(duration_seconds * 16))),
                    "max_output_characters": max(128, min(4096, round(duration_seconds * 128))),
                },
                "batch_items": 1,
            }
            result = dict(await self.adapter.dispatch("transcribe_batch", batch_params, cancelled))
        segments = result.get("segments", ())
        if isinstance(segments, (list, tuple)):
            result["segments"] = [
                _offset_segment(dict(segment), absolute_start)
                for segment in segments
                if isinstance(segment, Mapping)
            ]
        return result

    def _success_response(self, request: RPCRequest, result: Mapping[str, Any]) -> RPCResponse:
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=True,
            model_id=self.adapter.model_id,
            model_revision=self.adapter.model_revision,
            result=dict(result),
        )

    def _error_response(self, request: RPCRequest, code: str, detail: str) -> RPCResponse:
        return RPCResponse(
            request_id=request.request_id,
            job_id=request.job_id,
            ok=False,
            model_id=self.adapter.model_id,
            model_revision=self.adapter.model_revision,
            error_code=code,
            error_detail=detail,
        )

    @staticmethod
    def _require_same_uid(writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None or not hasattr(socket, "SO_PEERCRED"):
            return
        credentials = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _pid, uid, _gid = struct.unpack("3i", credentials)
        if uid != os.getuid():
            raise ProtocolError("worker RPC peer belongs to another user")


def _offset_segment(segment: dict[str, Any], offset: int) -> dict[str, Any]:
    for name in ("start_sample", "end_sample"):
        if isinstance(segment.get(name), int):
            segment[name] += offset
    words = segment.get("words")
    if isinstance(words, (list, tuple)):
        segment["words"] = [
            _offset_segment(dict(word), offset) if isinstance(word, Mapping) else word
            for word in words
        ]
    return segment
