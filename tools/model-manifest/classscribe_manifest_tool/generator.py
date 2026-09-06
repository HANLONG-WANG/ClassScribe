"""Deterministic manifest generation boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from urllib.parse import urlsplit

from huggingface_hub import snapshot_download

from classscribe_manifest_tool.credentials import (
    assert_hf_token_absent,
    authorization_headers,
    redact_for_diagnostics,
    require_gated_access_approval,
)
from classscribe_manifest_tool.discovery import (
    DiscoveredFile,
    RepositoryDiscovery,
    RepositoryTreeApi,
    discover_repository_tree,
    validate_selection_against_discovery,
)
from classscribe_manifest_tool.inputs import (
    ReleaseComponentSource,
    ReleaseInputs,
    ReleaseModel,
)
from classscribe_manifest_tool.selection import (
    SELECTION_KINDS,
    ModelFileSelection,
    SelectionKind,
    validate_exact_path,
)
from classscribe_manifest_tool.workspace import private_temporary_workspace

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[^\s/]+/[^\s/]+$")
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
LICENSE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ENVIRONMENT_KEYS = frozenset(
    {"python", "runtime_backend", "dtype", "worker_lock", "worker_lock_sha256"}
)
MAX_WEIGHT_INDEX_BYTES = 16 * 1024 * 1024


class SnapshotDownloadError(RuntimeError):
    """A sanitized selected-snapshot download failure."""


class GenerationMismatchError(RuntimeError):
    """A deterministic path/hash-only report that blocks publication."""

    def __init__(self, report: bytes) -> None:
        self.report = report
        super().__init__(report.decode("utf-8").rstrip())


@dataclass(frozen=True, slots=True)
class PayloadFile:
    """A validated regular file found below a payload root."""

    path: str
    size: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class HashedPayloadFile:
    """Actual bytes and digest computed from one securely opened payload file."""

    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ClassifiedPayloadFile:
    """A hashed payload file with its explicit human-reviewed kind."""

    path: str
    size_bytes: int
    sha256: str
    kind: SelectionKind


@dataclass(frozen=True, slots=True)
class ManifestMetadata:
    """Registry, license, and worker-lock facts used to build one manifest."""

    model_id: str
    repository: str
    revision: str
    worker: str
    license_id: str
    license_url: str
    requires_terms_acceptance: bool
    trust_remote_code: bool
    environment: Mapping[str, str]
    component_sources: tuple[ReleaseComponentSource, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelManifestDocument:
    """Validated immutable manifest v1 ready for canonical serialization."""

    metadata: ManifestMetadata
    installed_size_bytes: int
    files: tuple[ClassifiedPayloadFile, ...]

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "manifest_version": 1,
            "model_id": self.metadata.model_id,
            "repository": self.metadata.repository,
            "revision": self.metadata.revision,
            "worker": self.metadata.worker,
            "license_id": self.metadata.license_id,
            "license_url": self.metadata.license_url,
            "requires_terms_acceptance": self.metadata.requires_terms_acceptance,
            "trust_remote_code": self.metadata.trust_remote_code,
            "estimated_download_bytes": self.installed_size_bytes,
            "installed_size_bytes": self.installed_size_bytes,
            "environment": dict(self.metadata.environment),
            "files": [asdict(item) for item in self.files],
        }
        if self.metadata.component_sources:
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
                for source in self.metadata.component_sources
            ]
        return value


@contextmanager
def generation_workspace(*, token: str | None) -> Iterator[Path]:
    """Create a private system-temporary root independent of runtime model caches."""
    with private_temporary_workspace(token=token) as workspace:
        yield workspace


def download_selected_snapshot(
    repository: str,
    revision: str,
    selection: ModelFileSelection,
    workspace: Path,
    *,
    token: str | None,
    downloader: Callable[..., object] = snapshot_download,
) -> Path:
    """Download one exact allowlist at a full commit into its original layout."""
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("repository must have an owner/name identity")
    if not COMMIT_RE.fullmatch(revision):
        raise ValueError("snapshot revision must be a full lowercase commit")
    if not selection.include:
        raise ValueError("snapshot allowlist must not be empty")
    for path in selection.include:
        validate_exact_path(path, "snapshot allowlist")
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("generation workspace must be a regular directory")
    payload = workspace / "payload"
    cache = workspace / "hub-cache"
    if payload.exists() or payload.is_symlink() or cache.exists() or cache.is_symlink():
        raise ValueError("generation workspace must not contain prior download state")
    payload.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    try:
        result = downloader(
            repository,
            repo_type="model",
            revision=revision,
            cache_dir=cache,
            local_dir=payload,
            library_name="classscribe-model-manifest",
            library_version="1",
            force_download=True,
            token=False,
            local_files_only=False,
            allow_patterns=list(selection.include),
            ignore_patterns=None,
            headers=dict(authorization_headers(token)),
        )
    except Exception as error:
        diagnostic = redact_for_diagnostics(error, token=token)
        raise SnapshotDownloadError(f"selected snapshot download failed: {diagnostic}") from None
    if not isinstance(result, str) or Path(result).resolve() != payload.resolve():
        raise SnapshotDownloadError("snapshot downloader returned an unexpected payload root")
    return payload


def remove_huggingface_metadata(payload: Path) -> None:
    """Remove only downloader-owned local-dir metadata without following symlinks."""
    if payload.is_symlink() or not payload.is_dir():
        raise ValueError("payload root must be a regular directory")
    cache = payload / ".cache"
    if cache.is_symlink():
        raise ValueError("payload .cache must not be a symlink")
    if not cache.exists():
        return
    if not cache.is_dir():
        raise ValueError("payload .cache must be a directory")
    metadata = cache / "huggingface"
    if metadata.is_symlink():
        raise ValueError("Hugging Face metadata must not be a symlink")
    if metadata.exists():
        if not metadata.is_dir():
            raise ValueError("Hugging Face metadata must be a directory")
        shutil.rmtree(metadata)
    if not any(cache.iterdir()):
        cache.rmdir()


def scan_payload(
    payload: Path,
    *,
    expected_sizes: Mapping[str, int],
) -> tuple[PayloadFile, ...]:
    """Scan a payload without following links and enforce discovery-derived size limits."""
    if not payload.is_absolute() or payload != payload.resolve():
        raise ValueError("payload root must be an absolute canonical path")
    if payload.is_symlink() or not payload.is_dir():
        raise ValueError("payload root must be a regular directory")
    if not expected_sizes or any(
        isinstance(size, bool) or not isinstance(size, int) or size < 0
        for size in expected_sizes.values()
    ):
        raise ValueError("expected payload sizes must be non-empty confirmed byte counts")
    for expected_path in expected_sizes:
        validate_exact_path(expected_path, "expected payload path")
    maximum_total = sum(expected_sizes.values())
    total = 0
    files: list[PayloadFile] = []
    seen_paths: set[str] = set()
    seen_inodes: set[tuple[int, int]] = set()
    pending = [payload]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_path = Path(entry.path)
                relative = entry_path.relative_to(payload).as_posix()
                validate_exact_path(relative, "downloaded payload")
                metadata = entry.stat(follow_symlinks=False)
                entry_type = _classify_payload_entry(metadata, relative)
                if entry_type == "directory":
                    pending.append(entry_path)
                    continue
                inode = (metadata.st_dev, metadata.st_ino)
                if inode in seen_inodes:
                    raise ValueError(f"downloaded payload repeats an inode: {relative}")
                seen_inodes.add(inode)
                if relative in seen_paths:
                    raise ValueError(f"downloaded payload repeats a path: {relative}")
                seen_paths.add(relative)
                expected_size = expected_sizes.get(relative)
                if expected_size is not None and metadata.st_size > expected_size:
                    raise ValueError(f"downloaded payload file exceeds declared size: {relative}")
                total += metadata.st_size
                if total > maximum_total:
                    raise ValueError("downloaded payload exceeds the selected tree total size")
                files.append(
                    PayloadFile(
                        path=relative,
                        size=metadata.st_size,
                        device=metadata.st_dev,
                        inode=metadata.st_ino,
                    )
                )
    files.sort(key=lambda item: item.path)
    return tuple(files)


def _classify_payload_entry(metadata: os.stat_result, relative: str) -> str:
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"downloaded payload contains a symlink: {relative}")
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"downloaded payload contains a special file: {relative}")
    if metadata.st_nlink != 1:
        raise ValueError(f"downloaded payload contains a hardlink: {relative}")
    return "file"


def hash_payload_files(
    payload: Path,
    files: tuple[PayloadFile, ...],
    *,
    chunk_size: int = 1024 * 1024,
) -> tuple[HashedPayloadFile, ...]:
    """Stream SHA-256 and actual byte counts from securely reopened payload files."""
    if not payload.is_absolute() or payload != payload.resolve():
        raise ValueError("payload root must be an absolute canonical path")
    if chunk_size <= 0:
        raise ValueError("hash chunk size must be positive")
    paths = [item.path for item in files]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("payload files must have unique sorted paths")
    hashed: list[HashedPayloadFile] = []
    for item in files:
        validate_exact_path(item.path, "payload hash path")
        try:
            descriptor = _open_payload_file(payload, item.path)
        except OSError:
            raise ValueError(f"payload file cannot be safely opened: {item.path}") from None
        try:
            before = os.fstat(descriptor)
            if _classify_payload_entry(before, item.path) != "file":
                raise ValueError(f"payload path is not a regular file: {item.path}")
            if (before.st_dev, before.st_ino, before.st_size) != (
                item.device,
                item.inode,
                item.size,
            ):
                raise ValueError(f"payload file changed after scanning: {item.path}")
            digest = hashlib.sha256()
            actual_size = 0
            while block := os.read(descriptor, chunk_size):
                digest.update(block)
                actual_size += len(block)
            after = os.fstat(descriptor)
            if (
                actual_size != item.size
                or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            ):
                raise ValueError(f"payload file changed while hashing: {item.path}")
            hashed.append(
                HashedPayloadFile(
                    path=item.path,
                    size_bytes=actual_size,
                    sha256=digest.hexdigest(),
                )
            )
        finally:
            os.close(descriptor)
    return tuple(hashed)


def validate_weight_index_closure(
    payload: Path,
    files: tuple[PayloadFile, ...],
    selection: ModelFileSelection,
) -> None:
    """Require every model weight index to reference only selected model shards."""
    files_by_path = {item.path: item for item in files}
    if len(files_by_path) != len(files):
        raise ValueError("payload files repeat a path before weight index validation")
    selected_paths = set(selection.include)
    for path in selection.include:
        if selection.kinds.get(path) != "model" or not path.endswith(".index.json"):
            continue
        item = files_by_path.get(path)
        if item is None:
            raise ValueError("weight index is missing from the downloaded payload")
        content = _read_scanned_payload_file(
            payload,
            item,
            maximum_bytes=MAX_WEIGHT_INDEX_BYTES,
        )
        try:
            value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("weight index is not strict UTF-8 JSON") from error
        if not isinstance(value, dict) or not isinstance(value.get("weight_map"), dict):
            raise ValueError("weight index must contain a weight_map object")
        weight_map = value["weight_map"]
        if not weight_map or not all(
            isinstance(name, str)
            and bool(name)
            and isinstance(shard, str)
            and bool(shard)
            for name, shard in weight_map.items()
        ):
            raise ValueError("weight index contains an invalid weight_map")
        referenced_shards: set[str] = set()
        index_parent = PurePosixPath(path).parent
        for shard in set(weight_map.values()):
            validate_exact_path(shard, "weight index shard")
            resolved_shard = (index_parent / PurePosixPath(shard)).as_posix()
            validate_exact_path(resolved_shard, "resolved weight index shard")
            referenced_shards.add(resolved_shard)
        missing = referenced_shards - selected_paths
        if missing:
            raise ValueError(
                f"weight index references shards outside selection: {sorted(missing)}"
            )
        if any(selection.kinds.get(shard) != "model" for shard in referenced_shards):
            raise ValueError("weight index shards must be explicitly classified as model")


def _read_scanned_payload_file(
    payload: Path,
    item: PayloadFile,
    *,
    maximum_bytes: int,
) -> bytes:
    if item.size > maximum_bytes:
        raise ValueError("weight index exceeds the safe parsing size limit")
    try:
        descriptor = _open_payload_file(payload, item.path)
    except OSError:
        raise ValueError(f"payload file cannot be safely opened: {item.path}") from None
    try:
        before = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size) != (
            item.device,
            item.inode,
            item.size,
        ):
            raise ValueError(f"payload file changed after scanning: {item.path}")
        content = os.read(descriptor, maximum_bytes + 1)
        after = os.fstat(descriptor)
        if len(content) != item.size or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError(f"payload file changed while reading: {item.path}")
        return content
    finally:
        os.close(descriptor)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _open_payload_file(payload: Path, relative: str) -> int:
    parts = PurePosixPath(relative).parts
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
        file_flags |= os.O_NOFOLLOW
    directory = os.open(payload, directory_flags)
    try:
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], file_flags, dir_fd=directory)
    finally:
        os.close(directory)


def validate_payload_matches_selection(
    selection: ModelFileSelection,
    discovered: tuple[DiscoveredFile, ...],
    actual: tuple[HashedPayloadFile, ...],
) -> tuple[HashedPayloadFile, ...]:
    """Require downloaded and hashed files to exactly match the selected tree closure."""
    expected_paths = tuple(selection.include)
    discovered_paths = [item.path for item in discovered]
    if discovered_paths != list(expected_paths) or len(discovered_paths) != len(
        set(discovered_paths)
    ):
        raise ValueError("selected discovery files do not match the exact include list")
    actual_by_path = {item.path: item for item in actual}
    if len(actual_by_path) != len(actual):
        raise ValueError("downloaded payload repeats an actual path")
    missing = set(expected_paths) - set(actual_by_path)
    if missing:
        raise ValueError(f"downloaded payload is missing selected paths: {sorted(missing)}")
    extra = set(actual_by_path) - set(expected_paths)
    if extra:
        raise ValueError(f"downloaded payload contains extra paths: {sorted(extra)}")
    discovered_by_path = {item.path: item for item in discovered}
    size_mismatches = [
        path
        for path in expected_paths
        if actual_by_path[path].size_bytes != discovered_by_path[path].size
    ]
    if size_mismatches:
        raise ValueError(f"downloaded payload sizes differ from discovery: {size_mismatches}")
    return tuple(actual_by_path[path] for path in expected_paths)


def classify_payload_files(
    selection: ModelFileSelection,
    files: tuple[HashedPayloadFile, ...],
) -> tuple[ClassifiedPayloadFile, ...]:
    """Attach only explicit selection kinds; never infer a default kind."""
    paths = tuple(item.path for item in files)
    if paths != selection.include:
        raise ValueError("payload file order and paths must exactly match selection include")
    if set(selection.kinds) != set(paths):
        raise ValueError("selection must explicitly classify every payload file")
    if set(selection.kinds.values()) - SELECTION_KINDS:
        raise ValueError("selection contains an invalid payload file kind")
    return tuple(
        ClassifiedPayloadFile(
            path=item.path,
            size_bytes=item.size_bytes,
            sha256=item.sha256,
            kind=selection.kinds[item.path],
        )
        for item in files
    )


def build_model_manifest(
    metadata: ManifestMetadata,
    files: tuple[ClassifiedPayloadFile, ...],
) -> ModelManifestDocument:
    """Validate all manifest v1 fields and derive sizes from actual payload bytes."""
    if not IDENTIFIER_RE.fullmatch(metadata.model_id) or not IDENTIFIER_RE.fullmatch(
        metadata.worker
    ):
        raise ValueError("manifest model_id and worker must be safe identifiers")
    if not REPOSITORY_RE.fullmatch(metadata.repository):
        raise ValueError("manifest repository must have an owner/name identity")
    if not COMMIT_RE.fullmatch(metadata.revision):
        raise ValueError("manifest revision must be a full lowercase commit")
    if not LICENSE_ID_RE.fullmatch(metadata.license_id):
        raise ValueError("manifest license_id is invalid")
    license_url = urlsplit(metadata.license_url)
    if (
        license_url.scheme != "https"
        or not license_url.hostname
        or license_url.username is not None
        or license_url.password is not None
    ):
        raise ValueError("manifest license_url must be credential-free HTTPS")
    if not isinstance(metadata.requires_terms_acceptance, bool) or not isinstance(
        metadata.trust_remote_code, bool
    ):
        raise ValueError("manifest trust and terms flags must be booleans")
    environment = dict(metadata.environment)
    if not set(environment) >= REQUIRED_ENVIRONMENT_KEYS or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in environment.items()
    ):
        raise ValueError("manifest environment is missing required non-empty string fields")
    validate_exact_path(environment["worker_lock"], "manifest worker lock")
    if not SHA256_RE.fullmatch(environment["worker_lock_sha256"]):
        raise ValueError("manifest worker lock SHA-256 is invalid")
    if not files:
        raise ValueError("manifest must contain at least one payload file")
    paths = [item.path for item in files]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("manifest payload files must have unique Unicode-sorted paths")
    for item in files:
        validate_exact_path(item.path, "manifest payload")
        if item.size_bytes < 0 or not SHA256_RE.fullmatch(item.sha256):
            raise ValueError(f"manifest payload hash or size is invalid: {item.path}")
        if item.kind not in SELECTION_KINDS:
            raise ValueError(f"manifest payload kind is invalid: {item.path}")
    has_remote_code = any(item.kind == "remote_code" for item in files)
    if has_remote_code != metadata.trust_remote_code:
        raise ValueError("manifest remote-code files and trust policy must match")
    _validate_manifest_component_sources(metadata, files)
    frozen_metadata = ManifestMetadata(
        model_id=metadata.model_id,
        repository=metadata.repository,
        revision=metadata.revision,
        worker=metadata.worker,
        license_id=metadata.license_id,
        license_url=metadata.license_url,
        requires_terms_acceptance=metadata.requires_terms_acceptance,
        trust_remote_code=metadata.trust_remote_code,
        environment=MappingProxyType(dict(sorted(environment.items()))),
        component_sources=metadata.component_sources,
    )
    return ModelManifestDocument(
        metadata=frozen_metadata,
        installed_size_bytes=sum(item.size_bytes for item in files),
        files=files,
    )


def _validate_manifest_component_sources(
    metadata: ManifestMetadata,
    files: tuple[ClassifiedPayloadFile, ...],
) -> None:
    repositories = [source.repository for source in metadata.component_sources]
    if repositories != sorted(repositories) or len(repositories) != len(set(repositories)):
        raise ValueError("manifest component sources must have unique sorted repositories")
    if metadata.repository in repositories:
        raise ValueError("manifest component source must differ from payload repository")
    payload = {item.path: item for item in files}
    claimed_installed_paths: set[str] = set()
    for source in metadata.component_sources:
        if not REPOSITORY_RE.fullmatch(source.repository):
            raise ValueError("manifest component source repository is invalid")
        if not COMMIT_RE.fullmatch(source.revision):
            raise ValueError("manifest component source revision is invalid")
        if source.relationship not in {"copied", "derived"}:
            raise ValueError("manifest component source relationship is invalid")
        if not LICENSE_ID_RE.fullmatch(source.license_id):
            raise ValueError("manifest component source license ID is invalid")
        source_license_url = urlsplit(source.license_url)
        if (
            source_license_url.scheme != "https"
            or not source_license_url.hostname
            or source_license_url.username is not None
            or source_license_url.password is not None
        ):
            raise ValueError("manifest component source license URL is invalid")
        if not isinstance(source.requires_terms_acceptance, bool):
            raise ValueError("manifest component source terms flag must be boolean")
        if not source.files:
            raise ValueError("manifest component source files must not be empty")
        installed_paths = [item.installed_path for item in source.files]
        if installed_paths != sorted(installed_paths) or len(installed_paths) != len(
            set(installed_paths)
        ):
            raise ValueError("manifest component files must have unique sorted installed paths")
        source_paths = [item.source_path for item in source.files]
        if len(source_paths) != len(set(source_paths)):
            raise ValueError("manifest component files must have unique source paths")
        for item in source.files:
            validate_exact_path(item.installed_path, "manifest component installed path")
            validate_exact_path(item.source_path, "manifest component source path")
            if not SHA256_RE.fullmatch(item.source_sha256):
                raise ValueError("manifest component source file SHA-256 is invalid")
            if (
                isinstance(item.source_size_bytes, bool)
                or item.source_size_bytes < 0
            ):
                raise ValueError("manifest component source file size is invalid")
            if item.installed_path in claimed_installed_paths:
                raise ValueError("manifest installed file belongs to multiple component sources")
            claimed_installed_paths.add(item.installed_path)
            installed = payload.get(item.installed_path)
            if installed is None:
                raise ValueError("manifest component source refers outside payload")
            if source.relationship == "copied" and (
                installed.sha256 != item.source_sha256
                or installed.size_bytes != item.source_size_bytes
            ):
                raise ValueError("copied manifest component differs from fixed source")


def validate_component_source_discovery(
    source: ReleaseComponentSource,
    discovery: RepositoryDiscovery,
) -> None:
    """Confirm every frozen source file identity at its exact upstream commit."""
    if discovery.repository != source.repository or discovery.revision != source.revision:
        raise ValueError("component source discovery differs from its frozen identity")
    discovered = {item.path: item for item in discovery.files}
    if len(discovered) != len(discovery.files):
        raise ValueError("component source discovery repeats a path")
    for source_file in source.files:
        item = discovered.get(source_file.source_path)
        if item is None:
            raise ValueError("component source file is missing at the frozen revision")
        if item.size != source_file.source_size_bytes:
            raise ValueError("component source file size differs from the revision lock")
        if item.lfs_sha256 != source_file.source_sha256:
            raise ValueError("component source file LFS SHA-256 differs from the revision lock")


def canonical_manifest_bytes(
    manifest: ModelManifestDocument,
    *,
    token: str | None,
) -> bytes:
    """Serialize a manifest as deterministic UTF-8 JSON with one LF terminator."""
    value = manifest.as_dict()
    assert_hf_token_absent(value, token=token, destination="manifest")
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def validate_double_generation_revisions(
    requested_revision: str,
    first: RepositoryDiscovery,
    second: RepositoryDiscovery,
) -> None:
    """Require both clean generation runs to resolve the requested commit exactly."""
    if not COMMIT_RE.fullmatch(requested_revision):
        raise ValueError("double generation revision must be a full lowercase commit")
    if first.revision != requested_revision or second.revision != requested_revision:
        raise ValueError("double generation runs resolved to different commits")


def validate_double_generation_payloads(
    first: tuple[ClassifiedPayloadFile, ...],
    second: tuple[ClassifiedPayloadFile, ...],
) -> None:
    """Require both clean runs to produce identical actual path/size/SHA payloads."""
    first_values = {item.path: (item.size_bytes, item.sha256) for item in first}
    second_values = {item.path: (item.size_bytes, item.sha256) for item in second}
    if len(first_values) != len(first) or len(second_values) != len(second):
        raise ValueError("double generation payload contains duplicate paths")
    if first_values.keys() != second_values.keys():
        raise ValueError("double generation payload file sets differ")
    if first_values != second_values:
        raise ValueError("double generation payload size or SHA-256 differs")


def read_source_date_epoch(environ: Mapping[str, str]) -> int:
    """Require an explicit non-negative SOURCE_DATE_EPOCH for reproducible bundles."""
    raw = environ.get("SOURCE_DATE_EPOCH")
    if raw is None or not raw.isascii() or not raw.isdigit():
        raise ValueError("SOURCE_DATE_EPOCH must be an explicit non-negative integer")
    value = int(raw)
    try:
        datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("SOURCE_DATE_EPOCH is outside the supported range") from error
    return value


def canonical_bundle_bytes(
    *,
    registry_revision: int,
    facts_as_of: str,
    manifests: Mapping[str, bytes],
    generator_lock: bytes,
    source_date_epoch: int,
    token: str | None,
) -> bytes:
    """Build a deterministic bundle index from actual manifest and lock bytes."""
    if isinstance(registry_revision, bool) or registry_revision < 1:
        raise ValueError("bundle registry_revision must be a positive integer")
    try:
        date.fromisoformat(facts_as_of)
    except ValueError as error:
        raise ValueError("bundle facts_as_of must be an ISO date") from error
    if not manifests:
        raise ValueError("bundle must contain at least one manifest")
    if isinstance(source_date_epoch, bool) or source_date_epoch < 0:
        raise ValueError("bundle SOURCE_DATE_EPOCH must be a non-negative integer")
    try:
        generated_at = (
            datetime.fromtimestamp(source_date_epoch, UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("bundle SOURCE_DATE_EPOCH is outside the supported range") from error
    entries: list[dict[str, object]] = []
    for model_id in sorted(manifests):
        if not IDENTIFIER_RE.fullmatch(model_id):
            raise ValueError("bundle contains an unsafe model ID")
        content = manifests[model_id]
        if not isinstance(content, bytes):
            raise ValueError("bundle manifest content must be bytes")
        entries.append(
            {
                "model_id": model_id,
                "path": f"{model_id}.json",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    value = {
        "schema_version": 1,
        "registry_revision": registry_revision,
        "facts_as_of": facts_as_of,
        "generated_at": generated_at,
        "generator": {
            "name": "classscribe-model-manifest",
            "version": "1",
            "lock_sha256": hashlib.sha256(generator_lock).hexdigest(),
        },
        "manifests": entries,
    }
    assert_hf_token_absent(value, token=token, destination="bundle")
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def validate_double_generation_outputs(
    first_manifests: Mapping[str, bytes],
    second_manifests: Mapping[str, bytes],
    first_bundle: bytes,
    second_bundle: bytes,
) -> None:
    """Require canonical manifest and bundle bytes to match across clean runs."""
    if first_manifests.keys() != second_manifests.keys():
        raise ValueError("double generation manifest sets differ")
    if any(first_manifests[key] != second_manifests[key] for key in first_manifests):
        raise ValueError("double generation manifest bytes differ")
    if first_bundle != second_bundle:
        raise ValueError("double generation bundle bytes differ")


@dataclass(frozen=True, slots=True)
class GenerationRun:
    """The publication-relevant result of one clean generation directory."""

    revision: str
    payload: tuple[ClassifiedPayloadFile, ...]
    manifests: Mapping[str, bytes]
    bundle: bytes


def require_identical_generation_runs(
    requested_revision: str,
    first: GenerationRun,
    second: GenerationRun,
) -> None:
    """Stop publication with a path/hash-only report when clean runs differ."""
    if not COMMIT_RE.fullmatch(requested_revision):
        raise ValueError("double generation revision must be a full lowercase commit")
    differences: list[dict[str, str | None]] = []
    requested_sha = hashlib.sha256(requested_revision.encode("ascii")).hexdigest()
    for label, revision in (("first", first.revision), ("second", second.revision)):
        if revision != requested_revision:
            differences.append(
                {
                    "path": f"resolved-revision/{label}",
                    "first_sha256": hashlib.sha256(revision.encode("utf-8")).hexdigest(),
                    "second_sha256": requested_sha,
                }
            )
    first_hashes = _generation_run_hashes(first)
    second_hashes = _generation_run_hashes(second)
    for path in sorted(first_hashes.keys() | second_hashes.keys()):
        first_sha = first_hashes.get(path)
        second_sha = second_hashes.get(path)
        if first_sha != second_sha:
            differences.append(
                {"path": path, "first_sha256": first_sha, "second_sha256": second_sha}
            )
    if differences:
        differences.sort(key=lambda item: item["path"] or "")
        report = (
            json.dumps(
                {"differences": differences},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        raise GenerationMismatchError(report)
    validate_double_generation_payloads(first.payload, second.payload)
    validate_double_generation_outputs(
        first.manifests,
        second.manifests,
        first.bundle,
        second.bundle,
    )


def _generation_run_hashes(run: GenerationRun) -> dict[str, str]:
    result = {
        "resolved-revision": hashlib.sha256(run.revision.encode("utf-8")).hexdigest(),
        "bundle.v1.json": hashlib.sha256(run.bundle).hexdigest(),
    }
    for item in run.payload:
        result[f"payload/{item.path}"] = item.sha256
    for model_id, content in run.manifests.items():
        if not IDENTIFIER_RE.fullmatch(model_id):
            raise ValueError("generation run contains an unsafe manifest model ID")
        result[f"manifests/{model_id}.json"] = hashlib.sha256(content).hexdigest()
    return result


@dataclass(frozen=True, slots=True)
class GeneratedModelRun:
    discovery: RepositoryDiscovery
    payload: tuple[ClassifiedPayloadFile, ...]
    manifest: bytes


def generate_release_bundle(
    inputs: ReleaseInputs,
    *,
    source_date_epoch: int,
    token: str | None,
    accepted_repositories: Collection[str],
    api_factory: Callable[[], RepositoryTreeApi] | None = None,
    downloader: Callable[..., object] = snapshot_download,
) -> tuple[Mapping[str, bytes], bytes]:
    """Generate every selected model twice and return only verified canonical output."""
    first_manifests: dict[str, bytes] = {}
    second_manifests: dict[str, bytes] = {}
    for model in inputs.models:
        require_gated_access_approval(
            model.repository,
            requires_terms_acceptance=model.requires_terms_acceptance,
            accepted_repositories=accepted_repositories,
            token=token,
        )
        for source in model.component_sources:
            require_gated_access_approval(
                source.repository,
                requires_terms_acceptance=source.requires_terms_acceptance,
                accepted_repositories=accepted_repositories,
                token=token,
            )
        try:
            selection = inputs.selections.selection(model.model_id)
        except KeyError:
            raise ValueError("release generation requires a selection for every model") from None
        first = _generate_model_once(
            model,
            selection,
            token=token,
            api=None if api_factory is None else api_factory(),
            downloader=downloader,
        )
        second = _generate_model_once(
            model,
            selection,
            token=token,
            api=None if api_factory is None else api_factory(),
            downloader=downloader,
        )
        require_identical_generation_runs(
            model.revision,
            GenerationRun(
                revision=first.discovery.revision,
                payload=first.payload,
                manifests={model.model_id: first.manifest},
                bundle=b"",
            ),
            GenerationRun(
                revision=second.discovery.revision,
                payload=second.payload,
                manifests={model.model_id: second.manifest},
                bundle=b"",
            ),
        )
        first_manifests[model.model_id] = first.manifest
        second_manifests[model.model_id] = second.manifest
    lock_path = inputs.root / "tools/model-manifest/uv.lock"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ValueError("manifest tool lock is missing or unsafe")
    lock = lock_path.read_bytes()
    first_bundle = canonical_bundle_bytes(
        registry_revision=inputs.registry_revision,
        facts_as_of=inputs.facts_as_of,
        manifests=first_manifests,
        generator_lock=lock,
        source_date_epoch=source_date_epoch,
        token=token,
    )
    second_bundle = canonical_bundle_bytes(
        registry_revision=inputs.registry_revision,
        facts_as_of=inputs.facts_as_of,
        manifests=second_manifests,
        generator_lock=lock,
        source_date_epoch=source_date_epoch,
        token=token,
    )
    if first_manifests != second_manifests or first_bundle != second_bundle:
        first_artifacts = {
            **{
                f"manifests/{model_id}.json": content
                for model_id, content in first_manifests.items()
            },
            "bundle.v1.json": first_bundle,
        }
        second_artifacts = {
            **{
                f"manifests/{model_id}.json": content
                for model_id, content in second_manifests.items()
            },
            "bundle.v1.json": second_bundle,
        }
        raise GenerationMismatchError(
            _artifact_difference_report(first_artifacts, second_artifacts)
        )
    return MappingProxyType(first_manifests), first_bundle


def _generate_model_once(
    model: ReleaseModel,
    selection: ModelFileSelection,
    *,
    token: str | None,
    api: RepositoryTreeApi | None,
    downloader: Callable[..., object],
) -> GeneratedModelRun:
    discovery = discover_repository_tree(
        model.repository,
        model.revision,
        token=token,
        api=api,
    )
    for source in model.component_sources:
        source_discovery = discover_repository_tree(
            source.repository,
            source.revision,
            token=token,
            api=api,
        )
        validate_component_source_discovery(source, source_discovery)
    selected = validate_selection_against_discovery(selection, discovery)
    with generation_workspace(token=token) as workspace:
        payload_root = download_selected_snapshot(
            model.repository,
            model.revision,
            selection,
            workspace,
            token=token,
            downloader=downloader,
        )
        remove_huggingface_metadata(payload_root)
        scanned = scan_payload(
            payload_root,
            expected_sizes={item.path: item.size for item in selected},
        )
        validate_weight_index_closure(payload_root, scanned, selection)
        hashed = hash_payload_files(payload_root, scanned)
        matched = validate_payload_matches_selection(selection, selected, hashed)
        classified = classify_payload_files(selection, matched)
        manifest = canonical_manifest_bytes(
            build_model_manifest(
                ManifestMetadata(
                    model_id=model.model_id,
                    repository=model.repository,
                    revision=model.revision,
                    worker=model.worker,
                    license_id=model.license_id,
                    license_url=model.license_url,
                    requires_terms_acceptance=model.requires_terms_acceptance,
                    trust_remote_code=model.trust_remote_code,
                    component_sources=model.component_sources,
                    environment={
                        "python": "3.12",
                        "runtime_backend": model.runtime_backend,
                        "dtype": model.dtype,
                        "worker_lock": model.dependency_lock,
                        "worker_lock_sha256": model.dependency_lock_sha256,
                    },
                ),
                classified,
            ),
            token=token,
        )
    return GeneratedModelRun(discovery=discovery, payload=classified, manifest=manifest)


def _artifact_difference_report(
    first: Mapping[str, bytes], second: Mapping[str, bytes]
) -> bytes:
    differences = [
        {
            "path": path,
            "first_sha256": None
            if path not in first
            else hashlib.sha256(first[path]).hexdigest(),
            "second_sha256": None
            if path not in second
            else hashlib.sha256(second[path]).hexdigest(),
        }
        for path in sorted(first.keys() | second.keys())
        if first.get(path) != second.get(path)
    ]
    return (
        json.dumps({"differences": differences}, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode()


def write_release_bundle(
    output_directory: Path,
    manifests: Mapping[str, bytes],
    bundle: bytes,
    *,
    token: str | None,
) -> None:
    """Atomically replace canonical members first and the bundle index last."""
    if output_directory.is_symlink():
        raise ValueError("bundle output directory must not be a symlink")
    output_directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    if not output_directory.is_dir():
        raise ValueError("bundle output must be a directory")
    expected_names = {"bundle.v1.json", *(f"{model_id}.json" for model_id in manifests)}
    existing_names = {path.name for path in output_directory.iterdir()}
    if existing_names - expected_names:
        raise ValueError("bundle output contains files outside the generated member set")
    for model_id in sorted(manifests):
        if not IDENTIFIER_RE.fullmatch(model_id):
            raise ValueError("bundle output contains an unsafe model ID")
        content = manifests[model_id]
        assert_hf_token_absent(content, token=token, destination="manifest")
        _atomic_write(output_directory / f"{model_id}.json", content)
    assert_hf_token_absent(bundle, token=token, destination="bundle")
    _atomic_write(output_directory / "bundle.v1.json", bundle)


def _atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ValueError("release output destination must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
