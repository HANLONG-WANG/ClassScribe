"""Local-only diagnostic snapshot and redacted bundle export."""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from classscribe.observability import LocalMetrics, redact
from classscribe.recovery import atomic_write_bytes


@dataclass(frozen=True, slots=True)
class ComponentDiagnostic:
    name: str
    version: str | None
    status: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticSnapshot:
    generated_at: str
    system: dict[str, Any]
    gpu: dict[str, Any]
    components: tuple[ComponentDiagnostic, ...]
    workers: tuple[dict[str, Any], ...] = ()
    models: tuple[dict[str, Any], ...] = ()
    gpu_lease: dict[str, Any] | None = None
    gpu_queue: tuple[dict[str, Any], ...] = ()
    recent_errors: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)


CommandRunner = Callable[[Sequence[str]], tuple[int, str]]


def safe_command_runner(command: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, type(exc).__name__
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return completed.returncode, output[0][:500] if output else ""


class DiagnosticCollector:
    def __init__(
        self,
        *,
        metrics: LocalMetrics | None = None,
        runner: CommandRunner = safe_command_runner,
    ) -> None:
        self.metrics = metrics or LocalMetrics()
        self.runner = runner

    def _component(self, name: str, executable: str, args: tuple[str, ...]) -> ComponentDiagnostic:
        location = shutil.which(executable)
        if location is None:
            return ComponentDiagnostic(name, None, "missing")
        code, output = self.runner((location, *args))
        return ComponentDiagnostic(name, output or None, "ok" if code == 0 else "error")

    def collect(
        self,
        *,
        workers: tuple[dict[str, Any], ...] = (),
        models: tuple[dict[str, Any], ...] = (),
        gpu_lease: dict[str, Any] | None = None,
        gpu_queue: tuple[dict[str, Any], ...] = (),
        recent_errors: tuple[dict[str, Any], ...] = (),
    ) -> DiagnosticSnapshot:
        os_release = platform.freedesktop_os_release()
        gpu_code, gpu_output = self.runner(
            (
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            )
        )
        return DiagnosticSnapshot(
            generated_at=datetime.now(UTC).isoformat(),
            system={
                "distribution": os_release.get("PRETTY_NAME", os_release.get("NAME", "unknown")),
                "kernel": platform.release(),
                "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
                "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
            },
            gpu={"status": "ok" if gpu_code == 0 else "unavailable", "summary": gpu_output},
            components=(
                self._component("FFmpeg", "ffmpeg", ("-version",)),
                self._component("PipeWire", "pipewire", ("--version",)),
                self._component("IBus", "ibus", ("version",)),
                self._component("Portal", "busctl", ("--version",)),
            ),
            workers=workers,
            models=models,
            gpu_lease=gpu_lease,
            gpu_queue=gpu_queue,
            recent_errors=recent_errors,
            metrics=self.metrics.snapshot(),
        )


def snapshot_payload(snapshot: DiagnosticSnapshot) -> dict[str, Any]:
    payload = redact(asdict(snapshot), home=Path.home())
    if not isinstance(payload, dict):
        raise TypeError("redacted diagnostic snapshot must remain a mapping")
    return payload


def export_diagnostic_bundle(destination: Path, snapshot: DiagnosticSnapshot) -> None:
    """Atomically create a local zip containing only redacted structured JSON."""

    atomic_write_bytes(destination, diagnostic_bundle_bytes(snapshot), mode=0o600)


def diagnostic_bundle_bytes(snapshot: DiagnosticSnapshot) -> bytes:
    """Return the same redacted bundle for authenticated HTTP download."""

    serialized = json.dumps(
        snapshot_payload(snapshot), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("diagnostics.json")
        info.external_attr = 0o600 << 16
        archive.writestr(info, serialized)
    return buffer.getvalue()
