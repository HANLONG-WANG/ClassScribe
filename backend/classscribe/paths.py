"""XDG path resolution and restricted local storage helpers."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class PathSecurityError(ValueError):
    """Raised when an XDG or data path violates local-storage constraints."""


def _absolute_path(value: str | Path, *, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise PathSecurityError(f"{name} must be an absolute path")
    return path.resolve(strict=False)


def _xdg_base(environment: Mapping[str, str], key: str, fallback: Path, *, home: Path) -> Path:
    raw = environment.get(key)
    if raw is None or raw == "":
        return fallback
    if raw.startswith("~/"):
        return _absolute_path(home / raw[2:], name=key)
    return _absolute_path(raw, name=key)


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def validate_restricted_directory(path: Path) -> None:
    """Require an owned, non-symlink directory inaccessible to group and other users."""

    if path.is_symlink():
        raise PathSecurityError(f"restricted directory may not be a symlink: {path}")
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise PathSecurityError(f"restricted path is not a directory: {path}")
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise PathSecurityError(f"restricted directory is not owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PathSecurityError(f"restricted directory permissions are too broad: {path}")


@dataclass(frozen=True, slots=True)
class AppPaths:
    """All ClassScribe roots, derived from the XDG base-directory contract."""

    config: Path
    data: Path
    cache: Path
    state: Path
    runtime: Path

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        home: Path | None = None,
    ) -> AppPaths:
        env = os.environ if environment is None else environment
        home_path = _absolute_path(Path.home() if home is None else home, name="home")
        config_base = _xdg_base(env, "XDG_CONFIG_HOME", home_path / ".config", home=home_path)
        data_base = _xdg_base(env, "XDG_DATA_HOME", home_path / ".local" / "share", home=home_path)
        cache_base = _xdg_base(env, "XDG_CACHE_HOME", home_path / ".cache", home=home_path)
        state_base = _xdg_base(
            env, "XDG_STATE_HOME", home_path / ".local" / "state", home=home_path
        )
        runtime_raw = env.get("XDG_RUNTIME_DIR")
        runtime_base = (
            state_base / "classscribe" / "run"
            if not runtime_raw
            else _absolute_path(runtime_raw, name="XDG_RUNTIME_DIR")
        )
        return cls(
            config=config_base / "classscribe",
            data=data_base / "classscribe",
            cache=cache_base / "classscribe",
            state=state_base / "classscribe",
            runtime=runtime_base / "classscribe" if runtime_raw else runtime_base,
        )

    def required_directories(self) -> tuple[Path, ...]:
        return (
            self.config,
            self.data,
            self.data / "jobs",
            self.data / "glossaries",
            self.data / "benchmarks",
            self.cache,
            self.cache / "models",
            self.cache / "resampled",
            self.cache / "waveform-peaks",
            self.cache / "tmp",
            self.state,
            self.state / "logs",
            self.state / "crash-reports",
            self.runtime,
        )

    def ensure(self) -> None:
        """Create application-owned directories with mode 0700 and validate them."""

        for path in self.required_directories():
            if path.is_symlink():
                raise PathSecurityError(f"application directory may not be a symlink: {path}")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
            validate_restricted_directory(path)

    def data_path(self, *parts: str) -> Path:
        """Resolve a relative path without permitting traversal or symlink escape."""

        if not parts or any(not part for part in parts):
            raise PathSecurityError("a non-empty data-relative path is required")
        relative = Path(*parts)
        if relative.is_absolute() or ".." in relative.parts:
            raise PathSecurityError("data path must be relative and may not traverse parents")
        root = self.data.resolve(strict=False)
        candidate = (root / relative).resolve(strict=False)
        if not _is_within(candidate, root):
            raise PathSecurityError("data path escapes the ClassScribe data root")
        return candidate

    def job_paths(self, job_id: str) -> tuple[Path, ...]:
        if not job_id or job_id in {".", ".."} or "/" in job_id or "\\" in job_id:
            raise PathSecurityError("job_id must be a single non-empty path component")
        root = self.data_path("jobs", job_id)
        return (
            root,
            root / "source",
            root / "derived",
            root / "candidates",
            root / "exports",
        )
