"""Read-only Fedora dependency and per-worker CUDA readiness checks."""

from __future__ import annotations

import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class CheckStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    OPTIONAL_MISSING = "optional_missing"


@dataclass(frozen=True, slots=True)
class DependencyResult:
    dependency: str
    status: CheckStatus
    detail: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class SystemReport:
    platform: str
    checks: tuple[DependencyResult, ...]
    mutates_system: bool = False

    @property
    def ready(self) -> bool:
        return all(item.status is CheckStatus.OK or not item.required for item in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "ready": self.ready,
            "mutates_system": self.mutates_system,
            "checks": [asdict(item) for item in self.checks],
        }


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True, timeout=10)


def _first_line(output: str) -> str:
    return output.strip().splitlines()[0] if output.strip() else "version unavailable"


class FedoraDependencyChecker:
    """Probe dependencies without installing packages or changing configuration."""

    # Reviewed against nodejs/Release schedule.json on 2026-09-03.
    ACTIVE_NODE_LTS_MAJOR = 24
    MAINTENANCE_NODE_LTS_MAJORS = frozenset({22})

    def __init__(
        self,
        *,
        runner: CommandRunner = _run,
        which: Which = shutil.which,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._runner = runner
        self._which = which
        self._environment = os.environ if environment is None else environment

    def _command(
        self,
        dependency: str,
        executable: str,
        version_args: Sequence[str] = ("--version",),
        *,
        required: bool = True,
    ) -> DependencyResult:
        resolved = self._which(executable)
        if resolved is None:
            status = CheckStatus.MISSING if required else CheckStatus.OPTIONAL_MISSING
            return DependencyResult(dependency, status, f"{executable} not found", required)
        try:
            completed = self._runner((resolved, *version_args))
        except (OSError, subprocess.SubprocessError) as error:
            return DependencyResult(
                dependency, CheckStatus.INCOMPATIBLE, f"probe failed: {error}", required
            )
        output = completed.stdout or completed.stderr
        if completed.returncode != 0:
            return DependencyResult(
                dependency,
                CheckStatus.INCOMPATIBLE,
                f"{executable} exited {completed.returncode}: {_first_line(output)}",
                required,
            )
        return DependencyResult(dependency, CheckStatus.OK, _first_line(output), required)

    def _node(self) -> DependencyResult:
        result = self._command("Node.js Active LTS", "node")
        if result.status is not CheckStatus.OK:
            return result
        match = re.search(r"v?(\d+)", result.detail)
        if match is None:
            return DependencyResult(
                result.dependency, CheckStatus.INCOMPATIBLE, "cannot parse Node.js version"
            )
        major = int(match.group(1))
        if major == self.ACTIVE_NODE_LTS_MAJOR:
            return result
        if major in self.MAINTENANCE_NODE_LTS_MAJORS:
            return DependencyResult(
                result.dependency,
                CheckStatus.INCOMPATIBLE,
                f"Node.js {major} is Maintenance LTS; "
                f"install Active LTS {self.ACTIVE_NODE_LTS_MAJOR}",
            )
        return DependencyResult(
            result.dependency,
            CheckStatus.INCOMPATIBLE,
            f"Node.js {major} is not current Active LTS {self.ACTIVE_NODE_LTS_MAJOR}",
        )

    def _python_module(self, dependency: str, module: str) -> DependencyResult:
        try:
            loaded = importlib.import_module(module)
        except (ImportError, ValueError) as error:
            return DependencyResult(dependency, CheckStatus.MISSING, str(error))
        version = getattr(loaded, "__version__", "available")
        return DependencyResult(dependency, CheckStatus.OK, str(version))

    def _gi_namespace(self, namespace: str, version: str) -> DependencyResult:
        try:
            gi = importlib.import_module("gi")
            require_version = gi.require_version
            require_version(namespace, version)
            importlib.import_module(f"gi.repository.{namespace}")
        except (ImportError, ValueError, AttributeError) as error:
            return DependencyResult(f"GI {namespace} {version}", CheckStatus.MISSING, str(error))
        return DependencyResult(
            f"GI {namespace} {version}", CheckStatus.OK, "introspection available"
        )

    def _nvidia_driver(self) -> DependencyResult:
        version_file = Path("/proc/driver/nvidia/version")
        if not version_file.is_file():
            return DependencyResult(
                "NVIDIA kernel driver",
                CheckStatus.OPTIONAL_MISSING,
                "not present; CPU mode remains available",
                required=False,
            )
        try:
            detail = _first_line(version_file.read_text(encoding="utf-8"))
        except OSError as error:
            return DependencyResult(
                "NVIDIA kernel driver", CheckStatus.INCOMPATIBLE, str(error), required=False
            )
        return DependencyResult("NVIDIA kernel driver", CheckStatus.OK, detail, required=False)

    def _gstreamer_element(self, element: str, *, required: bool) -> DependencyResult:
        executable = self._which("gst-inspect-1.0")
        name = f"GStreamer element {element}"
        if executable is None:
            status = CheckStatus.MISSING if required else CheckStatus.OPTIONAL_MISSING
            return DependencyResult(name, status, "gst-inspect-1.0 not found", required)
        try:
            completed = self._runner((executable, element))
        except (OSError, subprocess.SubprocessError) as error:
            return DependencyResult(name, CheckStatus.INCOMPATIBLE, str(error), required)
        if completed.returncode:
            status = CheckStatus.MISSING if required else CheckStatus.OPTIONAL_MISSING
            return DependencyResult(name, status, "plugin element not installed", required)
        return DependencyResult(name, CheckStatus.OK, "available", required)

    def check(self) -> SystemReport:
        checks = [
            self._nvidia_driver(),
            self._command("nvidia-smi", "nvidia-smi", required=False),
            self._command("FFmpeg", "ffmpeg", ("-version",)),
            self._command("FFprobe", "ffprobe", ("-version",)),
            self._command("PipeWire", "pipewire"),
            self._command("WirePlumber", "wireplumber"),
            self._command("GStreamer", "gst-launch-1.0"),
            self._gstreamer_element("audioconvert", required=True),
            self._gstreamer_element("audioresample", required=True),
            self._gstreamer_element("pipewiresrc", required=True),
            self._gstreamer_element("opusdec", required=False),
            self._gstreamer_element("avdec_mp3", required=False),
            self._command("IBus", "ibus", ("version",)),
            self._python_module("PyGObject", "gi"),
            self._gi_namespace("GLib", "2.0"),
            self._gi_namespace("Gtk", "4.0"),
            self._gi_namespace("IBus", "1.0"),
            self._command("GCC", "gcc"),
            self._command("G++", "g++"),
            self._command("make", "make"),
            self._command("CMake", "cmake"),
            self._command("pkg-config", "pkg-config"),
            self._command("Git", "git"),
            self._command("uv", "uv"),
            self._command("Bubblewrap worker sandbox", "bwrap"),
            self._node(),
            self._command("pnpm", "pnpm"),
        ]
        return SystemReport(platform=platform.platform(), checks=tuple(checks))


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    report = FedoraDependencyChecker().check()
    json.dump(report.as_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
