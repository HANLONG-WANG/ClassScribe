"""Lossless, cancellable upload preparation from the canonical audio master."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from classscribe.audio.limits import MAI_MAX_UPLOAD_BYTES
from classscribe.errors import ClassScribeError, ErrorCode


def prepare_mai_upload(source: Path, destination: Path, cancelled: Callable[[], bool]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{uuid4().hex}.flac")
    process: subprocess.Popen[bytes] | None = None
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-threads",
                    "2",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-map_metadata",
                    "-1",
                    "-c:a",
                    "flac",
                    "-n",
                    str(temporary),
                ],
                stdout=subprocess.DEVNULL,
                stderr=errors,
            )
            while True:
                if cancelled():
                    raise ClassScribeError(
                        ErrorCode.JOB_STATE_CONFLICT, "Online audio preparation cancelled"
                    )
                try:
                    code = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if code or not temporary.is_file():
                raise ClassScribeError(
                    ErrorCode.MEDIA_DECODE_FAILED, "Could not prepare FLAC upload"
                )
        if temporary.stat().st_size > MAI_MAX_UPLOAD_BYTES:
            raise ClassScribeError(
                ErrorCode.UPLOAD_TOO_LARGE, "MAI upload exceeds 240 MB; select a shorter clip"
            )
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)
