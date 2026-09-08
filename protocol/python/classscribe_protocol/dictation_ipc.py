"""Small, bounded same-user control client shared by desktop components."""

from __future__ import annotations

import json
import os
import socket
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_CONTROL_BYTES = 64 * 1024


def default_dictation_socket(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime).expanduser()
    else:
        state = env.get("XDG_STATE_HOME")
        base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    if not base.is_absolute():
        raise ValueError("dictation runtime base must be absolute")
    directory = base / "classscribe" if runtime else base / "classscribe" / "run"
    return directory / "dictationd.sock"


class DictationIPCError(RuntimeError):
    pass


class DictationIPCClient:
    def __init__(self, socket_path: Path, *, timeout_seconds: float = 0.35) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def request(self, action: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        if not action or not action.replace("_", "").isalnum():
            raise ValueError("invalid dictation action")
        self._validate_socket()
        payload = (
            json.dumps(
                {"request_id": str(uuid4()), "action": action, "params": dict(params or {})},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) > MAX_CONTROL_BYTES:
            raise DictationIPCError("dictation control request is too large")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(payload)
                response = _read_line(connection)
        except OSError as exc:
            raise DictationIPCError(f"dictation daemon unavailable: {exc}") from exc
        try:
            decoded = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DictationIPCError("dictation daemon returned invalid JSON") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("ok"), bool):
            raise DictationIPCError("dictation daemon returned an invalid response")
        if not decoded["ok"]:
            raise DictationIPCError(str(decoded.get("error", "dictation command failed")))
        return decoded

    def _validate_socket(self) -> None:
        try:
            metadata = self.socket_path.lstat()
        except OSError as exc:
            raise DictationIPCError("dictation daemon socket does not exist") from exc
        if not stat.S_ISSOCK(metadata.st_mode) or self.socket_path.is_symlink():
            raise DictationIPCError("dictation daemon path is not a socket")
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            raise DictationIPCError("dictation daemon socket belongs to another user")


def _read_line(connection: socket.socket) -> str:
    chunks = bytearray()
    while True:
        chunk = connection.recv(min(4096, MAX_CONTROL_BYTES + 1 - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
        if b"\n" in chunk:
            break
        if len(chunks) > MAX_CONTROL_BYTES:
            raise DictationIPCError("dictation control response is too large")
    line, _, _rest = chunks.partition(b"\n")
    if not line or len(line) > MAX_CONTROL_BYTES:
        raise DictationIPCError("dictation control response is empty or too large")
    return line.decode("utf-8")
