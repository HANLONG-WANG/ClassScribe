"""Deterministic, fail-closed release-readiness validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from classscribe.models import ModelRegistry, load_model_licenses, load_registry

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


@dataclass(frozen=True, slots=True)
class ReleaseCheckResult:
    status: str
    checks: dict[str, bool]
    reasons: tuple[str, ...]
    details: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": self.checks,
            "reasons": list(self.reasons),
            "details": self.details,
        }


def validate_release(root: Path) -> ReleaseCheckResult:
    """Validate a source release tree without network access or external mutation."""

    repository = root.resolve(strict=True)
    manifest = _json_object(repository / "release/release-manifest.v1.json")
    registry = load_registry(repository / "config/model-registry.v1.yaml")
    missing = _missing_artifacts(repository, manifest)
    revision_errors = validate_revision_lock(
        registry, _json_object(repository / "config/model-revisions.lock.json")
    )
    lock_errors = _dependency_lock_errors(repository, registry)
    architecture_errors = _architecture_errors(repository)
    license_inventory = _json_object(repository / "LICENSES/inventory.v1.json")
    license_errors = _license_errors(license_inventory)
    model_license_errors = _model_license_errors(
        repository / "config/model-licenses.v1.json", registry, license_inventory
    )
    runtime_errors = _production_runtime_errors(repository / "backend/classscribe/api/runtime.py")
    phase12 = _json_object(repository / "benchmarks/results/phase12-acceptance.json")
    desktop = _json_object(repository / "benchmarks/results/ibus-desktop-compatibility.json")
    checks = {
        "artifact_inventory": not missing,
        "model_revision_lock": not revision_errors,
        "model_license_inventory": not model_license_errors,
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
    reasons.extend(runtime_errors)
    reasons.extend(license_errors)
    if not checks["phase12_acceptance"]:
        reasons.append("Phase 12 real-model acceptance is not passed")
    if not checks["desktop_matrix"]:
        reasons.append("IBus desktop compatibility matrix is not passed")
    return ReleaseCheckResult(
        status="ready" if all(checks.values()) else "blocked",
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
        "usr/share/classscribe/config/final-architecture.v1.json",
        "usr/share/classscribe/protocol/python/classscribe_protocol/messages.py",
        "usr/share/classscribe/protocol/python/classscribe_protocol/resident_workers.py",
        "usr/share/classscribe/protocol-schema/v1/resident-workers.schema.json",
        "usr/share/classscribe/workers/qwen/worker.py",
        "usr/share/classscribe/frontend/dist/index.html",
        "usr/share/classscribe/benchmarks/results/phase12-acceptance.json",
        "usr/share/classscribe/release/release-readiness.json",
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
    revision_errors = validate_revision_lock(
        registry, _json_object(share / "config/model-revisions.lock.json")
    )
    lock_errors = _dependency_lock_errors(share, registry)
    architecture_errors = _installed_architecture_errors(library, share)
    license_inventory = _json_object(installed / "usr/share/licenses/classscribe/inventory.v1.json")
    license_errors = _license_errors(license_inventory)
    model_license_errors = _model_license_errors(
        share / "config/model-licenses.v1.json", registry, license_inventory
    )
    runtime_errors = _production_runtime_errors(library / "classscribe/api/runtime.py")
    phase12 = _json_object(share / "benchmarks/results/phase12-acceptance.json")
    desktop = _json_object(share / "benchmarks/results/ibus-desktop-compatibility.json")
    checks = {
        "artifact_inventory": not missing,
        "model_revision_lock": not revision_errors,
        "model_license_inventory": not model_license_errors,
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
    reasons.extend(runtime_errors)
    reasons.extend(license_errors)
    if not checks["phase12_acceptance"]:
        reasons.append("Phase 12 real-model acceptance is not passed")
    if not checks["desktop_matrix"]:
        reasons.append("IBus desktop compatibility matrix is not passed")
    return ReleaseCheckResult(
        status="ready" if all(checks.values()) else "blocked",
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


def validate_revision_lock(registry: ModelRegistry, lock: dict[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
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
    return tuple(errors)


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
    path: Path, registry: ModelRegistry, source_inventory: dict[str, Any]
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
    if records.keys() != repositories:
        errors.append("model license inventory repositories differ from registry")
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
