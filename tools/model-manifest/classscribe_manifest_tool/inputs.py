"""Frozen release input loading independent from the ClassScribe runtime."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from classscribe_manifest_tool.selection import (
    FileSelectionIndex,
    load_file_selection,
    load_yaml_mapping,
    validate_exact_path,
)

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[^\s/]+/[^\s/]+$")
LICENSE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMPONENT_SOURCE_RELATIONSHIPS = frozenset({"copied", "derived"})


@dataclass(frozen=True, slots=True)
class ReleaseComponentSourceFile:
    installed_path: str
    source_path: str
    source_sha256: str
    source_size_bytes: int


@dataclass(frozen=True, slots=True)
class ReleaseComponentSource:
    repository: str
    revision: str
    relationship: Literal["copied", "derived"]
    license_id: str
    license_url: str
    requires_terms_acceptance: bool
    files: tuple[ReleaseComponentSourceFile, ...]


@dataclass(frozen=True, slots=True)
class ReleaseModel:
    model_id: str
    repository: str
    revision: str
    worker: str
    runtime_backend: str
    dtype: str
    trust_remote_code: bool
    dependency_lock: str
    dependency_lock_sha256: str
    license_id: str
    license_url: str
    requires_terms_acceptance: bool
    enabled: bool
    experimental: bool = False
    component_sources: tuple[ReleaseComponentSource, ...] = ()


@dataclass(frozen=True, slots=True)
class ReleaseInputs:
    root: Path
    registry_revision: int
    facts_as_of: str
    models: tuple[ReleaseModel, ...]
    selections: FileSelectionIndex


def load_release_inputs(
    *,
    registry_path: Path,
    revisions_path: Path,
    licenses_path: Path,
    selection_path: Path,
    require_complete_selection: bool,
) -> ReleaseInputs:
    """Load and cross-check the four frozen inventories before any network request."""
    root = registry_path.parent.parent.resolve(strict=True)
    registry = load_yaml_mapping(registry_path, label="model registry")
    schema = _load_json(root / "config/schema/model-registry.v1.schema.json", "registry schema")
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(registry)
    except ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path)
        raise ValueError(f"model registry failed schema validation at {location}") from None
    revisions = _load_json(revisions_path, "revision lock")
    licenses = _load_json(licenses_path, "license inventory")
    selections = load_file_selection(selection_path)
    registry_rows = _index_rows(registry.get("models"), "id", "model registry")
    revision_rows = _index_rows(revisions.get("models"), "id", "revision lock")
    license_rows = _index_rows(licenses.get("models"), "repository", "license inventory")
    model_ids = set(registry_rows)
    if set(revision_rows) != model_ids:
        raise ValueError("revision lock IDs differ from model registry")
    if require_complete_selection and set(selections.models) != model_ids:
        raise ValueError("selection IDs differ from model registry")
    if not set(selections.models) <= model_ids:
        raise ValueError("selection contains IDs outside model registry")
    if not (
        registry.get("registry_revision") == revisions.get("registry_revision")
        and registry.get("facts_as_of")
        == revisions.get("facts_as_of")
        == licenses.get("facts_as_of")
    ):
        raise ValueError("frozen inventory headers differ")
    if revisions.get("schema_version") != 1 or licenses.get("schema_version") != 1:
        raise ValueError("frozen inventory schema versions must be 1")
    _require_exact_keys(
        revisions,
        {"schema_version", "registry_revision", "facts_as_of", "models"},
        {"component_sources"},
        "revision lock",
    )
    _require_exact_keys(
        licenses,
        {"schema_version", "facts_as_of", "models"},
        set(),
        "license inventory",
    )
    raw_component_sources = revisions.get("component_sources", [])
    component_sources = _parse_component_sources(
        raw_component_sources,
        registry_rows=registry_rows,
        selections=selections,
    )
    expected_repositories = {
        cast(str, row["repository"]) for row in registry_rows.values()
    } | {
        source.repository
        for model_sources in component_sources.values()
        for source in model_sources
    }
    if set(license_rows) != expected_repositories:
        raise ValueError("license repositories differ from frozen model and component sources")
    _validate_license_rows(license_rows)
    models: list[ReleaseModel] = []
    for model_id in sorted(model_ids):
        registry_row = registry_rows[model_id]
        revision_row = revision_rows[model_id]
        _require_exact_keys(
            revision_row,
            {"id", "repository", "revision", "worker", "enabled"},
            set(),
            "revision lock model row",
        )
        for field in ("repository", "revision", "worker", "enabled"):
            if registry_row.get(field) != revision_row.get(field):
                raise ValueError("revision lock row differs from model registry")
        repository = cast(str, registry_row["repository"])
        license_row = license_rows[repository]
        dependency_lock = cast(str, registry_row["dependency_lock"])
        validate_exact_path(dependency_lock, "worker dependency lock")
        lock_path = root / dependency_lock
        if lock_path.is_symlink() or not lock_path.is_file():
            raise ValueError("worker dependency lock is missing or unsafe")
        lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        if lock_sha != registry_row.get("dependency_lock_sha256"):
            raise ValueError("worker dependency lock SHA differs from registry")
        models.append(
            ReleaseModel(
                model_id=model_id,
                repository=repository,
                revision=cast(str, registry_row["revision"]),
                worker=cast(str, registry_row["worker"]),
                runtime_backend=cast(str, registry_row["runtime_backend"]),
                dtype=cast(str, registry_row["dtype"]),
                trust_remote_code=cast(bool, registry_row["trust_remote_code"]),
                dependency_lock=dependency_lock,
                dependency_lock_sha256=lock_sha,
                license_id=cast(str, license_row["license_id"]),
                license_url=cast(str, license_row["license_url"]),
                requires_terms_acceptance=cast(
                    bool, license_row["requires_terms_acceptance"]
                ),
                enabled=cast(bool, registry_row["enabled"]),
                experimental=cast(bool, registry_row.get("experimental", False)),
                component_sources=tuple(
                    ReleaseComponentSource(
                        repository=source.repository,
                        revision=source.revision,
                        relationship=source.relationship,
                        license_id=cast(str, license_rows[source.repository]["license_id"]),
                        license_url=cast(str, license_rows[source.repository]["license_url"]),
                        requires_terms_acceptance=cast(
                            bool,
                            license_rows[source.repository]["requires_terms_acceptance"],
                        ),
                        files=source.files,
                    )
                    for source in component_sources.get(model_id, ())
                ),
            )
        )
    return ReleaseInputs(
        root=root,
        registry_revision=cast(int, registry["registry_revision"]),
        facts_as_of=cast(str, registry["facts_as_of"]),
        models=tuple(models),
        selections=FileSelectionIndex(
            schema_version=selections.schema_version,
            models=MappingProxyType(dict(selections.models)),
        ),
    )


def _parse_component_sources(
    raw: object,
    *,
    registry_rows: dict[str, dict[str, Any]],
    selections: FileSelectionIndex,
) -> dict[str, tuple[ReleaseComponentSource, ...]]:
    if not isinstance(raw, list):
        raise ValueError("revision lock component_sources must be an array")
    parsed: dict[str, list[ReleaseComponentSource]] = {}
    identities: list[tuple[str, str]] = []
    installed_paths: dict[str, set[str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("revision lock contains an invalid component source row")
        _require_exact_keys(
            item,
            {"model_id", "repository", "revision", "relationship", "files"},
            set(),
            "revision lock component source row",
        )
        model_id = item["model_id"]
        repository = item["repository"]
        revision = item["revision"]
        relationship = item["relationship"]
        if not isinstance(model_id, str) or model_id not in registry_rows:
            raise ValueError("component source refers to a model outside the registry")
        if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
            raise ValueError("component source repository must use owner/name")
        if repository == registry_rows[model_id].get("repository"):
            raise ValueError("component source repository must differ from payload repository")
        if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
            raise ValueError("component source revision must be a full lowercase commit")
        if relationship not in COMPONENT_SOURCE_RELATIONSHIPS:
            raise ValueError("component source relationship must be copied or derived")
        files = _parse_component_source_files(item["files"])
        identity = (model_id, repository)
        identities.append(identity)
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError(
                "component sources must have unique model/repository sorted identities"
            )
        claimed = installed_paths.setdefault(model_id, set())
        for source_file in files:
            if source_file.installed_path in claimed:
                raise ValueError("an installed path belongs to multiple component sources")
            claimed.add(source_file.installed_path)
            selection = selections.models.get(model_id)
            if selection is not None and source_file.installed_path not in selection.include:
                raise ValueError("component source installed path is outside model selection")
        parsed.setdefault(model_id, []).append(
            ReleaseComponentSource(
                repository=repository,
                revision=revision,
                relationship=cast(Literal["copied", "derived"], relationship),
                license_id="",
                license_url="",
                requires_terms_acceptance=False,
                files=files,
            )
        )
    return {model_id: tuple(sources) for model_id, sources in parsed.items()}


def _parse_component_source_files(raw: object) -> tuple[ReleaseComponentSourceFile, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("component source files must be a non-empty array")
    files: list[ReleaseComponentSourceFile] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("component source contains an invalid file row")
        _require_exact_keys(
            item,
            {"installed_path", "source_path", "source_sha256", "source_size_bytes"},
            set(),
            "component source file row",
        )
        installed_path = item["installed_path"]
        source_path = item["source_path"]
        source_sha256 = item["source_sha256"]
        source_size_bytes = item["source_size_bytes"]
        if not isinstance(installed_path, str) or not isinstance(source_path, str):
            raise ValueError("component source file paths must be strings")
        validate_exact_path(installed_path, "component installed path")
        validate_exact_path(source_path, "component source path")
        if not isinstance(source_sha256, str) or not SHA256_RE.fullmatch(source_sha256):
            raise ValueError("component source file SHA-256 is invalid")
        if (
            isinstance(source_size_bytes, bool)
            or not isinstance(source_size_bytes, int)
            or source_size_bytes < 0
        ):
            raise ValueError("component source file size is invalid")
        files.append(
            ReleaseComponentSourceFile(
                installed_path=installed_path,
                source_path=source_path,
                source_sha256=source_sha256,
                source_size_bytes=source_size_bytes,
            )
        )
    installed = [item.installed_path for item in files]
    if installed != sorted(installed) or len(installed) != len(set(installed)):
        raise ValueError("component source files must have unique sorted installed paths")
    source_paths = [item.source_path for item in files]
    if len(source_paths) != len(set(source_paths)):
        raise ValueError("component source files must have unique source paths")
    return tuple(files)


def _validate_license_rows(rows: dict[str, dict[str, Any]]) -> None:
    for row in rows.values():
        _require_exact_keys(
            row,
            {
                "repository",
                "license_id",
                "license_url",
                "requires_terms_acceptance",
                "verified_as_of",
            },
            set(),
            "license inventory row",
        )
        if not isinstance(row["license_id"], str) or not LICENSE_ID_RE.fullmatch(
            row["license_id"]
        ):
            raise ValueError("license inventory contains an invalid license ID")
        license_url = row["license_url"]
        if not isinstance(license_url, str):
            raise ValueError("license inventory contains an invalid license URL")
        parsed_url = urlsplit(license_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError("license inventory contains an invalid license URL")
        if not isinstance(row["requires_terms_acceptance"], bool):
            raise ValueError("license inventory terms flag must be boolean")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _index_rows(raw: object, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError(f"{label} rows must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            raise ValueError(f"{label} contains an invalid row")
        identifier = cast(str, item[key])
        if identifier in result:
            raise ValueError(f"{label} repeats an identity")
        result[identifier] = cast(dict[str, Any], item)
    return result


def _require_exact_keys(
    raw: Mapping[str, object],
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    keys = set(raw)
    if not required <= keys or keys - required - optional:
        raise ValueError(
            f"{label} must contain required {sorted(required)} and only optional {sorted(optional)}"
        )
