from __future__ import annotations

import hashlib
import json
import shutil
import socket
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
import yaml
from huggingface_hub.hf_api import RepoFile, RepoFolder

from classscribe_manifest_tool.cli import main
from classscribe_manifest_tool.generator import (
    ClassifiedPayloadFile,
    ManifestMetadata,
    build_model_manifest,
    canonical_bundle_bytes,
    canonical_manifest_bytes,
    generate_release_bundle,
    write_release_bundle,
)
from classscribe_manifest_tool.inputs import load_release_inputs
from classscribe_manifest_tool.verifier import BundleVerificationError, verify_bundle

SOURCE_ROOT = Path(__file__).resolve().parents[3]


class _FixtureRepositoryApi:
    def model_info(
        self,
        repo_id: str,
        *,
        revision: str,
        files_metadata: bool,
        token: bool,
    ) -> object:
        del repo_id, files_metadata, token
        return type("ModelInfo", (), {"sha": revision})()

    def list_repo_tree(
        self,
        repo_id: str,
        *,
        recursive: bool,
        revision: str,
        repo_type: str,
        token: bool,
    ) -> Iterable[RepoFile | RepoFolder]:
        del repo_id, recursive, revision, repo_type, token
        return (
            RepoFile(  # type: ignore[no-untyped-call]
                path="config.json", size=6, oid="fixture-git-oid"
            ),
        )


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_verified_fixture(root: Path) -> Path:
    for relative in (
        "protocol/schema/v1/model-manifest.schema.json",
        "protocol/schema/v1/model-manifest-bundle.schema.json",
        "config/schema/model-registry.v1.schema.json",
        "config/schema/model-file-selection.v1.schema.json",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_ROOT / relative, destination)

    worker_lock = b'version = 1\nrevision = 3\nrequires-python = "==3.12.*"\n'
    tool_lock = b'version = 1\nrevision = 3\nrequires-python = "==3.12.*"\n'
    worker_lock_sha = hashlib.sha256(worker_lock).hexdigest()
    _write_bytes(root / "workers/worker/uv.lock", worker_lock)
    for filename in ("adapter.py", "healthcheck.py", "pyproject.toml", "worker.py"):
        _write_bytes(root / "workers/worker" / filename, b"fixture\n")
    _write_bytes(root / "tools/model-manifest/uv.lock", tool_lock)
    _write_bytes(
        root / "config/model-worker-support.v1.json",
        _canonical_json(
            {
                "schema_version": 1,
                "models": [{"model_id": "alpha", "worker": "worker"}],
            }
        ),
    )

    model = {
        "id": "alpha",
        "display_name": "Alpha",
        "provider": "Owner",
        "repository": "owner/model",
        "revision": "a" * 40,
        "worker": "worker",
        "languages": ["en"],
        "tasks": ["asr"],
        "modes": ["batch"],
        "input_sample_rate": 16000,
        "input_channels": 1,
        "input_dtype": "pcm_s16le",
        "runtime_backend": "fixture_backend",
        "dtype": "float32",
        "trust_remote_code": False,
        "safe_operating_window_seconds": 30,
        "vendor_claimed_max_seconds": None,
        "responses": {
            "segment_timestamps": True,
            "word_timestamps": False,
            "confidence": False,
            "logprobs": False,
            "punctuation": False,
            "speakers": False,
            "hotwords": False,
        },
        "resources": {
            "vram_class": "tiny",
            "estimated_vram_mb": 0,
            "measured_vram_mb": None,
            "safety_margin_mb": 512,
        },
        "dependency_lock": "workers/worker/uv.lock",
        "dependency_lock_sha256": worker_lock_sha,
        "installation": {
            "state": "not_installed",
            "artifact_sha256": None,
            "local_revision": None,
        },
        "benchmark": {"latest_run_id": None, "results": {}, "source": "local_gold"},
        "enabled": True,
        "experimental": False,
        "known_defects": [],
        "disable_reason": None,
    }
    registry = {
        "schema_version": 1,
        "registry_revision": 1,
        "facts_as_of": "2026-09-03",
        "sources": ["https://huggingface.co/owner/model"],
        "models": [model],
        "rankings": {"fixture": ["alpha"]},
        "history": [],
    }
    registry_path = root / "config/model-registry.v1.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    _write_bytes(
        root / "config/model-revisions.lock.json",
        _canonical_json(
            {
                "schema_version": 1,
                "registry_revision": 1,
                "facts_as_of": "2026-09-03",
                "models": [
                    {
                        "id": "alpha",
                        "repository": "owner/model",
                        "revision": "a" * 40,
                        "worker": "worker",
                        "enabled": True,
                    }
                ],
            }
        ),
    )
    _write_bytes(
        root / "config/model-licenses.v1.json",
        _canonical_json(
            {
                "schema_version": 1,
                "facts_as_of": "2026-09-03",
                "models": [
                    {
                        "repository": "owner/model",
                        "license_id": "Apache-2.0",
                        "license_url": "https://huggingface.co/owner/model",
                        "requires_terms_acceptance": False,
                        "verified_as_of": "2026-09-03",
                    }
                ],
            }
        ),
    )
    selection = {
        "schema_version": 1,
        "models": {
            "alpha": {
                "include": ["config.json"],
                "kinds": {"config.json": "config"},
                "exclude": [],
            }
        },
    }
    (root / "config/model-file-selection.v1.yaml").write_text(
        yaml.safe_dump(selection, sort_keys=False), encoding="utf-8"
    )

    metadata = ManifestMetadata(
        model_id="alpha",
        repository="owner/model",
        revision="a" * 40,
        worker="worker",
        license_id="Apache-2.0",
        license_url="https://huggingface.co/owner/model",
        requires_terms_acceptance=False,
        trust_remote_code=False,
        environment={
            "python": "3.12",
            "runtime_backend": "fixture_backend",
            "dtype": "float32",
            "worker_lock": "workers/worker/uv.lock",
            "worker_lock_sha256": worker_lock_sha,
        },
    )
    manifest = canonical_manifest_bytes(
        build_model_manifest(
            metadata,
            (
                ClassifiedPayloadFile(
                    "config.json", 6, hashlib.sha256(b"config").hexdigest(), "config"
                ),
            ),
        ),
        token=None,
    )
    manifests = {"alpha": manifest}
    bundle = canonical_bundle_bytes(
        registry_revision=1,
        facts_as_of="2026-09-03",
        manifests=manifests,
        generator_lock=tool_lock,
        source_date_epoch=1788451200,
        token=None,
    )
    bundle_path = root / "config/model-manifests/v1/bundle.v1.json"
    _write_bytes(bundle_path.parent / "alpha.json", manifest)
    _write_bytes(bundle_path, bundle)
    return bundle_path


def _add_component_source_inputs(root: Path) -> None:
    revisions_path = root / "config/model-revisions.lock.json"
    revisions = json.loads(revisions_path.read_bytes())
    revisions["component_sources"] = [
        {
            "model_id": "alpha",
            "repository": "upstream/component",
            "revision": "c" * 40,
            "relationship": "derived",
            "files": [
                {
                    "installed_path": "config.json",
                    "source_path": "source-config.json",
                    "source_sha256": "d" * 64,
                    "source_size_bytes": 7,
                }
            ],
        }
    ]
    revisions_path.write_bytes(_canonical_json(revisions))
    licenses_path = root / "config/model-licenses.v1.json"
    licenses = json.loads(licenses_path.read_bytes())
    licenses["models"].append(
        {
            "repository": "upstream/component",
            "license_id": "CC-BY-4.0",
            "license_url": "https://huggingface.co/upstream/component",
            "requires_terms_acceptance": False,
            "verified_as_of": "2026-09-03",
        }
    )
    licenses_path.write_bytes(_canonical_json(licenses))


def _add_component_source_manifest(bundle_path: Path) -> None:
    root = bundle_path.parents[3]
    revisions = json.loads((root / "config/model-revisions.lock.json").read_bytes())
    source = dict(revisions["component_sources"][0])
    source.pop("model_id")
    licenses = json.loads((root / "config/model-licenses.v1.json").read_bytes())
    license_row = next(
        item for item in licenses["models"] if item["repository"] == source["repository"]
    )
    source.update(
        {
            "license_id": license_row["license_id"],
            "license_url": license_row["license_url"],
            "requires_terms_acceptance": license_row["requires_terms_acceptance"],
        }
    )
    _rewrite_member_and_bundle(
        bundle_path, lambda member: member.update({"component_sources": [source]})
    )


def _rewrite_member_and_bundle(bundle_path: Path, mutate: Any) -> None:
    member_path = bundle_path.parent / "alpha.json"
    member = json.loads(member_path.read_bytes())
    mutate(member)
    content = _canonical_json(member)
    member_path.write_bytes(content)
    bundle = json.loads(bundle_path.read_bytes())
    bundle["manifests"][0]["sha256"] = hashlib.sha256(content).hexdigest()
    bundle_path.write_bytes(_canonical_json(bundle))


def test_bundle_verifier_runs_offline_and_checks_complete_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)

    def reject_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline verifier attempted network access")

    monkeypatch.setattr(socket, "socket", reject_network)
    result = verify_bundle(bundle_path, root=tmp_path)

    assert result.model_count == 1
    assert result.bundle_sha256 == hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    assert result.manifest_sha256 == {
        "alpha": hashlib.sha256((bundle_path.parent / "alpha.json").read_bytes()).hexdigest()
    }
    assert result.models == (
        {
            "model_id": "alpha",
            "manifest_available": True,
            "manifest_sha256": result.manifest_sha256["alpha"],
            "worker_implemented": True,
            "installable": True,
            "install_block_reason": None,
            "enabled": True,
        },
    )


def test_bundle_verifier_rejects_worker_support_registry_drift(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    support_path = tmp_path / "config/model-worker-support.v1.json"
    support = json.loads(support_path.read_bytes())
    support["models"][0]["worker"] = "different"
    support_path.write_bytes(_canonical_json(support))

    with pytest.raises(BundleVerificationError, match="worker support contract differs"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_accepts_frozen_component_source_provenance(
    tmp_path: Path,
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)
    _add_component_source_manifest(bundle_path)

    result = verify_bundle(bundle_path, root=tmp_path)

    assert result.model_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "upstream/other"),
        ("revision", "e" * 40),
        ("relationship", "copied"),
        ("license_id", "MIT"),
        ("license_url", "https://huggingface.co/upstream/other"),
        ("requires_terms_acceptance", True),
    ],
)
def test_bundle_verifier_rejects_tampered_component_source_identity(
    tmp_path: Path, field: str, value: object
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)
    _add_component_source_manifest(bundle_path)

    def mutate(member: dict[str, Any]) -> None:
        member["component_sources"][0][field] = value

    _rewrite_member_and_bundle(bundle_path, mutate)

    with pytest.raises(BundleVerificationError, match="component sources differ"):
        verify_bundle(bundle_path, root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installed_path", "other.json"),
        ("source_path", "other.bin"),
        ("source_sha256", "e" * 64),
        ("source_size_bytes", 8),
    ],
)
def test_bundle_verifier_rejects_tampered_component_source_file(
    tmp_path: Path, field: str, value: object
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)
    _add_component_source_manifest(bundle_path)

    def mutate(member: dict[str, Any]) -> None:
        member["component_sources"][0]["files"][0][field] = value

    _rewrite_member_and_bundle(bundle_path, mutate)

    with pytest.raises(BundleVerificationError, match="component sources differ"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_rejects_missing_or_unexpected_component_source(
    tmp_path: Path,
) -> None:
    required_bundle = _build_verified_fixture(tmp_path / "required")
    _add_component_source_inputs(tmp_path / "required")
    with pytest.raises(BundleVerificationError, match="component sources differ"):
        verify_bundle(required_bundle, root=tmp_path / "required")

    unexpected_bundle = _build_verified_fixture(tmp_path / "unexpected")
    _add_component_source_inputs(tmp_path / "unexpected")
    _add_component_source_manifest(unexpected_bundle)
    revisions_path = tmp_path / "unexpected/config/model-revisions.lock.json"
    revisions = json.loads(revisions_path.read_bytes())
    revisions.pop("component_sources")
    revisions_path.write_bytes(_canonical_json(revisions))
    licenses_path = tmp_path / "unexpected/config/model-licenses.v1.json"
    licenses = json.loads(licenses_path.read_bytes())
    licenses["models"].pop()
    licenses_path.write_bytes(_canonical_json(licenses))
    with pytest.raises(BundleVerificationError, match="unexpected component source"):
        verify_bundle(unexpected_bundle, root=tmp_path / "unexpected")


def test_bundle_verifier_rejects_copied_component_with_different_payload_bytes(
    tmp_path: Path,
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)
    revisions_path = tmp_path / "config/model-revisions.lock.json"
    revisions = json.loads(revisions_path.read_bytes())
    revisions["component_sources"][0]["relationship"] = "copied"
    revisions_path.write_bytes(_canonical_json(revisions))
    _add_component_source_manifest(bundle_path)

    with pytest.raises(BundleVerificationError, match="differs from its fixed source"):
        verify_bundle(bundle_path, root=tmp_path)


def test_generator_input_loader_accepts_complete_cross_checked_fixture(
    tmp_path: Path,
) -> None:
    _build_verified_fixture(tmp_path)

    inputs = load_release_inputs(
        registry_path=tmp_path / "config/model-registry.v1.yaml",
        revisions_path=tmp_path / "config/model-revisions.lock.json",
        licenses_path=tmp_path / "config/model-licenses.v1.json",
        selection_path=tmp_path / "config/model-file-selection.v1.yaml",
        require_complete_selection=True,
    )

    assert inputs.root == tmp_path
    assert inputs.registry_revision == 1
    assert inputs.facts_as_of == "2026-09-03"
    assert [model.model_id for model in inputs.models] == ["alpha"]
    assert inputs.models[0].dependency_lock_sha256 == hashlib.sha256(
        (tmp_path / "workers/worker/uv.lock").read_bytes()
    ).hexdigest()
    assert inputs.selections.selection("alpha").include == ("config.json",)


def test_generator_input_loader_freezes_component_source_and_license(
    tmp_path: Path,
) -> None:
    _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)

    inputs = load_release_inputs(
        registry_path=tmp_path / "config/model-registry.v1.yaml",
        revisions_path=tmp_path / "config/model-revisions.lock.json",
        licenses_path=tmp_path / "config/model-licenses.v1.json",
        selection_path=tmp_path / "config/model-file-selection.v1.yaml",
        require_complete_selection=True,
    )

    sources = inputs.models[0].component_sources
    assert len(sources) == 1
    assert sources[0].repository == "upstream/component"
    assert sources[0].revision == "c" * 40
    assert sources[0].relationship == "derived"
    assert sources[0].license_id == "CC-BY-4.0"
    assert sources[0].files[0].installed_path == "config.json"
    assert sources[0].files[0].source_sha256 == "d" * 64


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("model_id", "outside", "outside the registry"),
        ("repository", "owner/model", "differ from payload"),
        ("revision", "C" * 40, "full lowercase commit"),
        ("relationship", "copied-ish", "copied or derived"),
    ],
)
def test_generator_input_loader_rejects_invalid_component_source_identity(
    tmp_path: Path, field: str, value: object, match: str
) -> None:
    _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)
    revisions_path = tmp_path / "config/model-revisions.lock.json"
    revisions = json.loads(revisions_path.read_bytes())
    revisions["component_sources"][0][field] = value
    revisions_path.write_bytes(_canonical_json(revisions))

    with pytest.raises(ValueError, match=match):
        load_release_inputs(
            registry_path=tmp_path / "config/model-registry.v1.yaml",
            revisions_path=revisions_path,
            licenses_path=tmp_path / "config/model-licenses.v1.json",
            selection_path=tmp_path / "config/model-file-selection.v1.yaml",
            require_complete_selection=True,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("installed_path", "outside.json", "outside model selection"),
        ("source_path", "../escape.json", "unsafe or non-exact path"),
        ("source_sha256", "D" * 64, "SHA-256 is invalid"),
        ("source_size_bytes", True, "size is invalid"),
    ],
)
def test_generator_input_loader_rejects_invalid_component_source_file(
    tmp_path: Path, field: str, value: object, match: str
) -> None:
    _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)
    revisions_path = tmp_path / "config/model-revisions.lock.json"
    revisions = json.loads(revisions_path.read_bytes())
    revisions["component_sources"][0]["files"][0][field] = value
    revisions_path.write_bytes(_canonical_json(revisions))

    with pytest.raises(ValueError, match=match):
        load_release_inputs(
            registry_path=tmp_path / "config/model-registry.v1.yaml",
            revisions_path=revisions_path,
            licenses_path=tmp_path / "config/model-licenses.v1.json",
            selection_path=tmp_path / "config/model-file-selection.v1.yaml",
            require_complete_selection=True,
        )


def test_generator_input_loader_rejects_missing_component_license(
    tmp_path: Path,
) -> None:
    _build_verified_fixture(tmp_path)
    _add_component_source_inputs(tmp_path)
    licenses_path = tmp_path / "config/model-licenses.v1.json"
    licenses = json.loads(licenses_path.read_bytes())
    licenses["models"].pop()
    licenses_path.write_bytes(_canonical_json(licenses))

    with pytest.raises(ValueError, match="frozen model and component sources"):
        load_release_inputs(
            registry_path=tmp_path / "config/model-registry.v1.yaml",
            revisions_path=tmp_path / "config/model-revisions.lock.json",
            licenses_path=licenses_path,
            selection_path=tmp_path / "config/model-file-selection.v1.yaml",
            require_complete_selection=True,
        )


def test_local_fixture_repository_generates_writes_and_verifies_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_bundle = _build_verified_fixture(tmp_path)
    shutil.rmtree(existing_bundle.parent)
    inputs = load_release_inputs(
        registry_path=tmp_path / "config/model-registry.v1.yaml",
        revisions_path=tmp_path / "config/model-revisions.lock.json",
        licenses_path=tmp_path / "config/model-licenses.v1.json",
        selection_path=tmp_path / "config/model-file-selection.v1.yaml",
        require_complete_selection=True,
    )
    download_count = 0

    def fixture_download(_repo_id: str, **kwargs: Any) -> str:
        nonlocal download_count
        download_count += 1
        local_dir = Path(kwargs["local_dir"])
        (local_dir / "config.json").write_bytes(b"config")
        return str(local_dir)

    manifests, bundle = generate_release_bundle(
        inputs,
        source_date_epoch=1788451200,
        token=None,
        accepted_repositories=(),
        api_factory=_FixtureRepositoryApi,
        downloader=fixture_download,
    )
    output = tmp_path / "config/model-manifests/v1"
    write_release_bundle(output, manifests, bundle, token=None)

    def reject_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline verifier attempted network access")

    monkeypatch.setattr(socket, "socket", reject_network)
    result = verify_bundle(output / "bundle.v1.json", root=tmp_path)

    assert download_count == 2
    assert result.model_count == 1
    assert result.manifest_sha256 == {
        "alpha": hashlib.sha256(manifests["alpha"]).hexdigest()
    }
    assert result.bundle_sha256 == hashlib.sha256(bundle).hexdigest()


def test_verify_cli_runs_under_socket_blockade_with_exact_bundle_interface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)

    def reject_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline verify CLI attempted network access")

    monkeypatch.setattr(socket, "socket", reject_network)

    result = main(["verify", "--bundle", str(bundle_path)])

    reported = json.loads(capsys.readouterr().out)
    assert result == 0
    assert reported["status"] == "verified"
    assert reported["model_count"] == 1
    assert reported["bundle_sha256"] == hashlib.sha256(bundle_path.read_bytes()).hexdigest()


def test_bundle_verifier_rejects_member_byte_tampering(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    member = bundle_path.parent / "alpha.json"
    member.write_bytes(member.read_bytes() + b" ")

    with pytest.raises(BundleVerificationError, match="SHA-256 differs"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_rejects_any_noncanonical_bundle_byte(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    bundle_path.write_bytes(bundle_path.read_bytes() + b" ")

    with pytest.raises(BundleVerificationError, match="not canonical JSON"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_rejects_bundle_schema_violation(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    bundle = json.loads(bundle_path.read_bytes())
    bundle["unexpected"] = True
    bundle_path.write_bytes(_canonical_json(bundle))

    with pytest.raises(BundleVerificationError, match="failed schema validation"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_rejects_missing_directory_member(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    (bundle_path.parent / "alpha.json").unlink()

    with pytest.raises(BundleVerificationError, match="member set differs"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_rejects_extra_directory_member(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    (bundle_path.parent / "extra.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(BundleVerificationError, match="member set differs"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_rejects_duplicate_model_id(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    bundle = json.loads(bundle_path.read_bytes())
    bundle["manifests"].append(
        {
            "model_id": "alpha",
            "path": "duplicate.json",
            "sha256": "c" * 64,
        }
    )
    bundle_path.write_bytes(_canonical_json(bundle))

    with pytest.raises(BundleVerificationError, match="unique sorted model IDs"):
        verify_bundle(bundle_path, root=tmp_path)


@pytest.mark.parametrize("unsafe_path", ["../alpha.json", "nested/alpha.json"])
def test_bundle_verifier_rejects_member_path_escape(
    tmp_path: Path, unsafe_path: str
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    bundle = json.loads(bundle_path.read_bytes())
    bundle["manifests"][0]["path"] = unsafe_path
    bundle_path.write_bytes(_canonical_json(bundle))

    with pytest.raises(BundleVerificationError, match="failed schema validation"):
        verify_bundle(bundle_path, root=tmp_path)


def test_bundle_verifier_rejects_generator_lock_byte_drift(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    lock = tmp_path / "tools/model-manifest/uv.lock"
    lock.write_bytes(lock.read_bytes() + b"# drift\n")

    with pytest.raises(BundleVerificationError, match="generator lock SHA-256 differs"):
        verify_bundle(bundle_path, root=tmp_path)


@pytest.mark.parametrize("sensitive", ["/tmp/private-cache", "hf_123456789secret"])
def test_bundle_verifier_rejects_local_or_secret_manifest_values(
    tmp_path: Path, sensitive: str
) -> None:
    bundle_path = _build_verified_fixture(tmp_path)

    def mutate(member: dict[str, Any]) -> None:
        member["environment"]["local_cache"] = sensitive

    _rewrite_member_and_bundle(bundle_path, mutate)

    with pytest.raises(BundleVerificationError, match="secret or local-machine") as failure:
        verify_bundle(bundle_path, root=tmp_path)

    assert sensitive not in str(failure.value)


def test_bundle_verifier_rejects_worker_lock_drift(tmp_path: Path) -> None:
    bundle_path = _build_verified_fixture(tmp_path)
    (tmp_path / "workers/worker/uv.lock").write_text("version = 1\n# drift\n", encoding="utf-8")

    with pytest.raises(BundleVerificationError, match="worker lock SHA-256 differs"):
        verify_bundle(bundle_path, root=tmp_path)
