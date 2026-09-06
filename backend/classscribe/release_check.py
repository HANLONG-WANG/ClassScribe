"""Deterministic, fail-closed release-readiness validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from classscribe.models import (
    LoadedManifestBundle,
    ModelRegistry,
    load_builtin_manifest_bundle,
    load_model_licenses,
    load_registry,
)

_EXPECTED_CLASSROOM = (
    "moss_structure",
    "language_specific_sentence_asr",
    "quality_gated_review",
    "time_aligned_consensus",
    "deterministic_terminology",
    "character_invariant_punctuation",
    "quality_gated_forced_alignment",
    "automatic_export",
)
_EXPECTED_IBUS = (
    "pipewire_capture",
    "streaming_vad",
    "resident_streaming_preedit",
    "sample_aligned_chunking",
    "optional_language_specific_confirmation",
    "ibus_commit_text",
)
_EXPECTED_FORBIDDEN = {
    "one_universal_model",
    "whole_long_transcript_voting",
    "unaligned_fixed_character_overlap_deletion",
    "forced_alignment_without_quality_gate",
    "runtime_implicit_network_download",
}
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[^\s/]+/[^\s/]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BUNDLE_RELATIVE = Path("config/model-manifests/v1/bundle.v1.json")
_SELECTION_RELATIVE = Path("config/model-file-selection.v1.yaml")
_WAIVER_RELATIVE = Path("release/release-waivers.v1.json")
_SELECTION_KINDS = frozenset({"model", "tokenizer", "config", "remote_code", "other"})
_WAIVABLE_RELEASE_GATES = (
    "source_license",
    "phase12_acceptance",
    "desktop_matrix",
)
_WAIVER_ACKNOWLEDGEMENTS = {
    "does_not_claim_desktop_compatibility": True,
    "does_not_claim_phase12_acceptance": True,
    "does_not_grant_redistribution_rights": True,
    "raw_checks_remain_failed": True,
}
_REQUIRED_BUNDLE_RELEASE_ARTIFACTS = frozenset(
    {
        "protocol/schema/v1/model-manifest-bundle.schema.json",
        "config/schema/model-file-selection.v1.schema.json",
        "config/model-file-selection.v1.yaml",
        _BUNDLE_RELATIVE.as_posix(),
        "tools/model-manifest/pyproject.toml",
        "tools/model-manifest/uv.lock",
        "backend/classscribe/models/manifests.py",
        "tools/model-manifest/classscribe_manifest_tool/verifier.py",
    }
)


@dataclass(frozen=True, slots=True)
class ReleaseCheckResult:
    status: str
    checks: dict[str, bool]
    effective_checks: dict[str, bool]
    waived_gates: tuple[str, ...]
    reasons: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    waiver_errors: tuple[str, ...]
    details: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": self.checks,
            "effective_checks": self.effective_checks,
            "waived_gates": list(self.waived_gates),
            "reasons": list(self.reasons),
            "blocking_reasons": list(self.blocking_reasons),
            "waiver_errors": list(self.waiver_errors),
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class _ReleaseWaiver:
    waiver_id: str
    authorized_on: str
    gates: tuple[str, ...]
    sha256: str


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects shadowed release configuration keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate key",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def validate_release(root: Path) -> ReleaseCheckResult:
    """Validate a source release tree without network access or external mutation."""

    repository = root.resolve(strict=True)
    manifest = _json_object(repository / "release/release-manifest.v1.json")
    registry = load_registry(repository / "config/model-registry.v1.yaml")
    missing = _missing_artifacts(repository, manifest)
    bundle_errors = _model_manifest_bundle_errors(repository, registry, manifest)
    revision_lock = _json_object(repository / "config/model-revisions.lock.json")
    revision_errors = validate_revision_lock(registry, revision_lock)
    lock_errors = (
        *_dependency_lock_errors(repository, registry),
        *_release_dependency_lock_errors(repository, manifest),
    )
    architecture_errors = _architecture_errors(repository)
    license_inventory = _json_object(repository / "LICENSES/inventory.v1.json")
    license_errors = _license_errors(license_inventory)
    model_license_errors = _model_license_errors(
        repository / "config/model-licenses.v1.json",
        registry,
        revision_lock,
        license_inventory,
    )
    runtime_errors = _production_runtime_errors(repository / "backend/classscribe/api/runtime.py")
    phase12 = _json_object(repository / "benchmarks/results/phase12-acceptance.json")
    desktop = _json_object(repository / "benchmarks/results/ibus-desktop-compatibility.json")
    checks = {
        "artifact_inventory": not missing,
        "model_revision_lock": not revision_errors,
        "model_license_inventory": not model_license_errors,
        "model_manifest_bundle": not bundle_errors,
        "dependency_lock_hashes": not lock_errors,
        "final_architecture": not architecture_errors,
        "production_runtime_binding": not runtime_errors,
        "source_license": not license_errors,
        "phase12_acceptance": phase12.get("status") == "passed",
        "desktop_matrix": desktop.get("status") == "passed",
    }
    reasons = [*(f"missing artifact: {item}" for item in missing)]
    reasons.extend(revision_errors)
    reasons.extend(lock_errors)
    reasons.extend(architecture_errors)
    reasons.extend(model_license_errors)
    reasons.extend(bundle_errors)
    reasons.extend(runtime_errors)
    reasons.extend(license_errors)
    if not checks["phase12_acceptance"]:
        reasons.append("Phase 12 real-model acceptance is not passed")
    if not checks["desktop_matrix"]:
        reasons.append("IBus desktop compatibility matrix is not passed")
    return _release_result(
        repository,
        manifest,
        checks=checks,
        reasons=tuple(reasons),
        details={
            "version": manifest.get("version"),
            "facts_cutoff": manifest.get("facts_cutoff"),
            "registry_revision": registry.registry_revision,
            "model_count": len(registry.models),
            "phase12_status": phase12.get("status"),
            "desktop_status": desktop.get("status"),
        },
    )


def validate_installed_release(prefix: Path) -> ReleaseCheckResult:
    """Validate the immutable files of an installed or relocated Fedora package."""

    installed = prefix.resolve(strict=True)
    share = installed / "usr/share/classscribe"
    library = installed / "usr/lib/classscribe"
    manifest = _json_object(share / "release/release-manifest.v1.json")
    registry = load_registry(share / "config/model-registry.v1.yaml")
    required = (
        "usr/lib/classscribe/classscribe/release_check.py",
        "usr/lib/classscribe/classscribe/api/app.py",
        "usr/lib/classscribe/classscribe/classroom/pipeline.py",
        "usr/lib/classscribe/classscribe/classroom/production.py",
        "usr/lib/classscribe/classscribe/models/resident.py",
        "usr/lib/classscribe/classscribe_protocol/messages.py",
        "usr/lib/classscribe/classscribe_ibus_engine/controller.py",
        "usr/lib/classscribe/classscribe_dictationd/daemon.py",
        "usr/lib/classscribe/classscribe_hotkey_portal/main.py",
        "usr/lib/classscribe/scripts/run_benchmark.py",
        "usr/lib/classscribe/scripts/validate_release.py",
        "usr/bin/classscribe-benchmark",
        "usr/bin/classscribe-release-check",
        "usr/bin/classscribe-doctor",
        "usr/libexec/classscribe-core",
        "usr/libexec/classscribe-dictationd",
        "usr/libexec/classscribe-hotkey",
        "usr/libexec/ibus-engine-classscribe",
        "usr/libexec/classscribe-launcher",
        "usr/lib/systemd/user/classscribe-core.service",
        "usr/lib/systemd/user/classscribe-dictationd.service",
        "usr/lib/systemd/user/classscribe-hotkey.service",
        "usr/share/applications/classscribe.desktop",
        "usr/share/icons/hicolor/scalable/apps/classscribe.svg",
        "usr/share/ibus/component/classscribe.xml",
        "usr/share/classscribe/config/model-registry.v1.yaml",
        "usr/share/classscribe/config/model-revisions.lock.json",
        "usr/share/classscribe/config/model-licenses.v1.json",
        "usr/share/classscribe/config/schema/model-file-selection.v1.schema.json",
        "usr/share/classscribe/config/model-file-selection.v1.yaml",
        "usr/share/classscribe/config/model-manifests/v1/bundle.v1.json",
        "usr/share/classscribe/config/final-architecture.v1.json",
        "usr/share/classscribe/protocol/python/classscribe_protocol/messages.py",
        "usr/share/classscribe/protocol/python/classscribe_protocol/resident_workers.py",
        "usr/share/classscribe/protocol-schema/v1/resident-workers.schema.json",
        "usr/share/classscribe/protocol-schema/v1/model-manifest-bundle.schema.json",
        "usr/lib/classscribe/classscribe/models/manifests.py",
        "usr/share/classscribe/workers/qwen/worker.py",
        "usr/share/classscribe/frontend/dist/index.html",
        "usr/share/classscribe/benchmarks/results/phase12-acceptance.json",
        "usr/share/classscribe/release/release-readiness.json",
        "usr/share/classscribe/release/release-waivers.v1.json",
        "usr/share/classscribe/tests/audio_fixtures/regression-audio.v1.json",
        "usr/share/doc/classscribe/docs/user-guide.md",
        "usr/share/doc/classscribe/docs/openapi.md",
        "usr/share/licenses/classscribe/README.md",
        "usr/share/licenses/classscribe/inventory.v1.json",
    )
    missing = tuple(
        relative
        for relative in required
        if (installed / relative).is_symlink() or not (installed / relative).is_file()
    )
    revision_lock = _json_object(share / "config/model-revisions.lock.json")
    bundle_errors = _model_manifest_bundle_errors(share, registry, manifest)
    revision_errors = validate_revision_lock(registry, revision_lock)
    lock_errors = _dependency_lock_errors(share, registry)
    architecture_errors = _installed_architecture_errors(library, share)
    license_inventory = _json_object(installed / "usr/share/licenses/classscribe/inventory.v1.json")
    license_errors = _license_errors(license_inventory)
    model_license_errors = _model_license_errors(
        share / "config/model-licenses.v1.json",
        registry,
        revision_lock,
        license_inventory,
    )
    runtime_errors = _production_runtime_errors(library / "classscribe/api/runtime.py")
    phase12 = _json_object(share / "benchmarks/results/phase12-acceptance.json")
    desktop = _json_object(share / "benchmarks/results/ibus-desktop-compatibility.json")
    checks = {
        "artifact_inventory": not missing,
        "model_revision_lock": not revision_errors,
        "model_license_inventory": not model_license_errors,
        "model_manifest_bundle": not bundle_errors,
        "dependency_lock_hashes": not lock_errors,
        "final_architecture": not architecture_errors,
        "production_runtime_binding": not runtime_errors,
        "source_license": not license_errors,
        "phase12_acceptance": phase12.get("status") == "passed",
        "desktop_matrix": desktop.get("status") == "passed",
    }
    reasons = [*(f"missing installed artifact: {item}" for item in missing)]
    reasons.extend(revision_errors)
    reasons.extend(lock_errors)
    reasons.extend(architecture_errors)
    reasons.extend(model_license_errors)
    reasons.extend(bundle_errors)
    reasons.extend(runtime_errors)
    reasons.extend(license_errors)
    if not checks["phase12_acceptance"]:
        reasons.append("Phase 12 real-model acceptance is not passed")
    if not checks["desktop_matrix"]:
        reasons.append("IBus desktop compatibility matrix is not passed")
    return _release_result(
        share,
        manifest,
        checks=checks,
        reasons=tuple(reasons),
        details={
            "layout": "installed",
            "version": manifest.get("version"),
            "facts_cutoff": manifest.get("facts_cutoff"),
            "registry_revision": registry.registry_revision,
            "model_count": len(registry.models),
            "phase12_status": phase12.get("status"),
            "desktop_status": desktop.get("status"),
        },
    )


def _release_result(
    root: Path,
    manifest: dict[str, Any],
    *,
    checks: dict[str, bool],
    reasons: tuple[str, ...],
    details: dict[str, object],
) -> ReleaseCheckResult:
    waiver, waiver_errors = _load_release_waiver(root, manifest, tuple(checks))
    waived_gates = waiver.gates if waiver is not None else ()
    effective_checks = {
        gate: passed or gate in waived_gates for gate, passed in checks.items()
    }
    unresolved = tuple(
        f"release gate remains blocked: {gate}"
        for gate, passed in effective_checks.items()
        if not passed
    )
    blocking_reasons = (*waiver_errors, *unresolved)
    if blocking_reasons:
        status = "blocked"
    elif all(checks.values()):
        status = "ready"
    else:
        status = "ready_with_waivers"
    waiver_details: dict[str, object] | None = None
    if waiver is not None:
        waiver_details = {
            "authorized_on": waiver.authorized_on,
            "integrity": "valid",
            "scope": "automated_release_blocking_only",
            "sha256": waiver.sha256,
            "waiver_id": waiver.waiver_id,
        }
    elif manifest.get("release_policy") == "fail_closed_with_explicit_waivers":
        waiver_details = {"integrity": "invalid"}
    return ReleaseCheckResult(
        status=status,
        checks=checks,
        effective_checks=effective_checks,
        waived_gates=waived_gates,
        reasons=reasons,
        blocking_reasons=blocking_reasons,
        waiver_errors=waiver_errors,
        details={
            **details,
            "release_policy": manifest.get("release_policy"),
            "waiver": waiver_details,
            "waivers_do_not_change_raw_evidence": True,
        },
    )


def _load_release_waiver(
    root: Path,
    manifest: dict[str, Any],
    release_gates: tuple[str, ...],
) -> tuple[_ReleaseWaiver | None, tuple[str, ...]]:
    errors: list[str] = []
    if manifest.get("release_gates") != list(release_gates):
        errors.append("release manifest gates differ from checker gates")
    policy = manifest.get("release_policy")
    waiver_reference = manifest.get("release_waiver")
    if policy == "fail_closed":
        if waiver_reference is not None:
            errors.append("fail-closed release policy must not reference a waiver")
        return None, tuple(errors)
    if policy != "fail_closed_with_explicit_waivers":
        errors.append("release policy is invalid")
        return None, tuple(errors)
    if not isinstance(waiver_reference, dict) or set(waiver_reference) != {
        "path",
        "sha256",
    }:
        errors.append("release waiver reference is invalid")
        return None, tuple(errors)
    raw_path = waiver_reference.get("path")
    expected_sha = waiver_reference.get("sha256")
    if raw_path != _WAIVER_RELATIVE.as_posix():
        errors.append("release waiver path is invalid")
    if not isinstance(expected_sha, str) or _SHA256_RE.fullmatch(expected_sha) is None:
        errors.append("release waiver SHA-256 is invalid")
    artifact_groups = manifest.get("required_artifacts")
    inventoried = (
        isinstance(artifact_groups, dict)
        and any(
            isinstance(group, list) and _WAIVER_RELATIVE.as_posix() in group
            for group in artifact_groups.values()
        )
    )
    if not inventoried:
        errors.append("release manifest omits waiver artifact")
    if errors:
        return None, tuple(errors)

    path = root / _WAIVER_RELATIVE
    if (
        path.is_symlink()
        or not path.is_file()
        or not path.resolve().is_relative_to(root.resolve())
    ):
        return None, ("release waiver is missing or unsafe",)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha:
        return None, ("release waiver SHA-256 differs from release manifest",)
    try:
        value = _strict_canonical_json_object(raw)
    except (UnicodeError, ValueError) as error:
        return None, (f"release waiver is invalid: {error}",)
    waiver_errors = _release_waiver_contract_errors(value, manifest)
    if waiver_errors:
        return None, waiver_errors
    authorization = cast(dict[str, object], value["authorization"])
    return (
        _ReleaseWaiver(
            waiver_id=cast(str, value["waiver_id"]),
            authorized_on=cast(str, authorization["authorized_on"]),
            gates=tuple(cast(list[str], value["waived_gates"])),
            sha256=digest,
        ),
        (),
    )


def _release_waiver_contract_errors(
    value: dict[str, Any], manifest: dict[str, Any]
) -> tuple[str, ...]:
    if set(value) != {
        "acknowledgements",
        "authorization",
        "release",
        "schema_version",
        "waived_gates",
        "waiver_id",
    }:
        return ("release waiver fields are invalid",)
    errors: list[str] = []
    if value.get("schema_version") != 1 or isinstance(value.get("schema_version"), bool):
        errors.append("release waiver schema_version is not 1")
    release = value.get("release")
    expected_release = {
        "facts_cutoff": manifest.get("facts_cutoff"),
        "registry_revision": manifest.get("registry_revision"),
        "version": manifest.get("version"),
    }
    if not isinstance(release, dict) or release != expected_release:
        errors.append("release waiver release identity differs from release manifest")
    authorization = value.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {
        "authorized_on",
        "basis",
        "scope",
    }:
        errors.append("release waiver authorization is invalid")
        authorized_on = None
    else:
        authorized_on = authorization.get("authorized_on")
        if (
            not isinstance(authorized_on, str)
            or _DATE_RE.fullmatch(authorized_on) is None
            or authorization.get("basis") != "explicit_release_owner_instruction"
            or authorization.get("scope") != "automated_release_blocking_only"
        ):
            errors.append("release waiver authorization is invalid")
    waiver_id = value.get("waiver_id")
    version = manifest.get("version")
    expected_id = (
        f"classscribe-{version}-release-blocking-{authorized_on}"
        if isinstance(version, str) and isinstance(authorized_on, str)
        else None
    )
    if waiver_id != expected_id:
        errors.append("release waiver ID is invalid")
    if value.get("waived_gates") != list(_WAIVABLE_RELEASE_GATES):
        errors.append("release waiver gates exceed or differ from the authorized scope")
    if value.get("acknowledgements") != _WAIVER_ACKNOWLEDGEMENTS:
        errors.append("release waiver acknowledgements are incomplete")
    return tuple(errors)


def _strict_canonical_json_object(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8")

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=reject_duplicate_pairs)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    canonical = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if raw != canonical:
        raise ValueError("JSON bytes are not canonical")
    return value


def validate_revision_lock(registry: ModelRegistry, lock: dict[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    allowed_keys = {
        "schema_version",
        "registry_revision",
        "facts_as_of",
        "models",
        "component_sources",
    }
    required_keys = allowed_keys - {"component_sources"}
    if not required_keys <= set(lock) or set(lock) - allowed_keys:
        errors.append("model revision lock fields are invalid")
    if lock.get("schema_version") != 1:
        errors.append("model revision lock schema_version is not 1")
    if lock.get("registry_revision") != registry.registry_revision:
        errors.append("model revision lock registry_revision differs from registry")
    if lock.get("facts_as_of") != registry.facts_as_of:
        errors.append("model revision lock facts_as_of differs from registry")
    raw_models = lock.get("models")
    if not isinstance(raw_models, list):
        return (*errors, "model revision lock models must be an array")
    actual: dict[str, tuple[str, str, str, bool]] = {}
    for item in raw_models:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            errors.append("model revision lock contains an invalid row")
            continue
        if set(item) != {"id", "repository", "revision", "worker", "enabled"}:
            errors.append("model revision lock model row fields are invalid")
        identifier = str(item["id"])
        if identifier in actual:
            errors.append(f"model revision lock repeats {identifier}")
            continue
        actual[identifier] = (
            str(item.get("repository", "")),
            str(item.get("revision", "")),
            str(item.get("worker", "")),
            item.get("enabled") is True,
        )
    expected = {
        item.id: (item.repository, item.revision, item.worker, item.enabled)
        for item in registry.models
    }
    if actual.keys() != expected.keys():
        errors.append("model revision lock IDs differ from registry")
    for identifier in sorted(actual.keys() & expected.keys()):
        if actual[identifier] != expected[identifier]:
            errors.append(f"model revision lock differs for {identifier}")
    errors.extend(_component_source_revision_errors(registry, lock))
    return tuple(errors)


def _component_source_revision_errors(
    registry: ModelRegistry, lock: dict[str, Any]
) -> tuple[str, ...]:
    raw_sources = lock.get("component_sources", [])
    if not isinstance(raw_sources, list):
        return ("model revision lock component_sources must be an array",)
    errors: list[str] = []
    registry_rows = {model.id: model for model in registry.models}
    identities: list[tuple[str, str]] = []
    installed_by_model: dict[str, set[str]] = {}
    for source in raw_sources:
        if not isinstance(source, dict):
            errors.append("model revision lock contains an invalid component source")
            continue
        if set(source) != {"model_id", "repository", "revision", "relationship", "files"}:
            errors.append("model revision lock component source fields are invalid")
            continue
        model_id = source.get("model_id")
        repository = source.get("repository")
        revision = source.get("revision")
        relationship = source.get("relationship")
        if not isinstance(model_id, str) or model_id not in registry_rows:
            errors.append("model revision lock component source model is unknown")
            continue
        if not isinstance(repository, str) or not _REPOSITORY_RE.fullmatch(repository):
            errors.append("model revision lock component source repository is invalid")
            continue
        identity = (model_id, repository)
        identities.append(identity)
        if repository == registry_rows[model_id].repository:
            errors.append("model revision lock component source repeats payload repository")
        if not isinstance(revision, str) or not _COMMIT_RE.fullmatch(revision):
            errors.append("model revision lock component source revision is invalid")
        if relationship not in {"copied", "derived"}:
            errors.append("model revision lock component source relationship is invalid")
        files = source.get("files")
        if not isinstance(files, list) or not files:
            errors.append("model revision lock component source files are invalid")
            continue
        installed_paths: list[str] = []
        source_paths: list[str] = []
        for item in files:
            if not isinstance(item, dict) or set(item) != {
                "installed_path",
                "source_path",
                "source_sha256",
                "source_size_bytes",
            }:
                errors.append("model revision lock component source file fields are invalid")
                continue
            installed_path = item.get("installed_path")
            source_path = item.get("source_path")
            if not _is_safe_component_path(installed_path) or not _is_safe_component_path(
                source_path
            ):
                errors.append("model revision lock component source file path is invalid")
                continue
            assert isinstance(installed_path, str)
            assert isinstance(source_path, str)
            installed_paths.append(installed_path)
            source_paths.append(source_path)
            source_sha = item.get("source_sha256")
            source_size = item.get("source_size_bytes")
            if not isinstance(source_sha, str) or not _SHA256_RE.fullmatch(source_sha):
                errors.append("model revision lock component source file SHA is invalid")
            if isinstance(source_size, bool) or not isinstance(source_size, int) or source_size < 0:
                errors.append("model revision lock component source file size is invalid")
        if installed_paths != sorted(installed_paths) or len(installed_paths) != len(
            set(installed_paths)
        ):
            errors.append("model revision lock component source files are not unique and sorted")
        if len(source_paths) != len(set(source_paths)):
            errors.append("model revision lock component source paths are not unique")
        claimed = installed_by_model.setdefault(model_id, set())
        if claimed & set(installed_paths):
            errors.append("model revision lock component sources overlap installed paths")
        claimed.update(installed_paths)
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        errors.append("model revision lock component sources are not unique and sorted")
    return tuple(errors)


def _is_safe_component_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    relative = PurePosixPath(value)
    return bool(
        value
        and len(value) <= 1024
        and not relative.is_absolute()
        and ".." not in relative.parts
        and value == relative.as_posix()
        and "\\" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and value != "supply-chain.json"
    )


def _missing_artifacts(root: Path, manifest: dict[str, Any]) -> tuple[str, ...]:
    groups = manifest.get("required_artifacts")
    if not isinstance(groups, dict):
        return ("release manifest required_artifacts",)
    missing: list[str] = []
    for values in groups.values():
        if not isinstance(values, list):
            missing.append("release manifest artifact group is not an array")
            continue
        for raw in values:
            relative = Path(str(raw))
            if relative.is_absolute() or ".." in relative.parts:
                missing.append(f"unsafe manifest path {raw}")
                continue
            path = root.joinpath(*relative.parts)
            if path.is_symlink() or not path.exists() or not path.resolve().is_relative_to(root):
                missing.append(relative.as_posix())
    return tuple(sorted(missing))


def _model_manifest_bundle_errors(
    root: Path, registry: ModelRegistry, release_manifest: dict[str, Any]
) -> tuple[str, ...]:
    try:
        licenses = load_model_licenses(root / "config/model-licenses.v1.json")
        bundle = load_builtin_manifest_bundle(root / _BUNDLE_RELATIVE, registry, licenses)
    except (OSError, KeyError, TypeError, ValueError) as error:
        return (f"model manifest bundle is invalid: {error}",)

    errors: list[str] = []
    expected_names = {"bundle.v1.json", *(entry.path for entry in bundle.index.manifests)}
    bundle_directory = root / _BUNDLE_RELATIVE.parent
    if bundle_directory.is_symlink() or not bundle_directory.is_dir():
        errors.append("model manifest bundle directory is missing or unsafe")
    else:
        actual_names = {entry.name for entry in bundle_directory.iterdir()}
        if actual_names != expected_names:
            errors.append("model manifest bundle directory contents differ from index")

    groups = release_manifest.get("required_artifacts")
    source_and_locks = groups.get("source_and_locks") if isinstance(groups, dict) else None
    expected_inventory = _REQUIRED_BUNDLE_RELEASE_ARTIFACTS | {
        f"config/model-manifests/v1/{entry.path}" for entry in bundle.index.manifests
    }
    if not isinstance(source_and_locks, list) or not expected_inventory <= set(
        item for item in source_and_locks if isinstance(item, str)
    ):
        errors.append("release manifest omits model bundle artifacts")

    raw_locks = release_manifest.get("dependency_lock_hashes")
    generator_lock = (
        raw_locks.get("tools/model-manifest/uv.lock") if isinstance(raw_locks, dict) else None
    )
    if bundle.index.generator.lock_sha256 != generator_lock:
        errors.append("model bundle generator lock differs from release manifest")
    errors.extend(_model_selection_errors(root, bundle))
    return tuple(errors)


def _model_selection_errors(
    root: Path, bundle: LoadedManifestBundle
) -> tuple[str, ...]:
    selection_path = root / _SELECTION_RELATIVE
    if selection_path.is_symlink() or not selection_path.is_file():
        return ("model file selection is missing or unsafe",)
    try:
        raw = yaml.load(selection_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError):
        return ("model file selection is invalid",)
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "models"}:
        return ("model file selection contract is invalid",)
    if raw.get("schema_version") != 1 or isinstance(raw.get("schema_version"), bool):
        return ("model file selection schema version is invalid",)
    rows = raw.get("models")
    if not isinstance(rows, dict):
        return ("model file selection rows are invalid",)
    model_ids = [entry.model_id for entry in bundle.index.manifests]
    if list(rows) != model_ids:
        return ("model file selection IDs differ from bundle",)

    errors: list[str] = []
    manifests = {manifest.model_id: manifest for manifest in bundle.manifests}
    for model_id in model_ids:
        row = rows.get(model_id)
        if not isinstance(row, dict) or set(row) != {"include", "kinds", "exclude"}:
            errors.append(f"model file selection row is invalid: {model_id}")
            continue
        include = row.get("include")
        exclude = row.get("exclude")
        kinds = row.get("kinds")
        if not _valid_selection_paths(include, require_nonempty=True) or not _valid_selection_paths(
            exclude, require_nonempty=False
        ):
            errors.append(f"model file selection paths are invalid: {model_id}")
            continue
        include_paths = cast(list[str], include)
        exclude_paths = cast(list[str], exclude)
        if not isinstance(kinds, dict) or set(kinds) != set(include_paths):
            errors.append(f"model file selection kinds are invalid: {model_id}")
            continue
        if not all(isinstance(kind, str) and kind in _SELECTION_KINDS for kind in kinds.values()):
            errors.append(f"model file selection kinds are invalid: {model_id}")
            continue
        selection_kinds = cast(dict[str, str], kinds)
        if set(include_paths) & set(exclude_paths):
            errors.append(f"model file selection include/exclude overlap: {model_id}")
            continue
        manifest = manifests[model_id]
        if tuple(include_paths) != tuple(item.path for item in manifest.files):
            errors.append(f"model file selection differs from manifest files: {model_id}")
        elif any(selection_kinds[item.path] != item.kind for item in manifest.files):
            errors.append(f"model file selection differs from manifest kinds: {model_id}")
    return tuple(errors)


def _valid_selection_paths(raw: object, *, require_nonempty: bool) -> bool:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return False
    paths = tuple(raw)
    if require_nonempty and not paths:
        return False
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        return False
    for path in paths:
        relative = PurePosixPath(path)
        if (
            not path
            or len(path) > 1024
            or relative.is_absolute()
            or ".." in relative.parts
            or "." in relative.parts
            or relative.as_posix() != path
            or "\\" in path
            or any(character in path for character in "*?[]")
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
            or path == "supply-chain.json"
        ):
            return False
    return True


def _dependency_lock_errors(root: Path, registry: ModelRegistry) -> tuple[str, ...]:
    errors: list[str] = []
    checked: set[str] = set()
    for model in registry.models:
        if model.dependency_lock in checked:
            continue
        checked.add(model.dependency_lock)
        lock = root / model.dependency_lock
        if lock.is_symlink() or not lock.is_file():
            errors.append(f"dependency lock is missing or unsafe: {model.dependency_lock}")
            continue
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        matching = {
            item.dependency_lock_sha256
            for item in registry.models
            if item.dependency_lock == model.dependency_lock
        }
        if len(matching) != 1 or digest not in matching:
            errors.append(f"dependency lock hash differs: {model.dependency_lock}")
    return tuple(errors)


def _release_dependency_lock_errors(
    root: Path, manifest: dict[str, Any]
) -> tuple[str, ...]:
    raw_locks = manifest.get("dependency_lock_hashes")
    if not isinstance(raw_locks, dict):
        return ("release manifest dependency_lock_hashes must be an object",)
    errors: list[str] = []
    for raw_path, expected_digest in sorted(raw_locks.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_path, str):
            errors.append("release dependency lock path must be a string")
            continue
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != raw_path:
            errors.append(f"unsafe release dependency lock path: {raw_path}")
            continue
        if not isinstance(expected_digest, str) or len(expected_digest) != 64 or any(
            character not in "0123456789abcdef" for character in expected_digest
        ):
            errors.append(f"invalid release dependency lock hash: {raw_path}")
            continue
        lock = root.joinpath(*relative.parts)
        if lock.is_symlink() or not lock.is_file() or not lock.resolve().is_relative_to(root):
            errors.append(f"release dependency lock is missing or unsafe: {raw_path}")
            continue
        if hashlib.sha256(lock.read_bytes()).hexdigest() != expected_digest:
            errors.append(f"release dependency lock hash differs: {raw_path}")
    if "tools/model-manifest/uv.lock" not in raw_locks:
        errors.append("manifest tool dependency lock is not pinned")
    return tuple(errors)


def _architecture_errors(root: Path) -> tuple[str, ...]:
    value = _json_object(root / "config/final-architecture.v1.json")
    errors = list(_architecture_manifest_errors(value))
    evidence = {
        "moss_structure": root / "backend/classscribe/structure/pipeline.py",
        "language_specific_sentence_asr": root / "backend/classscribe/asr/pipeline.py",
        "quality_gated_review": root / "backend/classscribe/quality/pipeline.py",
        "time_aligned_consensus": root / "backend/classscribe/consensus/alignment.py",
        "deterministic_terminology": root / "backend/classscribe/terminology/correction.py",
        "character_invariant_punctuation": root / "backend/classscribe/punctuation/guard.py",
        "quality_gated_forced_alignment": root / "backend/classscribe/alignment/gate.py",
        "automatic_export": root / "backend/classscribe/exports/renderers.py",
        "pipewire_capture": root / "ibus/dictationd/classscribe_dictationd/runtime.py",
        "streaming_vad": root / "ibus/dictationd/classscribe_dictationd/daemon.py",
        "resident_streaming_preedit": root / "ibus/dictationd/classscribe_dictationd/streaming.py",
        "sample_aligned_chunking": root / "ibus/dictationd/classscribe_dictationd/streaming.py",
        "optional_language_specific_confirmation": root
        / "ibus/dictationd/classscribe_dictationd/session.py",
        "ibus_commit_text": root / "ibus/engine/classscribe_ibus_engine/controller.py",
    }
    errors.extend(
        f"architecture evidence is missing: {name}"
        for name, path in evidence.items()
        if path.is_symlink() or not path.is_file()
    )
    return tuple(errors)


def _installed_architecture_errors(library: Path, share: Path) -> tuple[str, ...]:
    value = _json_object(share / "config/final-architecture.v1.json")
    errors = list(_architecture_manifest_errors(value))
    evidence = (
        library / "classscribe/structure/pipeline.py",
        library / "classscribe/asr/pipeline.py",
        library / "classscribe/quality/pipeline.py",
        library / "classscribe/consensus/alignment.py",
        library / "classscribe/terminology/correction.py",
        library / "classscribe/punctuation/guard.py",
        library / "classscribe/alignment/gate.py",
        library / "classscribe/exports/renderers.py",
        library / "classscribe_dictationd/runtime.py",
        library / "classscribe_dictationd/daemon.py",
        library / "classscribe_dictationd/streaming.py",
        library / "classscribe_dictationd/session.py",
        library / "classscribe_ibus_engine/controller.py",
    )
    errors.extend(
        f"installed architecture evidence is missing: {path.name}"
        for path in evidence
        if path.is_symlink() or not path.is_file()
    )
    return tuple(errors)


def _architecture_manifest_errors(value: dict[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    if tuple(value.get("classroom", ())) != _EXPECTED_CLASSROOM:
        errors.append("final classroom architecture sequence drifted")
    if tuple(value.get("ibus", ())) != _EXPECTED_IBUS:
        errors.append("final IBus architecture sequence drifted")
    forbidden = value.get("forbidden")
    if not isinstance(forbidden, list) or set(forbidden) != _EXPECTED_FORBIDDEN:
        errors.append("final architecture forbidden strategies drifted")
    return tuple(errors)


def _license_errors(inventory: dict[str, Any]) -> tuple[str, ...]:
    source = inventory.get("source")
    if not isinstance(source, dict):
        return ("source license inventory is missing",)
    errors: list[str] = []
    if source.get("status") != "final":
        errors.append("ClassScribe source license is not finalized")
    if source.get("redistribution_authorized") is not True:
        errors.append("ClassScribe source redistribution is not authorized")
    assets = inventory.get("bundled_assets")
    if not isinstance(assets, list):
        errors.append("bundled asset license inventory is missing")
    elif any(isinstance(item, dict) and item.get("release_blocking") is True for item in assets):
        errors.append("one or more bundled assets have unresolved licensing")
    return tuple(errors)


def _model_license_errors(
    path: Path,
    registry: ModelRegistry,
    revision_lock: dict[str, Any],
    source_inventory: dict[str, Any],
) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        records = load_model_licenses(path)
        raw = _json_object(path)
    except (OSError, ValueError, TypeError) as exc:
        return (f"model license inventory is invalid: {exc}",)
    if raw.get("facts_as_of") != registry.facts_as_of:
        errors.append("model license inventory facts_as_of differs from registry")
    repositories = {item.repository for item in registry.models}
    raw_sources = revision_lock.get("component_sources", [])
    if isinstance(raw_sources, list):
        repositories.update(
            source["repository"]
            for source in raw_sources
            if isinstance(source, dict) and isinstance(source.get("repository"), str)
        )
    if records.keys() != repositories:
        errors.append(
            "model license inventory repositories differ from model and component sources"
        )
    model_weights = source_inventory.get("model_weights")
    licenses = model_weights.get("licenses") if isinstance(model_weights, dict) else None
    expected = {repository: item.license_id for repository, item in records.items()}
    if not isinstance(licenses, dict) or licenses != expected:
        errors.append("source inventory model license map differs from frozen disclosure")
    return tuple(errors)


def _production_runtime_errors(path: Path) -> tuple[str, ...]:
    if path.is_symlink() or not path.is_file():
        return ("production runtime composition is missing",)
    source = path.read_text(encoding="utf-8")
    required = ("ProductionStageRunner(", "ClassroomPipeline(", "pipeline=pipeline")
    if any(value not in source for value in required):
        return ("production classroom stage handlers are not bound",)
    return ()


def _json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSON file is missing or unsafe: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value
