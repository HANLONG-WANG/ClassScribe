"""Bubblewrap launch contract for untrusted model and remote-code workers."""

from __future__ import annotations

import shutil
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from classscribe.errors import ClassScribeError, ErrorCode


def _canonical(path: Path, *, directory: bool) -> Path:
    absolute = path.absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ClassScribeError(
            ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, f"sandbox path does not exist: {path}"
        ) from error
    expected_type = resolved.is_dir() if directory else resolved.is_file()
    if absolute != resolved or not expected_type:
        raise ClassScribeError(
            ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT,
            "sandbox paths must be canonical real files/directories without symlinks",
        )
    return resolved


def _canonical_nvidia_device(path: Path) -> Path:
    absolute = path.absolute()
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise ClassScribeError(
            ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, f"GPU device does not exist: {path}"
        ) from error
    if (
        absolute != resolved
        or resolved.parent != Path("/dev")
        or not resolved.name.startswith("nvidia")
        or not stat.S_ISCHR(metadata.st_mode)
    ):
        raise ClassScribeError(
            ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT,
            "only canonical NVIDIA character devices directly below /dev are allowed",
        )
    return resolved


@dataclass(frozen=True, slots=True)
class WorkerSandbox:
    """Build a deny-by-default worker command; execution belongs to the scheduler."""

    bubblewrap: Path

    @classmethod
    def detect(cls) -> WorkerSandbox:
        executable = shutil.which("bwrap")
        if executable is None:
            raise RuntimeError("Bubblewrap is required to run model workers safely")
        return cls(Path(executable))

    def command(
        self,
        *,
        worker_python: Path,
        worker_entrypoint: Path,
        model_revision: Path,
        input_audio: Path,
        output_directory: Path,
        socket_directory: Path,
        worker_arguments: Sequence[str] = (),
        gpu_devices: Sequence[Path] = (),
    ) -> tuple[str, ...]:
        bubblewrap = _canonical(self.bubblewrap, directory=False)
        python = _canonical(worker_python, directory=False)
        worker_root = _canonical(python.parent.parent, directory=True)
        entrypoint = _canonical(worker_entrypoint, directory=False)
        try:
            entry_relative = entrypoint.relative_to(worker_root)
            python_relative = python.relative_to(worker_root)
        except ValueError as error:
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT,
                "worker Python and entrypoint must belong to one isolated environment",
            ) from error
        model = _canonical(model_revision, directory=True)
        audio = _canonical(input_audio, directory=False)
        output = _canonical(output_directory, directory=True)
        socket = _canonical(socket_directory, directory=True)

        command = [
            str(bubblewrap),
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--setenv",
            "PATH",
            "/worker/bin:/usr/bin",
            "--setenv",
            "PYTHONNOUSERSITE",
            "1",
            "--setenv",
            "HF_HUB_OFFLINE",
            "1",
            "--setenv",
            "TRANSFORMERS_OFFLINE",
            "1",
            "--setenv",
            "HF_DATASETS_OFFLINE",
            "1",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--dir",
            "/run",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--ro-bind",
            str(worker_root),
            "/worker",
            "--ro-bind",
            str(model),
            "/model",
            "--ro-bind",
            str(audio),
            "/input/audio",
            "--bind",
            str(output),
            "/output",
            "--bind",
            str(socket),
            "/run/classscribe",
        ]
        for device in gpu_devices:
            canonical_device = _canonical_nvidia_device(device)
            command.extend(("--dev-bind", str(canonical_device), str(canonical_device)))
        command.extend(
            (
                "--chdir",
                "/worker",
                f"/worker/{python_relative.as_posix()}",
                f"/worker/{entry_relative.as_posix()}",
                *worker_arguments,
            )
        )
        return tuple(command)
