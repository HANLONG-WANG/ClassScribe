from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from classscribe.system_check import CheckStatus, FedoraDependencyChecker

ROOT = Path(__file__).resolve().parents[2]
WORKERS = (
    "ark",
    "firered",
    "funasr_experimental",
    "granite",
    "moss_en",
    "moss_td",
    "nemotron",
    "pyannote",
    "qwen",
)


def test_fedora_checker_reports_full_matrix_without_mutating_system() -> None:
    commands: list[tuple[str, ...]] = []

    def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        output = "v24.20.0" if command[0].endswith("node") else "tool 1.0"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    checker = FedoraDependencyChecker(runner=run, which=lambda name: f"/usr/bin/{name}")
    report = checker.check()
    names = {item.dependency for item in report.checks}

    assert {
        "NVIDIA kernel driver",
        "nvidia-smi",
        "FFmpeg",
        "FFprobe",
        "PipeWire",
        "WirePlumber",
        "GStreamer",
        "IBus",
        "PyGObject",
        "GI GLib 2.0",
        "GI Gtk 4.0",
        "GI IBus 1.0",
        "GCC",
        "G++",
        "make",
        "CMake",
        "pkg-config",
        "Git",
        "uv",
        "Bubblewrap worker sandbox",
        "Node.js Active LTS",
        "pnpm",
    } <= names
    assert {"audioconvert", "audioresample", "pipewiresrc", "opusdec", "avdec_mp3"} <= {
        name.removeprefix("GStreamer element ") for name in names
    }
    assert report.mutates_system is False
    assert not any(command[0].endswith(("dnf", "rpm-ostree", "sudo")) for command in commands)
    assert ("/usr/bin/ffmpeg", "-version") in commands
    assert ("/usr/bin/ffprobe", "-version") in commands


@pytest.mark.parametrize(
    ("version", "status", "detail"),
    [
        ("v24.20.0", CheckStatus.OK, "v24.20.0"),
        ("v22.23.1", CheckStatus.INCOMPATIBLE, "Maintenance LTS"),
        ("v26.8.1", CheckStatus.INCOMPATIBLE, "not current Active LTS"),
    ],
)
def test_node_active_lts_is_date_reviewed(version: str, status: CheckStatus, detail: str) -> None:
    def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=version, stderr="")

    result = FedoraDependencyChecker(runner=run, which=lambda name: f"/x/{name}")._node()
    assert result.status is status
    assert detail in result.detail


@pytest.mark.parametrize("worker", WORKERS)
def test_each_isolated_worker_exposes_its_own_cuda_probe(worker: str) -> None:
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--offline",
            "--frozen",
            "--no-sync",
            "--project",
            str(ROOT / "workers" / worker),
            "python",
            str(ROOT / "workers" / worker / "worker.py"),
            "--cuda-check",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout)
    assert result["cuda_toolkit_required"] is False
    assert isinstance(result["cuda_available"], bool)
    assert (ROOT / "workers" / worker / "uv.lock").is_file()
    assert (ROOT / "workers" / worker / ".venv").is_dir()
