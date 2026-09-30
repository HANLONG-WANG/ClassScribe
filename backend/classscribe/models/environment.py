"""Reproducible per-worker environments created during explicit installs or repairs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import validate_restricted_directory

CommandRunner = Callable[[Sequence[str], Path, dict[str, str]], None]
_UV_BUILD_CONFIG = """\
[extra-build-dependencies]
classscribe-protocol = ["hatchling>=1.27,<2"]
"""
_IGNORED_SOURCE_NAMES = frozenset({".venv", "__pycache__", ".pytest_cache"})
_CACHE_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENVIRONMENT_LEASE = ".in-use.lock"


def _run_checked(command: Sequence[str], cwd: Path, environment: dict[str, str]) -> None:
    subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )


def acquire_environment_lease(project: Path) -> int:
    """Pin a published worker environment for the full subprocess lifetime."""
    if project.absolute() != project.resolve(strict=True) or not project.is_dir():
        raise ValueError("worker environment lease requires a canonical project")
    root = project.parent.parent
    if not (root / "complete.json").is_file():
        raise ValueError("worker environment is not published")
    path = root / _ENVIRONMENT_LEASE
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        opened = os.fstat(descriptor)
        present = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (present.st_dev, present.st_ino):
            raise ValueError("worker environment changed while acquiring its lease")
        if not project.is_dir():
            raise ValueError("worker environment was removed before startup")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _exclusive_environment(root: Path, *, create: bool = True) -> Iterator[bool]:
    path = root / _ENVIRONMENT_LEASE
    if not create and not path.exists():
        yield True
        return
    descriptor = os.open(
        path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_CREAT if create else 0), 0o600
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        opened = os.fstat(descriptor)
        present = path.stat(follow_symlinks=False)
        yield (opened.st_dev, opened.st_ino) == (present.st_dev, present.st_ino)
    finally:
        os.close(descriptor)


def _logical_payload_bytes(root: Path) -> int:
    total = 0
    for directory, _names, files in os.walk(root, followlinks=False):
        for name in files:
            metadata = (Path(directory) / name).lstat()
            if stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
    return total


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

    def ensure(self, worker_id: str, *, repair: bool = False) -> Path:
        """Build separately from runtime; serialize publishers across threads/processes."""
        if not worker_id.replace("_", "").isalnum():
            raise ValueError("unsafe worker ID")
        if (self.cache_root / worker_id).is_symlink():
            raise ValueError("worker environment root may not be a symlink")
        lock_path = self.cache_root / f".{worker_id}.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            if repair:
                state = self.inspect(worker_id)
                target = Path(state["target"])
                if target.parent.is_symlink() or target.is_symlink():
                    raise ValueError("worker environment path may not be a symlink")
                if state["status"] == "incomplete" and target.exists():
                    # Keep the damaged copy for diagnosis; never change a live environment.
                    with _exclusive_environment(target) as available:
                        if not available:
                            raise ClassScribeError(
                                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                                "release workers using this environment before repairing it",
                            )
                        backup = Path(tempfile.mkdtemp(prefix=".damaged-", dir=target.parent))
                        os.replace(target, backup / "environment")
            project = self._ensure(worker_id)
            # Complete rollback versions require an explicit cleanup request.
            self._cleanup_locked(worker_id, keep_versions=None, keep_damaged=1)
            return project

    def _ensure(self, worker_id: str) -> Path:
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
                        "payload_logical_bytes": _logical_payload_bytes(staging),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(staging, target)
            return target / "workers" / worker_id
        finally:
            if staging.exists() and not staging.is_symlink():
                shutil.rmtree(staging)

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
                f"worker environment is not provisioned for the pinned lock/source: {worker_id}; "
                f"{self.inspect(worker_id)['status']}; "
                "请在模型管理中点击“修复运行环境”后重试。源码更新也需要重建环境",
            )
        return target / "workers" / worker_id

    def inspect(self, worker_id: str) -> dict[str, str]:
        """Read readiness without installing, downloading, or modifying cached sources."""
        if not worker_id.replace("_", "").isalnum():
            raise ValueError("unsafe worker ID")
        source = self.source_root / "workers" / worker_id
        protocol = self.source_root / "protocol" / "python"
        lock_digest = hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest()
        source_digest = _source_sha256(source, protocol)
        worker_root = self.cache_root / worker_id
        lock_root = worker_root / lock_digest
        target = lock_root / source_digest
        if self._is_complete(target, worker_id, lock_digest, source_digest) or (
            self._is_compatible_legacy(lock_root, worker_id, lock_digest, source_digest)
        ):
            status = "ready"
        elif (
            target.exists()
            or target.is_symlink()
            or lock_root.is_symlink()
            or worker_root.is_symlink()
        ):
            status = "incomplete"
        elif (lock_root / "complete.json").is_file() or any(lock_root.glob("*/complete.json")):
            status = "source_changed"
        elif any(worker_root.glob("*/complete.json")) or any(worker_root.glob("*/*/complete.json")):
            status = "lock_changed"
        else:
            status = "missing"
        return {
            "worker_id": worker_id,
            "status": status,
            "lock_sha256": lock_digest,
            "source_sha256": source_digest,
            "target": str(target),
        }

    def _cache_inventory(self, worker_id: str) -> list[tuple[Path, str]]:
        if not worker_id.replace("_", "").isalnum():
            raise ValueError("unsafe worker ID")
        root = self.cache_root / worker_id
        if not root.exists():
            return []
        if root.is_symlink() or root.absolute() != root.resolve(strict=True):
            raise ValueError("worker cache must be canonical")
        result: list[tuple[Path, str]] = []
        for lock_root in root.iterdir():
            if not _CACHE_DIGEST.fullmatch(lock_root.name) or not lock_root.is_dir():
                continue
            if lock_root.is_symlink():
                raise ValueError("worker cache lock root may not be a symlink")
            if (lock_root / "complete.json").is_file():
                result.append((lock_root, "version"))
            for target in lock_root.iterdir():
                if target.is_symlink():
                    raise ValueError("worker cache entries may not be symlinks")
                if not target.is_dir():
                    continue
                if _CACHE_DIGEST.fullmatch(target.name):
                    kind = "version" if (target / "complete.json").is_file() else "damaged"
                    result.append((target, kind))
                elif target.name.startswith(".damaged-"):
                    result.append((target, "damaged"))
                elif re.fullmatch(r"\.[0-9a-f]{12}-[A-Za-z0-9_]+", target.name):
                    result.append((target, "staging"))
        return result

    @staticmethod
    def _lease_root(target: Path) -> Path:
        backup = target / "environment"
        if target.name.startswith(".damaged-") and backup.is_symlink():
            raise ValueError("worker cache backup may not be symlinked")
        return backup if target.name.startswith(".damaged-") and backup.is_dir() else target

    def cache_status(self, worker_id: str) -> dict[str, object]:
        """Inspect bounded metadata, without recursively walking large venvs."""
        inventory = self._cache_inventory(worker_id)
        in_use = 0
        known_bytes = 0
        unmeasured = 0
        for target, _kind in inventory:
            size: int | None = None
            lease_root = self._lease_root(target)
            marker = lease_root / "complete.json"
            try:
                if marker.stat().st_size <= 64 * 1024 and not marker.is_symlink():
                    value = json.loads(marker.read_text())
                    raw = value.get("payload_logical_bytes") if isinstance(value, dict) else None
                    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                        size = raw
            except (OSError, ValueError):
                pass
            try:
                with _exclusive_environment(lease_root, create=False) as available:
                    active = not available
            except OSError:
                active = True  # uncertain control paths cannot authorize deletion
            if size is None:
                unmeasured += 1
            else:
                known_bytes += size
            in_use += active
        return {
            "versions": sum(kind == "version" for _, kind in inventory),
            "damaged": sum(kind == "damaged" for _, kind in inventory),
            "staging": sum(kind == "staging" for _, kind in inventory),
            "size_bytes": known_bytes,
            "size_complete": unmeasured == 0,
            "in_use": in_use,
            "unmeasured_environments": unmeasured,
            "size_note": "logical payload bytes; shared package storage may be deduplicated",
        }

    def cleanup(
        self, worker_id: str, *, keep_versions: int = 2, keep_damaged: int = 1
    ) -> dict[str, object]:
        """Explicitly retire obsolete environments while preserving live and current ones."""
        if (
            isinstance(keep_versions, bool)
            or not isinstance(keep_versions, int)
            or keep_versions < 1
            or isinstance(keep_damaged, bool)
            or not isinstance(keep_damaged, int)
            or keep_damaged < 0
        ):
            raise ValueError("cache retention must preserve at least one complete version")
        if not worker_id.replace("_", "").isalnum():
            raise ValueError("unsafe worker ID")
        fd = os.open(
            self.cache_root / f".{worker_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            removed, protected = self._cleanup_locked(
                worker_id, keep_versions=keep_versions, keep_damaged=keep_damaged
            )
        return {
            **self.cache_status(worker_id),
            "removed_count": len(removed),
            "skipped_in_use": len(protected),
        }

    def _cleanup_locked(
        self, worker_id: str, *, keep_versions: int | None, keep_damaged: int
    ) -> tuple[list[str], list[str]]:
        inventory = self._cache_inventory(worker_id)
        current = Path(self.inspect(worker_id)["target"])
        versions = sorted(
            (p for p, kind in inventory if kind == "version"),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )
        # A legacy lock-only environment can still be the selected current version.
        if not current.exists() and (current.parent / "complete.json").is_file():
            current = current.parent
        keep = set(versions) if keep_versions is None else {current}
        if keep_versions is not None:
            # Keep current plus the newest rollback versions, not every old revision.
            keep = {current, *[p for p in versions if p != current][: keep_versions - 1]}
        damaged = sorted(
            (p for p, kind in inventory if kind == "damaged"),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )
        keep.update(damaged[:keep_damaged])
        removed: list[str] = []
        protected: list[str] = []
        for target, _kind in inventory:
            if target in keep:
                continue
            try:
                with _exclusive_environment(self._lease_root(target)) as available:
                    if not available:
                        protected.append(str(target))
                        continue
                    if target.parent == self.cache_root / worker_id:
                        # A legacy lock-only payload can share its parent with
                        # newer source-addressed environments. Retire only its
                        # own payload, never their containing lock directory.
                        for name in ("workers", "protocol", "complete.json"):
                            path = target / name
                            if path.is_dir() and not path.is_symlink():
                                shutil.rmtree(path)
                            else:
                                path.unlink(missing_ok=True)
                    else:
                        shutil.rmtree(target)
                    removed.append(str(target))
            except OSError:
                protected.append(str(target))
        return removed, protected

    def _ensure_root(self) -> None:
        if self.cache_root.is_symlink():
            raise ValueError("worker environment root may not be a symlink")
        self.cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cache_root.chmod(0o700)
        validate_restricted_directory(self.cache_root)

    @staticmethod
    def _is_complete(target: Path, worker_id: str, lock_digest: str, source_digest: str) -> bool:
        try:
            value = json.loads((target / "complete.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        project = target / "workers" / worker_id
        protocol = target / "protocol" / "python"
        python = project / ".venv" / "bin" / "python"
        if not isinstance(value, dict):
            return False
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
        if not isinstance(value, dict):
            return False
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
