"""Reproducible per-worker environments created only during user-confirmed installs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import validate_restricted_directory

CommandRunner = Callable[[Sequence[str], Path, dict[str, str]], None]


def _run_checked(command: Sequence[str], cwd: Path, environment: dict[str, str]) -> None:
    subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )


class WorkerEnvironmentProvisioner:
    """Materialize a lock-addressed worker environment below the user cache."""

    def __init__(
        self,
        source_root: Path,
        cache_root: Path,
        *,
        runner: CommandRunner = _run_checked,
    ) -> None:
        self.source_root = source_root.resolve(strict=True)
        self.cache_root = cache_root
        self.runner = runner
        self._ensure_root()

    def ensure(self, worker_id: str) -> Path:
        if not worker_id.replace("_", "").isalnum():
            raise ValueError("unsafe worker ID")
        source = self.source_root / "workers" / worker_id
        protocol = self.source_root / "protocol" / "python"
        lock = source / "uv.lock"
        if source.is_symlink() or protocol.is_symlink() or not lock.is_file():
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                f"worker source or lock is unavailable: {worker_id}",
            )
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        target = self.cache_root / worker_id / digest
        if self._is_complete(target, worker_id, digest):
            return target / "workers" / worker_id
        if target.exists() or target.is_symlink():
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                "an incomplete worker environment occupies the pinned cache path",
            )
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        staging = Path(tempfile.mkdtemp(prefix=f".{digest[:12]}-", dir=target.parent))
        staging.chmod(0o700)
        try:
            project = staging / "workers" / worker_id
            protocol_target = staging / "protocol" / "python"
            shutil.copytree(source, project, ignore=_ignored_worker_paths)
            shutil.copytree(protocol, protocol_target, ignore=_ignored_python_paths)
            environment = _build_environment(project / ".venv")
            self.runner(
                (sys.executable, "-m", "venv", "--copies", str(project / ".venv")),
                project,
                environment,
            )
            uv = shutil.which("uv")
            if uv is None:
                raise ClassScribeError(
                    ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                    "uv is required to provision the pinned worker environment",
                )
            self.runner(
                (uv, "sync", "--project", str(project), "--frozen", "--no-dev", "--no-editable"),
                project,
                environment,
            )
            python = project / ".venv" / "bin" / "python"
            if python.is_symlink() or not python.is_file() or not (project / "worker.py").is_file():
                raise ClassScribeError(
                    ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                    "provisioned worker lacks a copied Python interpreter or entrypoint",
                )
            (staging / "complete.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "worker_id": worker_id,
                        "lock_sha256": digest,
                        "offline_runtime": True,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(staging, target)
            return target / "workers" / worker_id
        except Exception:
            if staging.exists() and not staging.is_symlink():
                shutil.rmtree(staging)
            raise

    def resolve(self, worker_id: str) -> Path:
        """Resolve an already provisioned lock-addressed environment without installing."""

        if not worker_id.replace("_", "").isalnum():
            raise ValueError("unsafe worker ID")
        source = self.source_root / "workers" / worker_id
        lock = source / "uv.lock"
        if source.is_symlink() or not lock.is_file():
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                f"worker source or lock is unavailable: {worker_id}",
            )
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        target = self.cache_root / worker_id / digest
        if not self._is_complete(target, worker_id, digest):
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                f"worker environment is not provisioned for the pinned lock: {worker_id}",
            )
        return target / "workers" / worker_id

    def _ensure_root(self) -> None:
        if self.cache_root.is_symlink():
            raise ValueError("worker environment root may not be a symlink")
        self.cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cache_root.chmod(0o700)
        validate_restricted_directory(self.cache_root)

    @staticmethod
    def _is_complete(target: Path, worker_id: str, digest: str) -> bool:
        try:
            value = json.loads((target / "complete.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        project = target / "workers" / worker_id
        python = project / ".venv" / "bin" / "python"
        return (
            not target.is_symlink()
            and value.get("schema_version") == 1
            and value.get("worker_id") == worker_id
            and value.get("lock_sha256") == digest
            and python.is_file()
            and not python.is_symlink()
            and (project / "worker.py").is_file()
        )


def _build_environment(venv: Path) -> dict[str, str]:
    allowed = (
        "PATH",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "UV_CACHE_DIR",
    )
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(venv),
            "UV_NO_CONFIG": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _ignored_worker_paths(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {".venv", "__pycache__", ".pytest_cache"}}


def _ignored_python_paths(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {".venv", "__pycache__", ".pytest_cache"}}
