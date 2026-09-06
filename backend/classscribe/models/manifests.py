"""Read-only contracts for the bundled model-manifest index."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classscribe.models.licenses import ModelLicense
from classscribe.models.manager import (
    ManifestComponentSource,
    ManifestComponentSourceFile,
    ModelManifest,
)
from classscribe.models.registry import ModelEntry, ModelRegistry

MODEL_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
MANIFEST_PATH_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}\.json$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
UTC_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
MANIFEST_FIELDS = frozenset(
    {
        "manifest_version",
        "model_id",
        "repository",
        "revision",
        "worker",
        "license_id",
        "license_url",
        "requires_terms_acceptance",
        "trust_remote_code",
        "estimated_download_bytes",
        "installed_size_bytes",
        "environment",
        "files",
    }
)
OPTIONAL_MANIFEST_FIELDS = frozenset({"component_sources"})
MANIFEST_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes", "kind"})
COMPONENT_SOURCE_FIELDS = frozenset(
    {
        "repository",
        "revision",
        "relationship",
        "license_id",
        "license_url",
        "requires_terms_acceptance",
        "files",
    }
)
COMPONENT_SOURCE_FILE_FIELDS = frozenset(
    {"installed_path", "source_path", "source_sha256", "source_size_bytes"}
)
REVISION_LOCK_FIELDS = frozenset(
    {"schema_version", "registry_revision", "facts_as_of", "models"}
)
REVISION_COMPONENT_SOURCE_FIELDS = frozenset(
    {"model_id", "repository", "revision", "relationship", "files"}
)
REQUIRED_ENVIRONMENT_KEYS = frozenset(
    {"python", "runtime_backend", "dtype", "worker_lock", "worker_lock_sha256"}
)
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_REVISION_LOCK_BYTES = 16 * 1024 * 1024
MAX_WORKER_LOCK_BYTES = 128 * 1024 * 1024


class ManifestBundleEntry(BaseModel):
    """One manifest file addressed relative to its bundle directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(pattern=MODEL_ID_PATTERN)
    path: str = Field(pattern=MANIFEST_PATH_PATTERN)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_path(self) -> ManifestBundleEntry:
        if self.path == "bundle.v1.json":
            raise ValueError("bundle.v1.json is reserved for the bundle index")
        return self


class ManifestBundleGenerator(BaseModel):
    """Identity of the separately locked release-time generator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["classscribe-model-manifest"]
    version: Literal["1"]
    lock_sha256: str = Field(pattern=SHA256_PATTERN)


class ManifestBundleIndex(BaseModel):
    """Immutable bundle metadata with deterministic member ordering."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    registry_revision: int = Field(ge=1)
    facts_as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    generated_at: str = Field(pattern=UTC_TIMESTAMP_PATTERN)
    generator: ManifestBundleGenerator
    manifests: tuple[ManifestBundleEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest_entries(self) -> ManifestBundleIndex:
        model_ids = [entry.model_id for entry in self.manifests]
        if model_ids != sorted(model_ids):
            raise ValueError("bundle manifest entries must be sorted by model_id")
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("bundle model IDs must be unique")
        paths = [entry.path for entry in self.manifests]
        if len(paths) != len(set(paths)):
            raise ValueError("bundle manifest paths must be unique")
        return self


@dataclass(frozen=True, slots=True)
class LoadedManifestBundle:
    """A parsed bundle whose members retain deterministic index order."""

    index: ManifestBundleIndex
    manifests: tuple[ModelManifest, ...]

    def manifest(self, model_id: str) -> ModelManifest:
        try:
            return next(item for item in self.manifests if item.model_id == model_id)
        except StopIteration as error:
            raise KeyError(model_id) from error

    def manifest_sha256(self, model_id: str) -> str:
        try:
            return next(
                entry.sha256 for entry in self.index.manifests if entry.model_id == model_id
            )
        except StopIteration as error:
            raise KeyError(model_id) from error


def load_builtin_manifest_bundle(
    path: Path,
    registry: ModelRegistry,
    licenses: Mapping[str, ModelLicense],
) -> LoadedManifestBundle:
    """Load the built-in revision lock and bundle from one immutable resource root."""
    root = _resource_root_for_bundle(path)
    revisions, _revision_bytes = _load_json_document(
        root,
        PurePosixPath("config/model-revisions.lock.json"),
        "model revision lock",
        maximum_bytes=MAX_REVISION_LOCK_BYTES,
    )
    return load_manifest_bundle(path, registry, revisions, licenses)


def load_manifest_bundle(
    path: Path,
    registry: ModelRegistry,
    revisions: Mapping[str, Any],
    licenses: Mapping[str, ModelLicense],
) -> LoadedManifestBundle:
    """Load one complete built-in bundle and all of its manifest members."""
    root = _resource_root_for_bundle(path)
    bundle_relative = PurePosixPath("config/model-manifests/v1/bundle.v1.json")
    index_value, index_bytes = _load_json_document(
        root,
        bundle_relative,
        "model manifest bundle",
        maximum_bytes=MAX_BUNDLE_BYTES,
    )
    _require_canonical_json(index_value, index_bytes, "model manifest bundle")
    index = ManifestBundleIndex.model_validate(index_value)
    if index.registry_revision != registry.registry_revision:
        raise ValueError("bundle registry revision differs from model registry")
    if index.facts_as_of != registry.facts_as_of:
        raise ValueError("bundle facts date differs from model registry")
    if revisions.get("registry_revision") != registry.registry_revision:
        raise ValueError("revision lock registry revision differs from model registry")
    if revisions.get("facts_as_of") != registry.facts_as_of:
        raise ValueError("revision lock facts date differs from model registry")
    revision_rows = _revision_rows(revisions)
    expected_ids = {model.id for model in registry.models}
    component_sources = _component_sources(
        revisions,
        registry=registry,
        licenses=licenses,
    )
    indexed_ids = {entry.model_id for entry in index.manifests}
    if indexed_ids != expected_ids:
        raise ValueError("bundle model IDs differ from model registry")
    if set(revision_rows) != expected_ids:
        raise ValueError("revision lock model IDs differ from model registry")
    expected_repositories = {model.repository for model in registry.models} | {
        source.repository
        for sources in component_sources.values()
        for source in sources
    }
    if set(licenses) != expected_repositories:
        raise ValueError("model license repositories differ from model and component sources")

    manifests: list[ModelManifest] = []
    for entry in index.manifests:
        if entry.path != f"{entry.model_id}.json":
            raise ValueError("bundle entry path must match its model ID")
        value, manifest_bytes = _load_json_document(
            root,
            PurePosixPath("config/model-manifests/v1") / entry.path,
            "model manifest member",
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        if hashlib.sha256(manifest_bytes).hexdigest() != entry.sha256:
            raise ValueError("bundle manifest member SHA-256 differs from index")
        _require_canonical_json(value, manifest_bytes, "model manifest member")
        _validate_manifest_shape(value)
        manifest = ModelManifest.from_dict(value)
        manifest = replace(
            manifest,
            environment=MappingProxyType(dict(manifest.environment)),
        )
        if manifest.model_id != entry.model_id:
            raise ValueError("bundle entry model ID differs from manifest member")
        model = registry.model(entry.model_id)
        _validate_revision_row(revision_rows[entry.model_id], model)
        _validate_manifest_contract(
            manifest,
            model,
            licenses[model.repository],
            root,
            facts_as_of=registry.facts_as_of,
            component_sources=component_sources.get(model.id, ()),
        )
        manifests.append(manifest)
    return LoadedManifestBundle(index=index, manifests=tuple(manifests))


def _load_json_document(
    root: Path,
    relative: PurePosixPath,
    label: str,
    *,
    maximum_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    try:
        content = _read_regular_resource_file(
            root,
            relative,
            label,
            maximum_bytes=maximum_bytes,
        )
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError):
            raise
        raise ValueError(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value), content


def _require_canonical_json(value: Mapping[str, Any], content: bytes, label: str) -> None:
    canonical = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if content != canonical:
        raise ValueError(f"{label} is not canonical JSON")


def _revision_rows(revisions: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    fields = set(revisions)
    if not fields >= REVISION_LOCK_FIELDS or fields - REVISION_LOCK_FIELDS - {
        "component_sources"
    }:
        raise ValueError("revision lock fields differ from the v1 contract")
    if revisions.get("schema_version") != 1:
        raise ValueError("revision lock schema version is not 1")
    raw_models = revisions.get("models")
    if not isinstance(raw_models, list):
        raise ValueError("revision lock models must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw_models:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("revision lock contains an invalid model row")
        model_id = cast(str, item["id"])
        if model_id in result:
            raise ValueError("revision lock repeats a model ID")
        result[model_id] = cast(dict[str, Any], item)
    return result


def _component_sources(
    revisions: Mapping[str, Any],
    *,
    registry: ModelRegistry,
    licenses: Mapping[str, ModelLicense],
) -> dict[str, tuple[ManifestComponentSource, ...]]:
    raw_sources = revisions.get("component_sources", [])
    if not isinstance(raw_sources, list):
        raise ValueError("revision lock component_sources must be an array")
    registry_rows = {model.id: model for model in registry.models}
    identities: list[tuple[str, str]] = []
    installed_by_model: dict[str, set[str]] = {}
    result: dict[str, list[ManifestComponentSource]] = {}
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict) or set(raw_source) != (
            REVISION_COMPONENT_SOURCE_FIELDS
        ):
            raise ValueError("revision lock component source fields differ from v1")
        _validate_revision_component_source_shape(raw_source)
        model_id = cast(str, raw_source["model_id"])
        repository = cast(str, raw_source["repository"])
        if model_id not in registry_rows:
            raise ValueError("revision lock component source model is not in registry")
        if repository == registry_rows[model_id].repository:
            raise ValueError("revision lock component source repeats payload repository")
        identity = (model_id, repository)
        identities.append(identity)
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError("revision lock component sources must be unique and sorted")
        license_record = licenses.get(repository)
        if license_record is None:
            raise ValueError("revision lock component source has no license inventory row")
        if license_record.verified_as_of != registry.facts_as_of:
            raise ValueError("component source license facts date differs from registry")
        raw_files = cast(list[dict[str, Any]], raw_source["files"])
        files = tuple(ManifestComponentSourceFile(**item) for item in raw_files)
        claimed = installed_by_model.setdefault(model_id, set())
        installed_paths = {item.installed_path for item in files}
        if claimed & installed_paths:
            raise ValueError("revision lock component sources overlap installed paths")
        claimed.update(installed_paths)
        result.setdefault(model_id, []).append(
            ManifestComponentSource(
                repository=repository,
                revision=cast(str, raw_source["revision"]),
                relationship=cast(Literal["copied", "derived"], raw_source["relationship"]),
                license_id=license_record.license_id,
                license_url=license_record.license_url,
                requires_terms_acceptance=license_record.requires_terms_acceptance,
                files=files,
            )
        )
    return {model_id: tuple(sources) for model_id, sources in result.items()}


def _validate_revision_component_source_shape(source: dict[str, Any]) -> None:
    if not all(
        isinstance(source[field], str)
        for field in ("model_id", "repository", "revision", "relationship")
    ):
        raise ValueError("revision lock component source identity fields must be strings")
    raw_files = source["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("revision lock component source files must be non-empty")
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != COMPONENT_SOURCE_FILE_FIELDS:
            raise ValueError("revision lock component source file fields differ from v1")
        if not all(
            isinstance(item[field], str)
            for field in ("installed_path", "source_path", "source_sha256")
        ):
            raise ValueError("revision lock component source file identity must be strings")
        if type(item["source_size_bytes"]) is not int:
            raise ValueError("revision lock component source file size must be an integer")


def _validate_revision_row(row: Mapping[str, Any], model: ModelEntry) -> None:
    expected = {
        "id": model.id,
        "repository": model.repository,
        "revision": model.revision,
        "worker": model.worker,
        "enabled": model.enabled,
    }
    if dict(row) != expected:
        raise ValueError(f"revision lock row differs from model registry: {model.id}")


def _validate_manifest_shape(value: Mapping[str, Any]) -> None:
    fields = set(value)
    if fields not in {MANIFEST_FIELDS, MANIFEST_FIELDS | OPTIONAL_MANIFEST_FIELDS}:
        raise ValueError("model manifest fields differ from the v1 contract")
    string_fields = ("model_id", "repository", "revision", "worker", "license_id", "license_url")
    if any(not isinstance(value[field], str) for field in string_fields):
        raise ValueError("model manifest identity fields must be strings")
    if type(value["manifest_version"]) is not int:
        raise ValueError("model manifest version must be an integer")
    for field in ("requires_terms_acceptance", "trust_remote_code"):
        if type(value[field]) is not bool:
            raise ValueError("model manifest policy fields must be booleans")
    for field in ("estimated_download_bytes", "installed_size_bytes"):
        if type(value[field]) is not int:
            raise ValueError("model manifest byte counts must be integers")
    environment = value["environment"]
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in environment.items()
    ):
        raise ValueError("model manifest environment must be a string map")
    if not set(environment) >= REQUIRED_ENVIRONMENT_KEYS:
        raise ValueError("model manifest environment is incomplete")
    files = value["files"]
    if not isinstance(files, list):
        raise ValueError("model manifest files must be an array")
    for item in files:
        if not isinstance(item, dict) or set(item) != MANIFEST_FILE_FIELDS:
            raise ValueError("model manifest file fields differ from the v1 contract")
        if not all(isinstance(item[field], str) for field in ("path", "sha256", "kind")):
            raise ValueError("model manifest file identity fields must be strings")
        if type(item["size_bytes"]) is not int:
            raise ValueError("model manifest file size must be an integer")
    if "component_sources" not in value:
        return
    sources = value["component_sources"]
    if not isinstance(sources, list) or not sources:
        raise ValueError("model manifest component_sources must be a non-empty array")
    for source in sources:
        if not isinstance(source, dict) or set(source) != COMPONENT_SOURCE_FIELDS:
            raise ValueError("model manifest component source fields differ from v1")
        if not all(
            isinstance(source[field], str)
            for field in (
                "repository",
                "revision",
                "relationship",
                "license_id",
                "license_url",
            )
        ):
            raise ValueError("model manifest component source identity must be strings")
        if type(source["requires_terms_acceptance"]) is not bool:
            raise ValueError("model manifest component source terms flag must be boolean")
        source_files = source["files"]
        if not isinstance(source_files, list) or not source_files:
            raise ValueError("model manifest component source files must be non-empty")
        for item in source_files:
            if not isinstance(item, dict) or set(item) != COMPONENT_SOURCE_FILE_FIELDS:
                raise ValueError("model manifest component source file fields differ from v1")
            if not all(
                isinstance(item[field], str)
                for field in ("installed_path", "source_path", "source_sha256")
            ):
                raise ValueError("model manifest component source file identity must be strings")
            if type(item["source_size_bytes"]) is not int:
                raise ValueError("model manifest component source file size must be an integer")


def _validate_manifest_contract(
    manifest: ModelManifest,
    model: ModelEntry,
    license_record: ModelLicense,
    root: Path,
    *,
    facts_as_of: str,
    component_sources: tuple[ManifestComponentSource, ...],
) -> None:
    identity = (
        manifest.model_id,
        manifest.repository,
        manifest.revision,
        manifest.worker,
        manifest.trust_remote_code,
    )
    expected_identity = (
        model.id,
        model.repository,
        model.revision,
        model.worker,
        model.trust_remote_code,
    )
    if identity != expected_identity:
        raise ValueError(f"model manifest identity differs from registry: {model.id}")
    license_contract = (
        manifest.license_id,
        manifest.license_url,
        manifest.requires_terms_acceptance,
    )
    expected_license = (
        license_record.license_id,
        license_record.license_url,
        license_record.requires_terms_acceptance,
    )
    if license_record.repository != model.repository or license_contract != expected_license:
        raise ValueError(f"model manifest license differs from inventory: {model.id}")
    if license_record.verified_as_of != facts_as_of:
        raise ValueError(f"model license facts date differs from registry: {model.id}")
    if manifest.component_sources != component_sources:
        raise ValueError(f"model manifest component sources differ from inventory: {model.id}")
    expected_environment = {
        "python": "3.12",
        "runtime_backend": model.runtime_backend,
        "dtype": model.dtype,
        "worker_lock": model.dependency_lock,
        "worker_lock_sha256": model.dependency_lock_sha256,
    }
    if any(manifest.environment.get(key) != value for key, value in expected_environment.items()):
        raise ValueError(f"model manifest environment differs from registry: {model.id}")
    if manifest.estimated_download_bytes != manifest.installed_size_bytes:
        raise ValueError(f"model manifest byte totals differ: {model.id}")
    worker_lock = _read_regular_resource_file(
        root,
        PurePosixPath(model.dependency_lock),
        f"worker dependency lock for {model.id}",
        maximum_bytes=MAX_WORKER_LOCK_BYTES,
    )
    actual_lock_sha = hashlib.sha256(worker_lock).hexdigest()
    if actual_lock_sha != model.dependency_lock_sha256:
        raise ValueError(f"worker dependency lock SHA-256 differs: {model.id}")


def _resource_root_for_bundle(path: Path) -> Path:
    bundle_path = path.absolute()
    bundle_directory = bundle_path.parent
    if (
        bundle_path.name != "bundle.v1.json"
        or bundle_directory.name != "v1"
        or bundle_directory.parent.name != "model-manifests"
        or bundle_directory.parent.parent.name != "config"
    ):
        raise ValueError("bundle path must use the built-in config/model-manifests/v1 layout")
    root = bundle_directory.parents[2]
    if root.is_symlink() or not root.is_dir():
        raise ValueError("bundle resource root is missing or unsafe")
    return root


def _read_regular_resource_file(
    root: Path,
    relative: PurePosixPath,
    label: str,
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or "." in relative.parts
        or relative.as_posix() != str(relative)
    ):
        raise ValueError(f"{label} path is unsafe")
    if maximum_bytes < 1:
        raise ValueError("resource byte limit must be positive")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
        file_flags |= os.O_NOFOLLOW
    try:
        directory = os.open(root, directory_flags)
    except OSError as error:
        raise ValueError(f"{label} resource root cannot be safely opened") from error
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        before = os.stat(relative.parts[-1], dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"{label} must be a single-link regular file")
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=directory)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino, opened.st_size)
                != (before.st_dev, before.st_ino, before.st_size)
            ):
                raise ValueError(f"{label} changed before it could be read")
            if opened.st_size > maximum_bytes:
                raise ValueError(f"{label} exceeds the safe byte limit")
            chunks: list[bytes] = []
            actual_size = 0
            while chunk := os.read(descriptor, min(1024 * 1024, maximum_bytes + 1)):
                chunks.append(chunk)
                actual_size += len(chunk)
                if actual_size > maximum_bytes:
                    raise ValueError(f"{label} exceeds the safe byte limit")
            after = os.fstat(descriptor)
            if actual_size != opened.st_size or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) != (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ):
                raise ValueError(f"{label} changed while it was read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ValueError(f"{label} cannot be safely opened") from error
    finally:
        os.close(directory)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
