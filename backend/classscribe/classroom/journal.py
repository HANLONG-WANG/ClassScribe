"""Atomic model-response checkpoints for resumable audio windows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from classscribe_protocol import ProtocolError, RPCRequest, RPCResponse

from classscribe.activity import report_activity
from classscribe.recovery import atomic_write_text


class ResponseJournal:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    async def call(
        self,
        request: RPCRequest,
        identity: dict[str, Any],
        invoke: Callable[[], Awaitable[RPCResponse]],
    ) -> RPCResponse:
        key = hashlib.sha256(
            json.dumps(
                {"version": 1, "request": asdict(request), "identity": identity},
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        path = self.directory / f"{key}.json"
        if self.directory.is_symlink() or path.is_symlink():
            raise ProtocolError("structural journal must not contain symlinks")
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("oversized structural journal entry")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["segments"] = tuple(payload["segments"])
            payload["warnings"] = tuple(payload["warnings"])
            result = RPCResponse(**payload)
            if (
                result.ok
                and result.request_id == request.request_id
                and result.job_id == request.job_id
                and result.model_id == identity["model_id"]
                and result.model_revision == identity["revision"]
            ):
                report_activity("restore_window_result", model_id=identity["model_id"], force=True)
                return result
        except (OSError, ValueError, KeyError, TypeError, ProtocolError):
            pass
        result = await invoke()
        if result.ok:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(asdict(result), ensure_ascii=False))
        return result
