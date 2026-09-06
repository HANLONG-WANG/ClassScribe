from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from classscribe.models import (
    ModelLicense,
    ModelRegistry,
    load_builtin_manifest_bundle,
    load_manifest_bundle,
    load_model_licenses,
    load_registry,
)
from classscribe.resources import resource_path

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "whisper_tiny_reference"


def _controlled_inputs() -> tuple[ModelRegistry, dict[str, Any], dict[str, ModelLicense]]:
    production_registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    model = production_registry.model(MODEL_ID)
    registry_value = production_registry.model_dump(mode="json")
    registry_value["models"] = [model.model_dump(mode="json")]
    registry_value["rankings"] = {"fixture": [MODEL_ID]}
    registry = ModelRegistry.model_validate(registry_value)

    production_revisions = json.loads(
        (ROOT / "config/model-revisions.lock.json").read_text(encoding="utf-8")
    )
    production_revisions["models"] = [
        item for item in production_revisions["models"] if item["id"] == MODEL_ID
    ]
    production_revisions["component_sources"] = [
        item
        for item in production_revisions.get("component_sources", [])
        if item["model_id"] == MODEL_ID
    ]
    if not production_revisions["component_sources"]:
        production_revisions.pop("component_sources")
    production_licenses = load_model_licenses(ROOT / "config/model-licenses.v1.json")
    licenses = {model.repository: production_licenses[model.repository]}
    return registry, production_revisions, licenses


def _write_controlled_bundle(tmp_path: Path) -> Path:
    bundle_directory = tmp_path / "config/model-manifests/v1"
    bundle_directory.mkdir(parents=True)
    manifest = (ROOT / f"config/model-manifests/v1/{MODEL_ID}.json").read_bytes()
    (bundle_directory / f"{MODEL_ID}.json").write_bytes(manifest)
    index = {
        "schema_version": 1,
        "registry_revision": 1,
        "facts_as_of": "2026-09-03",
        "generated_at": "2026-09-05T00:00:00Z",
        "generator": {
            "name": "classscribe-model-manifest",
            "version": "1",
            "lock_sha256": "0" * 64,
        },
        "manifests": [
            {
                "model_id": MODEL_ID,
                "path": f"{MODEL_ID}.json",
                "sha256": hashlib.sha256(manifest).hexdigest(),
            }
        ],
    }
    bundle_path = bundle_directory / "bundle.v1.json"
    bundle_path.write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    worker_lock = ROOT / "workers/moss_en/uv.lock"
    fixture_lock = tmp_path / "workers/moss_en/uv.lock"
    fixture_lock.parent.mkdir(parents=True)
    fixture_lock.write_bytes(worker_lock.read_bytes())
    return bundle_path


def _replace_registry_model(registry: ModelRegistry, **updates: object) -> ModelRegistry:
    value = registry.model_dump(mode="json")
    value["models"][0].update(updates)
    return ModelRegistry.model_validate(value)


def _add_component_source_fixture(
    bundle_path: Path,
    revisions: dict[str, Any],
    licenses: dict[str, ModelLicense],
) -> None:
    source_repository = "upstream/component"
    source_file = {
        "installed_path": "config.json",
        "source_path": "source-config.json",
        "source_sha256": "d" * 64,
        "source_size_bytes": 7,
    }
    revisions["component_sources"] = [
        {
            "model_id": MODEL_ID,
            "repository": source_repository,
            "revision": "c" * 40,
            "relationship": "derived",
            "files": [source_file],
        }
    ]
    licenses[source_repository] = ModelLicense.model_validate(
        {
            "repository": source_repository,
            "license_id": "CC-BY-4.0",
            "license_url": "https://huggingface.co/upstream/component",
            "requires_terms_acceptance": False,
            "verified_as_of": "2026-09-03",
        }
    )
    manifest_path = bundle_path.parent / f"{MODEL_ID}.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["component_sources"] = [
        {
            "repository": source_repository,
            "revision": "c" * 40,
            "relationship": "derived",
            "license_id": "CC-BY-4.0",
            "license_url": "https://huggingface.co/upstream/component",
            "requires_terms_acceptance": False,
            "files": [source_file],
        }
    ]
    content = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    manifest_path.write_bytes(content)
    bundle = json.loads(bundle_path.read_bytes())
    bundle["manifests"][0]["sha256"] = hashlib.sha256(content).hexdigest()
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def test_load_manifest_bundle_parses_a_controlled_complete_registry(tmp_path: Path) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()

    bundle = load_manifest_bundle(bundle_path, registry, revisions, licenses)

    assert bundle.index.registry_revision == registry.registry_revision
    assert [entry.model_id for entry in bundle.index.manifests] == [MODEL_ID]
    assert [manifest.model_id for manifest in bundle.manifests] == [MODEL_ID]
    assert bundle.manifests[0].revision == registry.model(MODEL_ID).revision


def test_load_builtin_manifest_bundle_uses_the_production_revision_lock() -> None:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    licenses = load_model_licenses(ROOT / "config/model-licenses.v1.json")

    bundle = load_builtin_manifest_bundle(
        ROOT / "config/model-manifests/v1/bundle.v1.json",
        registry,
        licenses,
    )

    assert len(bundle.index.manifests) == 20
    assert {item.model_id for item in bundle.manifests} == {
        item.id for item in registry.models
    }
    assert bundle.manifest("pyannote_community_1").component_sources


def test_load_manifest_bundle_parses_and_freezes_component_sources(tmp_path: Path) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()
    _add_component_source_fixture(bundle_path, revisions, licenses)

    bundle = load_manifest_bundle(bundle_path, registry, revisions, licenses)

    sources = bundle.manifest(MODEL_ID).component_sources
    assert len(sources) == 1
    assert sources[0].repository == "upstream/component"
    assert sources[0].relationship == "derived"
    assert sources[0].license_id == "CC-BY-4.0"
    assert sources[0].files[0].installed_path == "config.json"


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("revision", "e" * 40, "component sources differ"),
        ("relationship", "copied", "copied component file differs"),
        ("license_id", "MIT", "component sources differ"),
        ("requires_terms_acceptance", True, "component sources differ"),
    ],
)
def test_load_manifest_bundle_rejects_component_source_drift(
    tmp_path: Path, field: str, value: object, match: str
) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()
    _add_component_source_fixture(bundle_path, revisions, licenses)
    manifest_path = bundle_path.parent / f"{MODEL_ID}.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["component_sources"][0][field] = value
    content = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    manifest_path.write_bytes(content)
    bundle = json.loads(bundle_path.read_bytes())
    bundle["manifests"][0]["sha256"] = hashlib.sha256(content).hexdigest()
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=match):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)


def test_load_manifest_bundle_never_weakens_production_registry_coverage(
    tmp_path: Path,
) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    revisions = json.loads(
        (ROOT / "config/model-revisions.lock.json").read_text(encoding="utf-8")
    )
    licenses = load_model_licenses(ROOT / "config/model-licenses.v1.json")

    with pytest.raises(ValueError, match="bundle model IDs differ"):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)


def test_source_and_packaged_resource_roots_load_and_fail_identically(tmp_path: Path) -> None:
    source_root = tmp_path / "source-tree"
    source_bundle = _write_controlled_bundle(source_root)
    packaged_root = tmp_path / "rpm-buildroot/usr/share/classscribe"
    shutil.copytree(source_root / "config", packaged_root / "config")
    shutil.copytree(source_root / "workers", packaged_root / "workers")
    relative_bundle = "config/model-manifests/v1/bundle.v1.json"
    packaged_bundle = resource_path(
        relative_bundle, {"CLASSSCRIBE_RESOURCE_ROOT": str(packaged_root)}
    )
    assert resource_path(
        relative_bundle, {"CLASSSCRIBE_RESOURCE_ROOT": str(source_root)}
    ) == source_bundle
    registry, revisions, licenses = _controlled_inputs()

    source_loaded = load_manifest_bundle(source_bundle, registry, revisions, licenses)
    packaged_loaded = load_manifest_bundle(packaged_bundle, registry, revisions, licenses)

    assert source_loaded.index == packaged_loaded.index
    assert tuple(item.as_dict() for item in source_loaded.manifests) == tuple(
        item.as_dict() for item in packaged_loaded.manifests
    )
    errors: list[str] = []
    for bundle_path in (source_bundle, packaged_bundle):
        manifest_path = bundle_path.parent / f"{MODEL_ID}.json"
        manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
        with pytest.raises(ValueError) as raised:
            load_manifest_bundle(bundle_path, registry, revisions, licenses)
        errors.append(str(raised.value))
    assert errors == ["bundle manifest member SHA-256 differs from index"] * 2


def test_load_manifest_bundle_verifies_member_and_worker_lock_sha_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()
    manifest_path = bundle_path.parent / f"{MODEL_ID}.json"
    manifest_path.write_bytes(manifest_path.read_bytes().replace(b'"MIT"', b'"BSD"'))
    network_attempts = 0

    def network_forbidden(*args: object, **kwargs: object) -> None:
        nonlocal network_attempts
        del args, kwargs
        network_attempts += 1
        raise AssertionError("manifest loading attempted to create a network socket")

    monkeypatch.setattr(socket, "socket", network_forbidden)

    with pytest.raises(ValueError, match="member SHA-256 differs"):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)
    assert network_attempts == 0

    bundle_path = _write_controlled_bundle(tmp_path / "worker-drift")
    worker_lock = tmp_path / "worker-drift/workers/moss_en/uv.lock"
    worker_lock.write_bytes(worker_lock.read_bytes() + b"\n# drift\n")
    with pytest.raises(ValueError, match="worker dependency lock SHA-256 differs"):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)


def test_loaded_manifest_query_is_deeply_read_only(tmp_path: Path) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()
    bundle = load_manifest_bundle(bundle_path, registry, revisions, licenses)

    manifest = bundle.manifest(MODEL_ID)
    assert manifest is bundle.manifests[0]
    with pytest.raises(KeyError, match="unknown"):
        bundle.manifest("unknown")
    with pytest.raises(FrozenInstanceError):
        manifest.worker = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        manifest.environment["dtype"] = "float32"  # type: ignore[index]
    exported = manifest.as_dict()
    exported["worker"] = "changed"
    assert bundle.manifest(MODEL_ID).worker == "moss_en"


@pytest.mark.parametrize("target", ["bundle", "manifest", "worker_lock"])
def test_loader_rejects_symlinks_for_every_bundled_resource(
    tmp_path: Path, target: str
) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()
    targets = {
        "bundle": bundle_path,
        "manifest": bundle_path.parent / f"{MODEL_ID}.json",
        "worker_lock": tmp_path / "workers/moss_en/uv.lock",
    }
    selected = targets[target]
    outside = tmp_path / f"outside-{target}"
    selected.replace(outside)
    selected.symlink_to(outside)

    with pytest.raises(ValueError, match=r"safely opened|regular file"):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)


def test_loader_rejects_special_files_and_hardlinks(tmp_path: Path) -> None:
    registry, revisions, licenses = _controlled_inputs()
    fifo_root = tmp_path / "fifo"
    bundle_path = _write_controlled_bundle(fifo_root)
    manifest_path = bundle_path.parent / f"{MODEL_ID}.json"
    manifest_path.unlink()
    os.mkfifo(manifest_path)
    with pytest.raises(ValueError, match="regular file"):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)

    hardlink_root = tmp_path / "hardlink"
    bundle_path = _write_controlled_bundle(hardlink_root)
    manifest_path = bundle_path.parent / f"{MODEL_ID}.json"
    alias = bundle_path.parent / "manifest-alias"
    os.link(manifest_path, alias)
    with pytest.raises(ValueError, match="single-link regular file"):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)


@pytest.mark.parametrize(
    ("drift", "match"),
    [
        ("registry_revision", "bundle registry revision differs"),
        ("model_revision", "manifest identity differs"),
        ("revision_lock", "revision lock row differs"),
        ("license", "manifest license differs"),
        ("trust", "manifest identity differs"),
        ("worker", "manifest identity differs"),
        ("worker_lock", "manifest environment differs"),
    ],
)
def test_loader_fails_closed_for_every_frozen_contract_drift(
    tmp_path: Path, drift: str, match: str
) -> None:
    bundle_path = _write_controlled_bundle(tmp_path)
    registry, revisions, licenses = _controlled_inputs()
    if drift == "registry_revision":
        registry_value = registry.model_dump(mode="json")
        registry_value["registry_revision"] = 2
        registry = ModelRegistry.model_validate(registry_value)
        revisions["registry_revision"] = 2
    elif drift == "model_revision":
        registry = _replace_registry_model(registry, revision="0" * 40)
        revisions["models"][0]["revision"] = "0" * 40
    elif drift == "revision_lock":
        revisions["models"][0]["revision"] = "0" * 40
    elif drift == "license":
        record = next(iter(licenses.values()))
        changed = record.model_dump(mode="json")
        changed["license_id"] = "BSD-3-Clause"
        licenses = {record.repository: ModelLicense.model_validate(changed)}
    elif drift == "trust":
        registry = _replace_registry_model(registry, trust_remote_code=True)
    elif drift == "worker":
        registry = _replace_registry_model(registry, worker="changed_worker")
        revisions["models"][0]["worker"] = "changed_worker"
    elif drift == "worker_lock":
        registry = _replace_registry_model(registry, dependency_lock_sha256="0" * 64)
    else:  # pragma: no cover - exhaustive parametrization guard
        raise AssertionError(drift)

    with pytest.raises(ValueError, match=match):
        load_manifest_bundle(bundle_path, registry, revisions, licenses)
