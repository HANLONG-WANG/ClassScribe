"""Reproducible per-worker environments created only during user-confirmed installs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import validate_restricted_directory

CommandRunner = Callable[[Sequence[str], Path, dict[str, str]], None]
_UV_BUILD_CONFIG = """\
[extra-build-dependencies]
classscribe-protocol = ["hatchling>=1.27,<2"]
"""
_IGNORED_SOURCE_NAMES = frozenset({".venv", "__pycache__", ".pytest_cache"})


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
        lock_digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        source_digest = _source_sha256(source, protocol)
        lock_root = self.cache_root / worker_id / lock_digest
        target = lock_root / source_digest
        if self._is_complete(target, worker_id, lock_digest, source_digest):
            return target / "workers" / worker_id
        if self._is_compatible_legacy(lock_root, worker_id, lock_digest, source_digest):
            return lock_root / "workers" / worker_id
        if target.exists() or target.is_symlink():
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                "an incomplete worker environment occupies the pinned cache path",
            )
        if lock_root.is_symlink():
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                "the worker environment lock root may not be a symlink",
            )
        lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_root.chmod(0o700)
        validate_restricted_directory(lock_root)
        staging = Path(tempfile.mkdtemp(prefix=f".{source_digest[:12]}-", dir=lock_root))
        staging.chmod(0o700)
        try:
            project = staging / "workers" / worker_id
            protocol_target = staging / "protocol" / "python"
            shutil.copytree(source, project, ignore=_ignored_worker_paths)
            shutil.copytree(protocol, protocol_target, ignore=_ignored_python_paths)
            if _source_sha256(project, protocol_target) != source_digest:
                raise ClassScribeError(
                    ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                    "worker source changed while its environment was being provisioned",
                )
            uv_config = staging / "uv.toml"
            uv_config.write_text(_UV_BUILD_CONFIG, encoding="utf-8")
            uv_config.chmod(0o600)
            environment = _build_environment(project / ".venv")
            uv = shutil.which("uv")
            if uv is None:
                raise ClassScribeError(
                    ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                    "uv is required to provision the pinned worker environment",
                )
            self.runner(
                (
                    uv,
                    "sync",
                    "--project",
                    str(project),
                    "--frozen",
                    "--no-dev",
                    "--no-editable",
                    "--preview",
                    "--config-file",
                    str(uv_config),
                ),
                project,
                environment,
            )
            uv_config.unlink()
            python = project / ".venv" / "bin" / "python"
            try:
                base_python = python.resolve(strict=True)
            except OSError as error:
                raise ClassScribeError(
                    ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                    "provisioned worker lacks its pinned Python interpreter",
                ) from error
            if not base_python.is_file():
                raise ClassScribeError(
                    ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                    "provisioned worker Python interpreter is not a regular file",
                )
            copied_python = python.with_name(".python.classscribe-copy")
            shutil.copy2(base_python, copied_python)
            os.replace(copied_python, python)
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
                        "lock_sha256": lock_digest,
                        "source_sha256": source_digest,
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
        protocol = self.source_root / "protocol" / "python"
        lock = source / "uv.lock"
        if source.is_symlink() or protocol.is_symlink() or not lock.is_file():
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                f"worker source or lock is unavailable: {worker_id}",
            )
        lock_digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        source_digest = _source_sha256(source, protocol)
        lock_root = self.cache_root / worker_id / lock_digest
        target = lock_root / source_digest
        if not self._is_complete(target, worker_id, lock_digest, source_digest):
            if self._is_compatible_legacy(lock_root, worker_id, lock_digest, source_digest):
                return lock_root / "workers" / worker_id
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
    def _is_complete(
        target: Path, worker_id: str, lock_digest: str, source_digest: str
    ) -> bool:
        try:
            value = json.loads((target / "complete.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        project = target / "workers" / worker_id
        protocol = target / "protocol" / "python"
        python = project / ".venv" / "bin" / "python"
        metadata_matches = (
            not target.is_symlink()
            and value.get("schema_version") == 1
            and value.get("worker_id") == worker_id
            and value.get("lock_sha256") == lock_digest
            and value.get("source_sha256") == source_digest
            and python.is_file()
            and not python.is_symlink()
            and (project / "worker.py").is_file()
        )
        if not metadata_matches:
            return False
        try:
            return _source_sha256(project, protocol) == source_digest
        except (ClassScribeError, OSError):
            return False

    @staticmethod
    def _is_compatible_legacy(
        target: Path, worker_id: str, lock_digest: str, source_digest: str
    ) -> bool:
        """Read an old lock-only environment only when its copied sources still match."""

        try:
            value = json.loads((target / "complete.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        project = target / "workers" / worker_id
        protocol = target / "protocol" / "python"
        python = project / ".venv" / "bin" / "python"
        metadata_matches = (
            not target.is_symlink()
            and value.get("schema_version") == 1
            and value.get("worker_id") == worker_id
            and value.get("lock_sha256") == lock_digest
            and "source_sha256" not in value
            and value.get("offline_runtime") is True
            and python.is_file()
            and not python.is_symlink()
            and (project / "worker.py").is_file()
        )
        if not metadata_matches:
            return False
        try:
            return _source_sha256(project, protocol) == source_digest
        except (ClassScribeError, OSError):
            return False


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
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _ignored_worker_paths(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _IGNORED_SOURCE_NAMES}


def _ignored_python_paths(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _IGNORED_SOURCE_NAMES}


def _source_sha256(worker: Path, protocol: Path) -> str:
    digest = hashlib.sha256(b"classscribe-worker-source-v1\0")
    for label, root in (("worker", worker), ("protocol", protocol)):
        if root.is_symlink() or not root.is_dir():
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                f"worker source root is unavailable: {label}",
            )
        for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
            names[:] = sorted(name for name in names if name not in _IGNORED_SOURCE_NAMES)
            for name in names:
                path = Path(directory) / name
                if path.is_symlink():
                    relative = path.relative_to(root)
                    raise ClassScribeError(
                        ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                        f"worker source tree contains a symlink: {label}/{relative.as_posix()}",
                    )
            for filename in sorted(filenames):
                if filename in _IGNORED_SOURCE_NAMES:
                    continue
                path = Path(directory) / filename
                relative = path.relative_to(root)
                if path.is_symlink():
                    raise ClassScribeError(
                        ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                        f"worker source tree contains a symlink: {label}/{relative.as_posix()}",
                    )
                if not path.is_file():
                    raise ClassScribeError(
                        ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                        "worker source tree contains a special file: "
                        f"{label}/{relative.as_posix()}",
                    )
                encoded_name = f"{label}/{relative.as_posix()}".encode()
                content = path.read_bytes()
                digest.update(len(encoded_name).to_bytes(8, "big"))
                digest.update(encoded_name)
                digest.update(len(content).to_bytes(8, "big"))
                digest.update(content)
    build_config = _UV_BUILD_CONFIG.encode()
    digest.update(len(build_config).to_bytes(8, "big"))
    digest.update(build_config)
    return digest.hexdigest()
