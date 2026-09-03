"""Local API credentials, upload limits, UUID storage, and worker path allowlisting."""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import AppPaths, PathSecurityError, validate_restricted_directory
from classscribe.recovery import atomic_write_text
from classscribe.timeline import SAMPLE_RATE, validate_sample_index

TOKEN_BYTES: Final = 32
TOKEN_MODE: Final = 0o600
MAX_CLASSROOM_SAMPLES: Final = 90 * 60 * SAMPLE_RATE
CREDENTIAL_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class TokenStore:
    """Create and verify a 256-bit local bearer token stored with mode 0600."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self) -> str:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        validate_restricted_directory(self.path.parent)
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                TOKEN_MODE,
            )
        except FileExistsError:
            return self.load()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(token)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        return token

    def load(self) -> str:
        if self.path.is_symlink():
            raise PathSecurityError("API token file may not be a symlink")
        metadata = self.path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise PathSecurityError("API token path must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != TOKEN_MODE:
            raise PathSecurityError("API token file permissions must be 0600")
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            raise PathSecurityError("API token file is not owned by the current user")
        token = self.path.read_text(encoding="utf-8").strip()
        if len(token) < 43:
            raise PathSecurityError("API token is shorter than a 256-bit urlsafe token")
        return token

    @staticmethod
    def verify(expected: str, supplied: str) -> bool:
        return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


class RestrictedCredentialEnvironment:
    """A mode-0600 environment file kept separate from ordinary YAML configuration."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, credentials: dict[str, str]) -> None:
        lines: list[str] = []
        for name, value in sorted(credentials.items()):
            if not CREDENTIAL_NAME.fullmatch(name):
                raise PathSecurityError(f"invalid credential variable name: {name}")
            if not value or "\n" in value or "\r" in value or "\0" in value:
                raise PathSecurityError(f"invalid credential value for {name}")
            lines.append(f"{name}={value}")
        atomic_write_text(self.path, "\n".join(lines) + "\n", mode=TOKEN_MODE)

    def load(self) -> dict[str, str]:
        if self.path.is_symlink():
            raise PathSecurityError("credential environment file may not be a symlink")
        metadata = self.path.stat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != TOKEN_MODE:
            raise PathSecurityError("credential environment file must be regular and mode 0600")
        credentials: dict[str, str] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if not separator or not CREDENTIAL_NAME.fullmatch(name) or not value:
                raise PathSecurityError("malformed credential environment file")
            credentials[name] = value
        return credentials


def parse_uuid(value: str, *, field: str = "file_id") -> UUID:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ClassScribeError(ErrorCode.INVALID_FILE_ID, f"invalid {field}") from exc
    if str(parsed) != value.lower():
        raise ClassScribeError(ErrorCode.INVALID_FILE_ID, f"{field} must be canonical UUID text")
    return parsed


@dataclass(frozen=True, slots=True)
class UploadLimits:
    max_bytes: int = 8 * 1024 * 1024 * 1024
    max_duration_samples: int = MAX_CLASSROOM_SAMPLES
    max_channels: int = 8
    max_sample_rate: int = 192_000
    decode_threads: int = 2
    allowed_suffixes: tuple[str, ...] = (
        ".wav",
        ".flac",
        ".mp3",
        ".m4a",
        ".ogg",
        ".opus",
        ".mp4",
        ".mkv",
        ".webm",
    )

    def validate(
        self,
        *,
        source_name: str,
        size_bytes: int,
        duration_samples: int,
        channels: int,
        sample_rate: int,
    ) -> None:
        if isinstance(size_bytes, bool) or not 0 <= size_bytes <= self.max_bytes:
            raise ClassScribeError(ErrorCode.UPLOAD_TOO_LARGE, "upload exceeds byte limit")
        validate_sample_index(duration_samples, field="duration_samples")
        if duration_samples > self.max_duration_samples:
            raise ClassScribeError(ErrorCode.AUDIO_TOO_LONG, "media exceeds 90-minute limit")
        if Path(source_name).suffix.lower() not in self.allowed_suffixes:
            raise ClassScribeError(ErrorCode.UNSUPPORTED_MEDIA, "unsupported media suffix")
        if not 1 <= channels <= self.max_channels:
            raise ClassScribeError(ErrorCode.DECODE_LIMIT_EXCEEDED, "channel limit exceeded")
        if not 1 <= sample_rate <= self.max_sample_rate:
            raise ClassScribeError(ErrorCode.DECODE_LIMIT_EXCEEDED, "sample-rate limit exceeded")

    def ffmpeg_guard_args(self) -> tuple[str, ...]:
        duration_seconds = self.max_duration_samples // SAMPLE_RATE
        return (
            "-nostdin",
            "-threads",
            str(self.decode_threads),
            "-t",
            str(duration_seconds),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-sample_fmt",
            "s16",
        )


class SecureFileLocator:
    """Allocate UUID paths and approve only normalized local worker inputs."""

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.allowed_roots = tuple(
            root.resolve(strict=False)
            for root in (paths.data / "jobs", paths.cache / "resampled", paths.cache / "tmp")
        )

    def allocate_job(self) -> tuple[str, tuple[Path, ...]]:
        job_id = str(uuid4())
        job_paths = self.paths.job_paths(job_id)
        for path in job_paths:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
            path.chmod(0o700)
        return job_id, job_paths

    def source_upload_path(self, job_id: str) -> Path:
        parse_uuid(job_id, field="job_id")
        return self.paths.data_path("jobs", job_id, "source", f"{uuid4()}.upload")

    def approve_worker_path(self, path: Path) -> Path:
        absolute = path.absolute()
        try:
            canonical = path.resolve(strict=True)
        except OSError as exc:
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "worker path does not exist"
            ) from exc
        if absolute != canonical or not canonical.is_file():
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT,
                "worker path must be a canonical regular file without symlinks",
            )
        if not any(canonical.is_relative_to(root) for root in self.allowed_roots):
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT,
                "worker path lies outside approved ClassScribe data roots",
            )
        return canonical
