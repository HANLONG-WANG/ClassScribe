"""Strict, completely offline model manifest bundle verification."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from classscribe_manifest_tool.inputs import (
    ReleaseComponentSource,
    load_release_inputs,
)
from classscribe_manifest_tool.selection import load_file_selection, load_yaml_mapping
from classscribe_manifest_tool.support import (
    load_worker_support,
    model_status,
    validate_worker_support,
)

TOKEN_RE = re.compile(r"(?i)\bhf_[a-z0-9]{8,}\b")
WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
LOCAL_MARKERS = ("/home/", "/root/", "/tmp/", "\\users\\", ".cache/huggingface")


class BundleVerificationError(RuntimeError):
    """A stable fail-closed verifier error without untrusted values."""


@dataclass(frozen=True, slots=True)
class BundleVerificationResult:
    bundle_sha256: str
    model_count: int
    manifest_sha256: Mapping[str, str]
    models: tuple[Mapping[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "verified",
            "bundle_sha256": self.bundle_sha256,
            "model_count": self.model_count,
            "manifest_sha256": dict(self.manifest_sha256),
            "models": [dict(model) for model in self.models],
        }


def verify_bundle(bundle_path: Path, *, root: Path | None = None) -> BundleVerificationResult:
    """Verify one source bundle and all frozen inputs without performing network access."""
    repository = _resolve_repository_root(bundle_path, root)
    expected_bundle = repository / "config/model-manifests/v1/bundle.v1.json"
    if bundle_path.absolute() != expected_bundle.absolute():
        raise BundleVerificationError("bundle must use the frozen config/model-manifests/v1 path")
    bundle_bytes = _read_regular_file(bundle_path, repository, "bundle")
    bundle = _load_json_object(bundle_bytes, "bundle")
    _require_canonical_json(bundle_bytes, bundle, "bundle")
    bundle_schema = _load_schema(
        repository / "protocol/schema/v1/model-manifest-bundle.schema.json", repository
    )
    manifest_schema = _load_schema(
        repository / "protocol/schema/v1/model-manifest.schema.json", repository
    )
    _validate_schema(bundle, bundle_schema, "bundle")
    _assert_no_sensitive_values(bundle, "bundle")

    registry = load_yaml_mapping(
        repository / "config/model-registry.v1.yaml", label="model registry"
    )
    registry_schema = _load_schema(
        repository / "config/schema/model-registry.v1.schema.json", repository
    )
    _validate_schema(registry, registry_schema, "model registry")
    registry_rows = _index_rows(registry.get("models"), "id", "model registry")
    registry_ids = set(registry_rows)
    try:
        support = load_worker_support(repository)
        validate_worker_support(
            support,
            {
                model_id: cast(str, row["worker"])
                for model_id, row in registry_rows.items()
            },
        )
    except ValueError as error:
        raise BundleVerificationError(str(error)) from None

    revisions = _read_json_path(
        repository / "config/model-revisions.lock.json", repository, "revision lock"
    )
    revision_rows = _index_rows(revisions.get("models"), "id", "revision lock")
    licenses = _read_json_path(
        repository / "config/model-licenses.v1.json", repository, "license inventory"
    )
    license_rows = _index_rows(licenses.get("models"), "repository", "license inventory")
    selection_path = repository / "config/model-file-selection.v1.yaml"
    selection_value = load_yaml_mapping(selection_path, label="model file selection")
    selection_schema = _load_schema(
        repository / "config/schema/model-file-selection.v1.schema.json", repository
    )
    _validate_schema(selection_value, selection_schema, "model file selection")
    selections = load_file_selection(selection_path)
    try:
        frozen_inputs = load_release_inputs(
            registry_path=repository / "config/model-registry.v1.yaml",
            revisions_path=repository / "config/model-revisions.lock.json",
            licenses_path=repository / "config/model-licenses.v1.json",
            selection_path=selection_path,
            require_complete_selection=True,
        )
    except ValueError as error:
        if str(error) == "worker dependency lock SHA differs from registry":
            raise BundleVerificationError(
                "worker lock SHA-256 differs from the model registry"
            ) from None
        raise BundleVerificationError(f"frozen release inputs are invalid: {error}") from None
    frozen_models = {model.model_id: model for model in frozen_inputs.models}

    _verify_inventory_headers(bundle, registry, revisions, licenses)
    entries = _bundle_entries(bundle)
    entry_ids = [cast(str, item["model_id"]) for item in entries]
    if entry_ids != sorted(entry_ids) or len(entry_ids) != len(set(entry_ids)):
        raise BundleVerificationError("bundle manifest entries must have unique sorted model IDs")
    if set(entry_ids) != registry_ids:
        raise BundleVerificationError("bundle member IDs do not exactly match the model registry")
    if set(revision_rows) != registry_ids:
        raise BundleVerificationError("revision lock IDs do not exactly match the model registry")
    if set(selections.models) != registry_ids:
        raise BundleVerificationError("selection IDs do not exactly match the model registry")
    expected_license_repositories = {
        model.repository for model in frozen_inputs.models
    } | {
        source.repository
        for model in frozen_inputs.models
        for source in model.component_sources
    }
    if set(license_rows) != expected_license_repositories:
        raise BundleVerificationError(
            "license repositories do not exactly match frozen model and component sources"
        )
    _verify_bundle_directory(bundle_path.parent, entries)
    _verify_generator_lock(repository, bundle)

    manifest_hashes: dict[str, str] = {}
    for entry in entries:
        model_id = cast(str, entry["model_id"])
        expected_name = f"{model_id}.json"
        if entry["path"] != expected_name:
            raise BundleVerificationError("bundle member path does not match its model ID")
        member_path = bundle_path.parent / expected_name
        content = _read_regular_file(member_path, repository, "manifest member")
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry["sha256"]:
            raise BundleVerificationError("bundle member SHA-256 differs from actual bytes")
        manifest = _load_json_object(content, "manifest member")
        _require_canonical_json(content, manifest, "manifest member")
        _validate_schema(manifest, manifest_schema, "manifest member")
        _assert_no_sensitive_values(manifest, "manifest member")
        _verify_manifest_cross_contracts(
            repository,
            model_id,
            manifest,
            registry_rows[model_id],
            revision_rows[model_id],
            license_rows,
            selections.models[model_id].include,
            selections.models[model_id].kinds,
            frozen_models[model_id].component_sources,
        )
        manifest_hashes[model_id] = digest
    models = tuple(
        MappingProxyType(
            model_status(
                repository,
                model_id=model_id,
                worker=cast(str, registry_rows[model_id]["worker"]),
                enabled=cast(bool, registry_rows[model_id]["enabled"]),
                experimental=cast(bool, registry_rows[model_id].get("experimental", False)),
                manifest_sha256=manifest_hashes[model_id],
                support=support,
            )
        )
        for model_id in sorted(registry_ids)
    )
    return BundleVerificationResult(
        bundle_sha256=hashlib.sha256(bundle_bytes).hexdigest(),
        model_count=len(entries),
        manifest_sha256=MappingProxyType(manifest_hashes),
        models=models,
    )


def _resolve_repository_root(bundle_path: Path, root: Path | None) -> Path:
    if root is not None:
        try:
            return root.resolve(strict=True)
        except OSError as error:
            raise BundleVerificationError("verification root is missing or unsafe") from error
    for candidate in bundle_path.absolute().parents:
        if (candidate / "config/model-registry.v1.yaml").is_file():
            return candidate.resolve(strict=True)
    raise BundleVerificationError("cannot locate repository root for bundle")


def _read_regular_file(path: Path, root: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BundleVerificationError(f"{label} is missing or is not a regular file")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BundleVerificationError(f"{label} is missing or unsafe") from error
    if not resolved.is_relative_to(root):
        raise BundleVerificationError(f"{label} escapes the verification root")
    return path.read_bytes()


def _load_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BundleVerificationError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise BundleVerificationError(f"{label} must contain a JSON object")
    return cast(dict[str, Any], value)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _require_canonical_json(content: bytes, value: object, label: str) -> None:
    if content != _canonical_json_bytes(value):
        raise BundleVerificationError(f"{label} bytes are not canonical JSON")


def _read_json_path(path: Path, root: Path, label: str) -> dict[str, Any]:
    return _load_json_object(_read_regular_file(path, root, label), label)


def _load_schema(path: Path, root: Path) -> dict[str, Any]:
    return _read_json_path(path, root, "JSON schema")


def _validate_schema(value: object, schema: dict[str, Any], label: str) -> None:
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path)
        suffix = f" at {location}" if location else ""
        raise BundleVerificationError(f"{label} failed schema validation{suffix}") from None


def _index_rows(raw: object, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        raise BundleVerificationError(f"{label} rows must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            raise BundleVerificationError(f"{label} contains an invalid row")
        identifier = cast(str, item[key])
        if identifier in result:
            raise BundleVerificationError(f"{label} contains duplicate identities")
        result[identifier] = cast(dict[str, Any], item)
    return result


def _bundle_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    raw = bundle.get("manifests")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise BundleVerificationError("bundle manifests must be an array of objects")
    return cast(list[dict[str, Any]], raw)


def _verify_inventory_headers(
    bundle: dict[str, Any],
    registry: dict[object, object],
    revisions: dict[str, Any],
    licenses: dict[str, Any],
) -> None:
    if revisions.get("schema_version") != 1 or licenses.get("schema_version") != 1:
        raise BundleVerificationError("frozen inventory schema versions must be 1")
    if not (
        bundle.get("registry_revision")
        == registry.get("registry_revision")
        == revisions.get("registry_revision")
    ):
        raise BundleVerificationError("bundle, registry, and revision revisions differ")
    if not (
        bundle.get("facts_as_of")
        == registry.get("facts_as_of")
        == revisions.get("facts_as_of")
        == licenses.get("facts_as_of")
    ):
        raise BundleVerificationError("bundle and frozen inventory fact dates differ")


def _verify_bundle_directory(directory: Path, entries: Sequence[dict[str, Any]]) -> None:
    expected = {"bundle.v1.json", *(cast(str, item["path"]) for item in entries)}
    actual: set[str] = set()
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise BundleVerificationError("bundle directory contains a non-regular entry")
        actual.add(path.name)
    if actual != expected:
        raise BundleVerificationError("bundle directory member set differs from its index")


def _verify_generator_lock(repository: Path, bundle: dict[str, Any]) -> None:
    generator = bundle.get("generator")
    if not isinstance(generator, dict):
        raise BundleVerificationError("bundle generator metadata is invalid")
    lock = repository / "tools/model-manifest/uv.lock"
    content = _read_regular_file(lock, repository, "manifest tool lock")
    _verify_uv_lock(content, "manifest tool lock")
    if hashlib.sha256(content).hexdigest() != generator.get("lock_sha256"):
        raise BundleVerificationError("bundle generator lock SHA-256 differs from actual bytes")


def _verify_manifest_cross_contracts(
    repository: Path,
    model_id: str,
    manifest: dict[str, Any],
    registry: dict[str, Any],
    revision: dict[str, Any],
    licenses: dict[str, dict[str, Any]],
    selected_paths: tuple[str, ...],
    selected_kinds: Mapping[str, str],
    component_sources: tuple[ReleaseComponentSource, ...],
) -> None:
    if manifest.get("model_id") != model_id:
        raise BundleVerificationError("manifest model ID differs from its bundle entry")
    for field in ("repository", "revision", "worker"):
        if manifest.get(field) != registry.get(field) or manifest.get(field) != revision.get(field):
            raise BundleVerificationError(f"manifest {field} differs from frozen inventories")
    if revision.get("enabled") != registry.get("enabled"):
        raise BundleVerificationError("revision enabled state differs from the model registry")
    if manifest.get("trust_remote_code") != registry.get("trust_remote_code"):
        raise BundleVerificationError("manifest trust policy differs from the model registry")
    license_row = licenses.get(cast(str, registry.get("repository")))
    if license_row is None:
        raise BundleVerificationError("manifest repository has no license inventory row")
    license_fields = ("license_id", "license_url", "requires_terms_acceptance")
    if any(manifest.get(field) != license_row.get(field) for field in license_fields):
        raise BundleVerificationError("manifest license fields differ from the license inventory")
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        raise BundleVerificationError("manifest environment is invalid")
    expected_environment = {
        "python": "3.12",
        "runtime_backend": registry.get("runtime_backend"),
        "dtype": registry.get("dtype"),
        "worker_lock": registry.get("dependency_lock"),
        "worker_lock_sha256": registry.get("dependency_lock_sha256"),
    }
    if any(environment.get(key) != value for key, value in expected_environment.items()):
        raise BundleVerificationError("manifest environment differs from registry or worker lock")
    worker_lock = repository / cast(str, registry["dependency_lock"])
    worker_lock_content = _read_regular_file(worker_lock, repository, "worker lock")
    _verify_uv_lock(worker_lock_content, "worker lock")
    if hashlib.sha256(worker_lock_content).hexdigest() != registry.get("dependency_lock_sha256"):
        raise BundleVerificationError("worker lock SHA-256 differs from the model registry")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not all(isinstance(item, dict) for item in raw_files):
        raise BundleVerificationError("manifest files are invalid")
    files = cast(list[dict[str, Any]], raw_files)
    paths = tuple(cast(str, item.get("path")) for item in files)
    if paths != selected_paths:
        raise BundleVerificationError("manifest files differ from the human selection")
    if any(item.get("kind") != selected_kinds.get(cast(str, item.get("path"))) for item in files):
        raise BundleVerificationError("manifest file kinds differ from the human selection")
    sizes = [item.get("size_bytes") for item in files]
    if not all(
        isinstance(size, int) and not isinstance(size, bool) and size >= 0 for size in sizes
    ):
        raise BundleVerificationError("manifest file sizes are invalid")
    total = sum(cast(int, size) for size in sizes)
    if manifest.get("installed_size_bytes") != total or manifest.get(
        "estimated_download_bytes"
    ) != total:
        raise BundleVerificationError("manifest aggregate sizes differ from its files")
    _verify_manifest_component_sources(manifest, files, component_sources)


def _verify_manifest_component_sources(
    manifest: dict[str, Any],
    files: list[dict[str, Any]],
    component_sources: tuple[ReleaseComponentSource, ...],
) -> None:
    expected = [
        {
            "repository": source.repository,
            "revision": source.revision,
            "relationship": source.relationship,
            "license_id": source.license_id,
            "license_url": source.license_url,
            "requires_terms_acceptance": source.requires_terms_acceptance,
            "files": [
                {
                    "installed_path": item.installed_path,
                    "source_path": item.source_path,
                    "source_sha256": item.source_sha256,
                    "source_size_bytes": item.source_size_bytes,
                }
                for item in source.files
            ],
        }
        for source in component_sources
    ]
    if component_sources:
        if manifest.get("component_sources") != expected:
            raise BundleVerificationError(
                "manifest component sources differ from frozen revision and license inputs"
            )
    elif "component_sources" in manifest:
        raise BundleVerificationError("manifest has unexpected component source provenance")
    files_by_path = {cast(str, item["path"]): item for item in files}
    for source in component_sources:
        if source.relationship != "copied":
            continue
        for source_file in source.files:
            installed = files_by_path[source_file.installed_path]
            if (
                installed.get("sha256") != source_file.source_sha256
                or installed.get("size_bytes") != source_file.source_size_bytes
            ):
                raise BundleVerificationError(
                    "copied manifest component differs from its fixed source"
                )


def _verify_uv_lock(content: bytes, label: str) -> None:
    try:
        value = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise BundleVerificationError(f"{label} is not valid UTF-8 TOML") from error
    if value.get("version") != 1:
        raise BundleVerificationError(f"{label} version is not supported")


def _assert_no_sensitive_values(value: object, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_sensitive_values(key, label)
            _assert_no_sensitive_values(item, label)
        return
    if isinstance(value, list):
        for item in value:
            _assert_no_sensitive_values(item, label)
        return
    if not isinstance(value, str):
        return
    lowered = value.lower()
    if (
        TOKEN_RE.search(value)
        or "authorization" in lowered
        or "bearer " in lowered
        or "hf_token" in lowered
        or value.startswith(("/", "~", "file://"))
        or WINDOWS_ABSOLUTE_RE.match(value)
        or any(marker in lowered for marker in LOCAL_MARKERS)
    ):
        raise BundleVerificationError(f"{label} contains a secret or local-machine value")
