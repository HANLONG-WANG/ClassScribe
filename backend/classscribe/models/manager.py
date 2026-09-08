"""Transactional, user-initiated model installation and offline resolution."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import wave
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.db.models import ModelHealth, ModelInstallation
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import validate_restricted_directory

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
LICENSE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,127}$")
AUDIT_FILENAME = "supply-chain.json"
ACTIVE_FILENAME = "active.json"
MANIFEST_KINDS = frozenset({"model", "tokenizer", "config", "remote_code", "other"})
COMPONENT_SOURCE_RELATIONSHIPS = frozenset({"copied", "derived"})
MAX_MANIFEST_PATH_LENGTH = 1024


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str
    sha256: str
    size_bytes: int
    kind: Literal["model", "tokenizer", "config", "remote_code", "other"] = "model"

    def __post_init__(self) -> None:
        relative = PurePosixPath(self.path)
        if (
            not self.path
            or len(self.path) > MAX_MANIFEST_PATH_LENGTH
            or relative.is_absolute()
            or ".." in relative.parts
            or self.path != relative.as_posix()
            or "\\" in self.path
            or any(ord(character) < 32 or ord(character) == 127 for character in self.path)
            or self.path == AUDIT_FILENAME
        ):
            raise ValueError(f"unsafe manifest path: {self.path!r}")
        if not SHA256_RE.fullmatch(self.sha256):
            raise ValueError(f"invalid sha256 for {self.path}")
        if self.size_bytes < 0:
            raise ValueError(f"negative size for {self.path}")
        if self.kind not in MANIFEST_KINDS:
            raise ValueError(f"invalid manifest file kind for {self.path}")


@dataclass(frozen=True, slots=True)
class ManifestComponentSourceFile:
    """One installed file traced to a fixed file in a component source repository."""

    installed_path: str
    source_path: str
    source_sha256: str
    source_size_bytes: int

    def __post_init__(self) -> None:
        for label, path in (
            ("component installed path", self.installed_path),
            ("component source path", self.source_path),
        ):
            relative = PurePosixPath(path)
            if (
                not path
                or len(path) > MAX_MANIFEST_PATH_LENGTH
                or relative.is_absolute()
                or ".." in relative.parts
                or path != relative.as_posix()
                or "\\" in path
                or any(ord(character) < 32 or ord(character) == 127 for character in path)
                or path == AUDIT_FILENAME
            ):
                raise ValueError(f"unsafe {label}: {path!r}")
        if not SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("component source file SHA-256 is invalid")
        if self.source_size_bytes < 0:
            raise ValueError("component source file size is negative")


@dataclass(frozen=True, slots=True)
class ManifestComponentSource:
    """Auditable origin for files copied or derived from another fixed repository."""

    repository: str
    revision: str
    relationship: Literal["copied", "derived"]
    license_id: str
    license_url: str
    requires_terms_acceptance: bool
    files: tuple[ManifestComponentSourceFile, ...]

    def __post_init__(self) -> None:
        if self.repository.count("/") != 1 or any(
            character.isspace() for character in self.repository
        ):
            raise ValueError("component source repository must use owner/name")
        if not COMMIT_RE.fullmatch(self.revision):
            raise ValueError("component source revision must be a full commit SHA")
        if self.relationship not in COMPONENT_SOURCE_RELATIONSHIPS:
            raise ValueError("component source relationship is invalid")
        if not LICENSE_ID_RE.fullmatch(self.license_id):
            raise ValueError("component source license ID is invalid")
        license_url = urlsplit(self.license_url)
        if (
            license_url.scheme != "https"
            or not license_url.hostname
            or license_url.username is not None
            or license_url.password is not None
        ):
            raise ValueError("component source license URL must be credential-free HTTPS")
        if not self.files:
            raise ValueError("component source must identify at least one installed file")
        installed_paths = [item.installed_path for item in self.files]
        if installed_paths != sorted(installed_paths):
            raise ValueError("component source files must be sorted by installed path")
        if len(installed_paths) != len(set(installed_paths)):
            raise ValueError("component source installed paths must be unique")
        source_paths = [item.source_path for item in self.files]
        if len(source_paths) != len(set(source_paths)):
            raise ValueError("component source paths must be unique")


@dataclass(frozen=True, slots=True)
class ModelManifest:
    manifest_version: int
    model_id: str
    repository: str
    revision: str
    worker: str
    license_id: str
    license_url: str
    requires_terms_acceptance: bool
    trust_remote_code: bool
    estimated_download_bytes: int
    installed_size_bytes: int
    environment: Mapping[str, str]
    files: tuple[ManifestFile, ...]
    component_sources: tuple[ManifestComponentSource, ...] = ()

    def __post_init__(self) -> None:
        if self.manifest_version != 1:
            raise ValueError("unsupported model manifest version")
        if not IDENTIFIER_RE.fullmatch(self.model_id) or not IDENTIFIER_RE.fullmatch(self.worker):
            raise ValueError("model_id and worker must be safe identifiers")
        if not self.repository or any(char.isspace() for char in self.repository):
            raise ValueError("repository must be a non-empty locator without whitespace")
        if not LICENSE_ID_RE.fullmatch(self.license_id):
            raise ValueError("license_id must be a bounded SPDX or LicenseRef identifier")
        license_url = urlsplit(self.license_url)
        if (
            license_url.scheme != "https"
            or not license_url.hostname
            or license_url.username is not None
            or license_url.password is not None
        ):
            raise ValueError("license_url must be an HTTPS URL without embedded credentials")
        if not COMMIT_RE.fullmatch(self.revision):
            raise ClassScribeError(
                ErrorCode.MODEL_REVISION_NOT_PINNED,
                "model revision must be a full lowercase 40-character commit SHA",
            )
        if self.estimated_download_bytes < 0 or self.installed_size_bytes < 0:
            raise ValueError("model byte estimates must be non-negative")
        if not self.files:
            raise ValueError("model manifest must list every installed file")
        paths = [item.path for item in self.files]
        if paths != sorted(paths):
            raise ValueError("model manifest files must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("model manifest paths must be unique")
        actual_size = sum(item.size_bytes for item in self.files)
        if actual_size != self.installed_size_bytes:
            raise ValueError("installed_size_bytes must equal the manifest file sizes")
        remote_code = [item for item in self.files if item.kind == "remote_code"]
        if self.trust_remote_code and not remote_code:
            raise ValueError("trust_remote_code requires at least one hashed code file")
        if remote_code and not self.trust_remote_code:
            raise ValueError("remote-code files require trust_remote_code=true")
        source_repositories = [source.repository for source in self.component_sources]
        if source_repositories != sorted(source_repositories):
            raise ValueError("component sources must be sorted by repository")
        if len(source_repositories) != len(set(source_repositories)):
            raise ValueError("component source repositories must be unique")
        if self.repository in source_repositories:
            raise ValueError("component source must differ from the payload repository")
        manifest_files = {item.path: item for item in self.files}
        installed_component_paths: set[str] = set()
        for source in self.component_sources:
            for item in source.files:
                if item.installed_path in installed_component_paths:
                    raise ValueError("installed file belongs to multiple component sources")
                installed_component_paths.add(item.installed_path)
                installed = manifest_files.get(item.installed_path)
                if installed is None:
                    raise ValueError("component source refers to a file outside the manifest")
                if source.relationship == "copied" and (
                    installed.sha256 != item.source_sha256
                    or installed.size_bytes != item.source_size_bytes
                ):
                    raise ValueError("copied component file differs from its fixed source")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelManifest:
        raw_files = value.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("manifest files must be an array")
        environment = value.get("environment")
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in environment.items()
        ):
            raise ValueError("manifest environment must be a string map")
        raw_component_sources = value.get("component_sources", [])
        if not isinstance(raw_component_sources, list):
            raise ValueError("manifest component_sources must be an array")
        component_sources: list[ManifestComponentSource] = []
        for raw_source in raw_component_sources:
            if not isinstance(raw_source, dict):
                raise ValueError("manifest component source must be an object")
            raw_source_files = raw_source.get("files")
            if not isinstance(raw_source_files, list) or not all(
                isinstance(item, dict) for item in raw_source_files
            ):
                raise ValueError("manifest component source files must be an array")
            source_value = dict(raw_source)
            source_value["files"] = tuple(
                ManifestComponentSourceFile(**item) for item in raw_source_files
            )
            component_sources.append(ManifestComponentSource(**source_value))
        return cls(
            manifest_version=int(value["manifest_version"]),
            model_id=str(value["model_id"]),
            repository=str(value["repository"]),
            revision=str(value["revision"]),
            worker=str(value["worker"]),
            license_id=str(value["license_id"]),
            license_url=str(value["license_url"]),
            requires_terms_acceptance=bool(value["requires_terms_acceptance"]),
            trust_remote_code=bool(value["trust_remote_code"]),
            estimated_download_bytes=int(value["estimated_download_bytes"]),
            installed_size_bytes=int(value["installed_size_bytes"]),
            environment=environment,
            files=tuple(ManifestFile(**item) for item in raw_files),
            component_sources=tuple(component_sources),
        )

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "manifest_version": self.manifest_version,
            "model_id": self.model_id,
            "repository": self.repository,
            "revision": self.revision,
            "worker": self.worker,
            "license_id": self.license_id,
            "license_url": self.license_url,
            "requires_terms_acceptance": self.requires_terms_acceptance,
            "trust_remote_code": self.trust_remote_code,
            "estimated_download_bytes": self.estimated_download_bytes,
            "installed_size_bytes": self.installed_size_bytes,
            "environment": dict(self.environment),
            "files": [asdict(item) for item in self.files],
        }
        if self.component_sources:
            value["component_sources"] = [
                {
                    "repository": source.repository,
                    "revision": source.revision,
                    "relationship": source.relationship,
                    "license_id": source.license_id,
                    "license_url": source.license_url,
                    "requires_terms_acceptance": source.requires_terms_acceptance,
                    "files": [asdict(item) for item in source.files],
                }
                for source in self.component_sources
            ]
        return value


@dataclass(frozen=True, slots=True)
class InstallationPlan:
    confirmation_token: str
    model_id: str
    revision: str
    license_id: str
    license_url: str
    requires_terms_acceptance: bool
    component_sources: tuple[ManifestComponentSource, ...]
    estimated_download_bytes: int
    installed_size_bytes: int
    required_free_bytes: int
    available_bytes: int
    environment: Mapping[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DownloadReceipt:
    resolved_revision: str
    source: str
    downloaded_bytes: int


@dataclass(frozen=True, slots=True)
class HealthCheckOutcome:
    healthy: bool
    measured_vram_mb: int | None
    detail: str
    environment: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class InstallationResult:
    model_id: str
    revision: str
    local_path: Path
    aggregate_sha256: str
    health: HealthCheckOutcome


@dataclass(frozen=True, slots=True)
class ManifestDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    remote_code_changed: tuple[str, ...]


class ModelDownloader(Protocol):
    def download(
        self,
        *,
        repository: str,
        revision: str,
        destination: Path,
        files: Sequence[ManifestFile],
    ) -> DownloadReceipt: ...


HealthCheck = Callable[[Path, Path, Mapping[str, str]], HealthCheckOutcome]


class InstallationRecorder(Protocol):
    def record(
        self,
        manifest: ModelManifest,
        local_path: Path,
        aggregate_sha256: str,
        health: HealthCheckOutcome,
        installed_at: datetime,
    ) -> None: ...

    def remove(self, model_id: str, revision: str) -> None: ...


class SQLAlchemyInstallationRecorder:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record(
        self,
        manifest: ModelManifest,
        local_path: Path,
        aggregate_sha256: str,
        health: HealthCheckOutcome,
        installed_at: datetime,
    ) -> None:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(ModelInstallation).where(
                    ModelInstallation.model_id == manifest.model_id,
                    ModelInstallation.revision == manifest.revision,
                )
            )
            item = existing or ModelInstallation(
                model_id=manifest.model_id,
                revision=manifest.revision,
            )
            item.repository = manifest.repository
            item.local_path = str(local_path)
            item.sha256 = aggregate_sha256
            item.environment_json = {
                "declared": dict(manifest.environment),
                "observed": dict(health.environment),
            }
            item.measured_vram_mb = health.measured_vram_mb
            item.health_status = ModelHealth.HEALTHY
            item.installed_at = installed_at
            item.checked_at = installed_at
            if existing is None:
                session.add(item)

    def remove(self, model_id: str, revision: str) -> None:
        with self._sessions.begin() as session:
            session.execute(
                delete(ModelInstallation).where(
                    ModelInstallation.model_id == model_id,
                    ModelInstallation.revision == revision,
                )
            )


class ModelManager:
    """Own model artifacts; normal runtime code can only resolve complete local revisions."""

    def __init__(
        self,
        root: Path,
        *,
        recorder: InstallationRecorder | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root
        self._recorder = recorder
        self._now = now or (lambda: datetime.now(UTC))
        self._plans: dict[str, tuple[ModelManifest, datetime]] = {}
        self._install_progress: dict[tuple[str, str], str] = {}
        self._ensure_directory(self.root)
        self._ensure_directory(self.root / ".staging")

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if path.is_symlink():
            raise ValueError(f"model directory may not be a symlink: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        validate_restricted_directory(path)

    def installation_stage(self, model_id: str, revision: str) -> str:
        self._validate_identifier(model_id)
        self._validate_revision(revision)
        stage = self._install_progress.get((model_id, revision))
        if stage is not None:
            return stage
        target = self.root / model_id / "revisions" / revision
        if target.is_symlink() or (target / AUDIT_FILENAME).is_symlink():
            return "not_installed"
        try:
            audit = json.loads((target / AUDIT_FILENAME).read_text(encoding="utf-8"))
            if audit.get("health_check", {}).get("healthy") is True:
                return "complete"
            if audit.get("aggregate_sha256"):
                return "awaiting_health_check"
        except (OSError, ValueError, AttributeError):
            pass
        return "not_installed"

    def _reusable_download(self, manifest: ModelManifest) -> dict[str, Any] | None:
        target = self.root / manifest.model_id / "revisions" / manifest.revision
        if target.is_symlink() or (target / AUDIT_FILENAME).is_symlink():
            return None
        try:
            audit = json.loads((target / AUDIT_FILENAME).read_text(encoding="utf-8"))
            if (
                audit["manifest"] == manifest.as_dict()
                and audit.get("health_check", {}).get("healthy") is not True
            ):
                return dict(audit)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return None

    def request_user_install(self, manifest: ModelManifest) -> InstallationPlan:
        """Create a short-lived, single-use confirmation after showing size/environment."""

        usage = shutil.disk_usage(self.root)
        reusable = self._reusable_download(manifest) is not None
        required = (
            0 if reusable else manifest.estimated_download_bytes + manifest.installed_size_bytes
        )
        required += max(required // 10, 16 * 1024 * 1024)
        if usage.free < required:
            raise ClassScribeError(
                ErrorCode.MODEL_DISK_SPACE_INSUFFICIENT,
                f"installation requires {required} bytes; {usage.free} bytes available",
            )
        token = secrets.token_urlsafe(32)
        expires = self._now() + timedelta(minutes=10)
        self._plans[token] = (manifest, expires)
        return InstallationPlan(
            confirmation_token=token,
            model_id=manifest.model_id,
            revision=manifest.revision,
            license_id=manifest.license_id,
            license_url=manifest.license_url,
            requires_terms_acceptance=manifest.requires_terms_acceptance,
            component_sources=manifest.component_sources,
            estimated_download_bytes=0 if reusable else manifest.estimated_download_bytes,
            installed_size_bytes=manifest.installed_size_bytes,
            required_free_bytes=required,
            available_bytes=usage.free,
            environment=dict(manifest.environment),
            expires_at=expires,
        )

    def install_confirmed(
        self,
        confirmation_token: str,
        *,
        downloader: ModelDownloader,
        health_audio: Path,
        health_check: HealthCheck,
        terms_accepted: bool = False,
        expected_model_id: str | None = None,
    ) -> InstallationResult:
        planned = self._plans.pop(confirmation_token, None)
        if planned is None or planned[1] < self._now():
            raise ClassScribeError(
                ErrorCode.MODEL_INSTALL_NOT_USER_INITIATED,
                "a current, single-use user confirmation is required",
            )
        manifest = planned[0]
        if expected_model_id is not None and manifest.model_id != expected_model_id:
            raise ClassScribeError(
                ErrorCode.MODEL_INSTALL_NOT_USER_INITIATED,
                "the confirmation token belongs to a different model",
            )
        if manifest.requires_terms_acceptance and not terms_accepted:
            raise ClassScribeError(
                ErrorCode.MODEL_INSTALL_NOT_USER_INITIATED,
                "the upstream access terms must be explicitly accepted for this installation",
            )
        with self._model_mutation(manifest.model_id):
            health_audio_metadata = self._validate_health_audio(health_audio)
            model_root = self.root / manifest.model_id
            revisions_root = model_root / "revisions"
            self._ensure_directory(model_root)
            self._ensure_directory(revisions_root)
            target = revisions_root / manifest.revision
            cached_audit = self._reusable_download(manifest)
            if (target.exists() or target.is_symlink()) and cached_audit is None:
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED, "revision is already installed"
                )
            staging = Path(
                tempfile.mkdtemp(prefix=f"{manifest.model_id}-", dir=self.root / ".staging")
            )
            staging.chmod(0o700)
            payload = staging / "payload"
            payload.mkdir(mode=0o700)
            previous_active = self._active_revision(manifest.model_id)
            installed_at = self._now()
            owns_target = False
            verified = False
            progress_key = (manifest.model_id, manifest.revision)
            try:
                if cached_audit is None:
                    self._install_progress[progress_key] = "downloading"
                    receipt = downloader.download(
                        repository=manifest.repository,
                        revision=manifest.revision,
                        destination=payload,
                        files=manifest.files,
                    )
                    if receipt.resolved_revision != manifest.revision:
                        raise ClassScribeError(
                            ErrorCode.MODEL_REVISION_NOT_PINNED,
                            "download source resolved a revision different from the pinned commit",
                        )
                    self._install_progress[progress_key] = "verifying"
                    aggregate = self._verify_payload(payload, manifest)
                    audit = {
                        "format_version": 1,
                        "installed_at": installed_at.isoformat(),
                        "source": receipt.source,
                        "downloaded_bytes": receipt.downloaded_bytes,
                        "aggregate_sha256": aggregate,
                        "manifest": manifest.as_dict(),
                        "health_audio": health_audio_metadata,
                    }
                    self._atomic_json(payload / AUDIT_FILENAME, audit)
                    os.replace(payload, target)
                    owns_target = True
                else:
                    self._install_progress[progress_key] = "verifying"
                    aggregate = self._verify_payload(target, manifest, allow_audit=True)
                    if aggregate != cached_audit["aggregate_sha256"]:
                        raise ClassScribeError(
                            ErrorCode.MODEL_INTEGRITY_FAILED, "cached checksum mismatch"
                        )
                    audit = cached_audit
                    audit["health_audio"] = health_audio_metadata
                verified = True
                self._install_progress[progress_key] = "checking"
                health = health_check(target, health_audio, runtime_environment())
                if not health.healthy:
                    raise ClassScribeError(ErrorCode.MODEL_HEALTH_CHECK_FAILED, health.detail)
                audit["health_check"] = {
                    "healthy": health.healthy,
                    "detail": health.detail,
                    "measured_vram_mb": health.measured_vram_mb,
                    "environment": dict(health.environment),
                }
                self._atomic_json(target / AUDIT_FILENAME, audit)
                if self._recorder is not None:
                    self._recorder.record(manifest, target, aggregate, health, installed_at)
                self._activate(manifest.model_id, manifest.revision)
                self._install_progress[progress_key] = "complete"
                return InstallationResult(
                    model_id=manifest.model_id,
                    revision=manifest.revision,
                    local_path=target,
                    aggregate_sha256=aggregate,
                    health=health,
                )
            except Exception:
                self._install_progress[progress_key] = (
                    "awaiting_health_check" if verified else "failed"
                )
                if verified:
                    audit["health_check"] = {"healthy": False}
                    self._atomic_json(target / AUDIT_FILENAME, audit)
                elif owns_target and target.exists() and not target.is_symlink():
                    shutil.rmtree(target)
                current_active = self._active_revision(manifest.model_id)
                if current_active != previous_active:
                    if previous_active is None:
                        (model_root / ACTIVE_FILENAME).unlink(missing_ok=True)
                    else:
                        self._activate(manifest.model_id, previous_active)
                raise
            finally:
                if staging.exists() and not staging.is_symlink():
                    shutil.rmtree(staging)

    @contextmanager
    def _model_mutation(self, model_id: str) -> Iterator[None]:
        self._validate_identifier(model_id)
        root = self.root / model_id
        self._ensure_directory(root)
        descriptor = os.open(root / ".mutation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def resolve_for_runtime(self, model_id: str) -> Path:
        """Resolve and revalidate the active revision without any downloader/network path."""

        self._validate_identifier(model_id)
        revision = self._active_revision(model_id)
        if revision is None:
            raise ClassScribeError(
                ErrorCode.MODEL_NOT_FULLY_INSTALLED, f"model {model_id} has no active revision"
            )
        return self._validate_installed_revision(model_id, revision)

    def installed_revision_metadata(self, model_id: str) -> Path:
        """Inspect installation records for planning only; never authorize model execution."""
        self._validate_identifier(model_id)
        revision = self._active_revision(model_id)
        if revision is None:
            raise ClassScribeError(
                ErrorCode.MODEL_NOT_FULLY_INSTALLED, f"model {model_id} has no active revision"
            )
        return self._validate_installed_revision(model_id, revision, verify_files=False)

    def revision_fingerprint(
        self, model_id: str
    ) -> tuple[tuple[str, int, int, int, int, int], ...]:
        """Stat-only invalidation token for already verified, loaded worker reuse."""
        target = self.installed_revision_metadata(model_id)
        result = []
        for path in (target, *sorted(target.rglob("*"))):
            if path.is_symlink():
                raise ClassScribeError(ErrorCode.MODEL_INTEGRITY_FAILED, "model contains a symlink")
            metadata = path.stat()
            result.append(
                (
                    str(path),
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )
            )
        return tuple(result)

    def _validate_installed_revision(
        self, model_id: str, revision: str, *, verify_files: bool = True
    ) -> Path:
        target = self.root / model_id / "revisions" / revision
        if target.is_symlink() or (target / AUDIT_FILENAME).is_symlink():
            raise ClassScribeError(
                ErrorCode.MODEL_NOT_FULLY_INSTALLED,
                f"model {model_id} revision {revision} contains a symlinked control path",
            )
        try:
            audit = json.loads((target / AUDIT_FILENAME).read_text(encoding="utf-8"))
            manifest = ModelManifest.from_dict(audit["manifest"])
            if manifest.model_id != model_id or manifest.revision != revision:
                raise ValueError("installed manifest identity differs from requested revision")
            if (
                not isinstance(audit.get("health_check"), dict)
                or audit["health_check"].get("healthy") is not True
            ):
                raise ValueError("revision has no successful health check")
            if verify_files:
                aggregate = self._verify_payload(target, manifest, allow_audit=True)
                if aggregate != audit["aggregate_sha256"]:
                    raise ValueError("aggregate checksum mismatch")
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            ClassScribeError,
        ) as error:
            raise ClassScribeError(
                ErrorCode.MODEL_NOT_FULLY_INSTALLED,
                f"model {model_id} revision {revision} is incomplete: {error}",
            ) from error
        return target

    def rollback(self, model_id: str, revision: str) -> Path:
        self._validate_identifier(model_id)
        self._validate_revision(revision)
        with self._model_mutation(model_id):
            target = self._validate_installed_revision(model_id, revision)
            self._activate(model_id, revision)
            return target

    def delete_revision(self, model_id: str, revision: str) -> None:
        self._validate_identifier(model_id)
        self._validate_revision(revision)
        with self._model_mutation(model_id):
            if self._active_revision(model_id) == revision:
                raise ClassScribeError(
                    ErrorCode.MODEL_REVISION_ACTIVE,
                    "activate another revision before deleting this one",
                )
            target = self.root / model_id / "revisions" / revision
            if target.is_symlink():
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED, "revision may not be symlinked"
                )
            if target.exists():
                shutil.rmtree(target)
            self._install_progress.pop((model_id, revision), None)
            if self._recorder is not None:
                self._recorder.remove(model_id, revision)

    def diff_upgrade(self, model_id: str, candidate: ModelManifest) -> ManifestDiff:
        current = self.resolve_for_runtime(model_id)
        audit = json.loads((current / AUDIT_FILENAME).read_text(encoding="utf-8"))
        installed = ModelManifest.from_dict(audit["manifest"])
        old = {item.path: item for item in installed.files}
        new = {item.path: item for item in candidate.files}
        added = tuple(sorted(new.keys() - old.keys()))
        removed = tuple(sorted(old.keys() - new.keys()))
        changed = tuple(sorted(path for path in old.keys() & new.keys() if old[path] != new[path]))
        remote_paths = {
            item.path for item in (*installed.files, *candidate.files) if item.kind == "remote_code"
        }
        remote_changed = tuple(
            sorted(path for path in (*added, *removed, *changed) if path in remote_paths)
        )
        return ManifestDiff(added, removed, changed, remote_changed)

    def _active_revision(self, model_id: str) -> str | None:
        self._validate_identifier(model_id)
        active = self.root / model_id / ACTIVE_FILENAME
        if active.is_symlink():
            raise ClassScribeError(
                ErrorCode.MODEL_NOT_FULLY_INSTALLED, "active model pointer may not be a symlink"
            )
        try:
            value = json.loads(active.read_text(encoding="utf-8"))
            revision = str(value["revision"])
            self._validate_revision(revision)
        except FileNotFoundError:
            return None
        except (OSError, KeyError, ValueError, TypeError) as error:
            raise ClassScribeError(
                ErrorCode.MODEL_NOT_FULLY_INSTALLED, f"invalid active model pointer: {error}"
            ) from error
        return revision

    def _activate(self, model_id: str, revision: str) -> None:
        self._validate_identifier(model_id)
        self._validate_revision(revision)
        model_root = self.root / model_id
        self._ensure_directory(model_root)
        self._atomic_json(
            model_root / ACTIVE_FILENAME,
            {"format_version": 1, "revision": revision, "activated_at": self._now().isoformat()},
        )

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError("unsafe model identifier")

    @staticmethod
    def _validate_revision(value: str) -> None:
        if not COMMIT_RE.fullmatch(value):
            raise ValueError("unsafe model revision")

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _verify_payload(
        payload: Path, manifest: ModelManifest, *, allow_audit: bool = False
    ) -> str:
        if payload.is_symlink() or not payload.is_dir():
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED, "model payload must be a real directory"
            )
        expected = {item.path: item for item in manifest.files}
        actual: set[str] = set()
        for path in payload.rglob("*"):
            if path.is_symlink():
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED, f"symlinks are forbidden: {path}"
                )
            if path.is_file():
                actual.add(path.relative_to(payload).as_posix())
        allowed = set(expected)
        if allow_audit:
            allowed.add(AUDIT_FILENAME)
        if actual != allowed:
            missing = sorted(allowed - actual)
            extra = sorted(actual - allowed)
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED,
                f"manifest mismatch; missing={missing}, extra={extra}",
            )
        aggregate = hashlib.sha256()
        for relative in sorted(expected):
            spec = expected[relative]
            path = payload / relative
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
            if size != spec.size_bytes or digest.hexdigest() != spec.sha256:
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED, f"hash or size mismatch: {relative}"
                )
            aggregate.update(relative.encode("utf-8"))
            aggregate.update(b"\0")
            aggregate.update(spec.sha256.encode("ascii"))
            aggregate.update(b"\0")
        return aggregate.hexdigest()

    @staticmethod
    def _validate_health_audio(path: Path) -> dict[str, int | str]:
        try:
            with wave.open(str(path), "rb") as recording:
                frames = recording.getnframes()
                rate = recording.getframerate()
                channels = recording.getnchannels()
                width = recording.getsampwidth()
        except (OSError, wave.Error) as error:
            raise ValueError(f"health check requires a readable PCM WAV: {error}") from error
        if frames <= 0 or rate != 16_000 or channels != 1 or width != 2:
            raise ValueError("health WAV must be non-empty 16 kHz mono 16-bit PCM")
        if frames / rate > 15:
            raise ValueError("health WAV must be at most 15 seconds")
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "frames": frames,
            "sample_rate": rate,
            "channels": channels,
            "sample_width_bytes": width,
        }


def runtime_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Force supported model libraries offline after explicit installation."""

    environment = dict(base or {})
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    return environment
