from __future__ import annotations

import hashlib
import json
import shutil
import wave
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from classscribe.models import ModelRegistry, load_registry
from classscribe.release_check import (
    _load_release_waiver,
    _model_license_errors,
    _model_manifest_bundle_errors,
    _release_dependency_lock_errors,
    _release_result,
    validate_release,
    validate_revision_lock,
)

from scripts.materialize_regression_audio import materialize
from scripts.validate_release import main as validate_release_main

ROOT = Path(__file__).resolve().parents[2]


def test_revision_lock_exactly_matches_registry() -> None:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    lock = json.loads((ROOT / "config/model-revisions.lock.json").read_text(encoding="utf-8"))
    assert validate_revision_lock(registry, lock) == ()
    lock["models"][0]["revision"] = "0" * 40
    assert validate_revision_lock(registry, lock) == (
        "model revision lock differs for moss_td_0_9b",
    )


def test_revision_lock_validates_component_source_identity_and_files() -> None:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    lock = json.loads((ROOT / "config/model-revisions.lock.json").read_text(encoding="utf-8"))
    assert validate_revision_lock(registry, lock) == ()

    invalid_revision = deepcopy(lock)
    invalid_revision["component_sources"][0]["revision"] = "A" * 40
    assert "model revision lock component source revision is invalid" in (
        validate_revision_lock(registry, invalid_revision)
    )

    unsafe_path = deepcopy(lock)
    unsafe_path["component_sources"][0]["files"][0]["source_path"] = "../escape.bin"
    assert "model revision lock component source file path is invalid" in (
        validate_revision_lock(registry, unsafe_path)
    )


def test_release_license_inventory_covers_component_sources() -> None:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    lock = json.loads((ROOT / "config/model-revisions.lock.json").read_text(encoding="utf-8"))
    inventory = json.loads((ROOT / "LICENSES/inventory.v1.json").read_text(encoding="utf-8"))
    licenses_path = ROOT / "config/model-licenses.v1.json"

    assert _model_license_errors(licenses_path, registry, lock, inventory) == ()

    missing_source = deepcopy(inventory)
    del missing_source["model_weights"]["licenses"][
        "pyannote/wespeaker-voxceleb-resnet34-LM"
    ]
    assert _model_license_errors(licenses_path, registry, lock, missing_source) == (
        "source inventory model license map differs from frozen disclosure",
    )


def test_release_check_applies_exact_waiver_without_changing_raw_evidence() -> None:
    result = validate_release(ROOT)
    assert result.status == "ready_with_waivers"
    assert result.checks == {
        "artifact_inventory": True,
        "model_revision_lock": True,
        "model_license_inventory": True,
        "model_manifest_bundle": True,
        "dependency_lock_hashes": True,
        "final_architecture": True,
        "production_runtime_binding": True,
        "source_license": False,
        "phase12_acceptance": False,
        "desktop_matrix": False,
    }
    assert result.effective_checks == dict.fromkeys(result.checks, True)
    assert result.waived_gates == (
        "source_license",
        "phase12_acceptance",
        "desktop_matrix",
    )
    assert result.blocking_reasons == ()
    assert result.waiver_errors == ()
    assert "ClassScribe source license is not finalized" in result.reasons
    assert "production classroom stage handlers are not bound" not in result.reasons
    assert "Phase 12 real-model acceptance is not passed" in result.reasons
    assert result.details["waivers_do_not_change_raw_evidence"] is True
    assert result.details["waiver"] == {
        "authorized_on": "2026-09-06",
        "integrity": "valid",
        "scope": "automated_release_blocking_only",
        "sha256": "b5a6f6fd90240e630b16b4382a52c8acc2d65542d84e50be9ff56a78ee2a8bf5",
        "waiver_id": "classscribe-0.1.0-release-blocking-2026-09-06",
    }


def test_frozen_release_readiness_matches_current_fail_closed_result() -> None:
    result = validate_release(ROOT)
    frozen = json.loads(
        (ROOT / "release/release-readiness.json").read_text(encoding="utf-8")
    )
    release_manifest = json.loads(
        (ROOT / "release/release-manifest.v1.json").read_text(encoding="utf-8")
    )

    assert frozen == result.as_dict()
    assert release_manifest["release_gates"] == list(result.checks)


def test_release_check_cli_exits_zero_only_with_a_valid_effective_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert validate_release_main(["--root", str(ROOT)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready_with_waivers"
    assert output["checks"]["source_license"] is False
    assert output["effective_checks"]["source_license"] is True
    assert output["blocking_reasons"] == []


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


@pytest.mark.parametrize(
    ("attack", "expected"),
    [
        ("missing", "release waiver is missing or unsafe"),
        ("symlink", "release waiver is missing or unsafe"),
        ("sha", "release waiver SHA-256 differs from release manifest"),
        ("noncanonical", "JSON bytes are not canonical"),
        ("version", "release waiver release identity differs from release manifest"),
        ("registry", "release waiver release identity differs from release manifest"),
        ("extra_gate", "release waiver gates exceed or differ from the authorized scope"),
        ("acknowledgement", "release waiver acknowledgements are incomplete"),
        ("authorization", "release waiver authorization is invalid"),
        ("inventory", "release manifest omits waiver artifact"),
        ("gate_inventory", "release manifest gates differ from checker gates"),
    ],
)
def test_release_waiver_attacks_fail_closed(
    tmp_path: Path, attack: str, expected: str
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    waiver_path = release / "release-waivers.v1.json"
    source = ROOT / "release/release-waivers.v1.json"
    waiver_path.write_bytes(source.read_bytes())
    manifest = json.loads(
        (ROOT / "release/release-manifest.v1.json").read_text(encoding="utf-8")
    )
    release_gates = tuple(manifest["release_gates"])

    if attack == "missing":
        waiver_path.unlink()
    elif attack == "symlink":
        waiver_path.unlink()
        target = tmp_path / "outside-waiver.json"
        target.write_bytes(source.read_bytes())
        waiver_path.symlink_to(target)
    elif attack == "sha":
        waiver_path.write_bytes(waiver_path.read_bytes() + b" ")
    elif attack == "gate_inventory":
        manifest["release_gates"] = [*manifest["release_gates"], "unreviewed_gate"]
    elif attack == "inventory":
        manifest["required_artifacts"]["test_and_diagnostics"].remove(
            "release/release-waivers.v1.json"
        )
    else:
        waiver = json.loads(waiver_path.read_text(encoding="utf-8"))
        if attack == "noncanonical":
            raw = json.dumps(waiver).encode()
        elif attack == "version":
            waiver["release"]["version"] = "0.2.0"
            raw = _canonical_json(waiver)
        elif attack == "registry":
            waiver["release"]["registry_revision"] = 2
            raw = _canonical_json(waiver)
        elif attack == "extra_gate":
            waiver["waived_gates"].append("artifact_inventory")
            raw = _canonical_json(waiver)
        elif attack == "acknowledgement":
            waiver["acknowledgements"]["raw_checks_remain_failed"] = False
            raw = _canonical_json(waiver)
        elif attack == "authorization":
            waiver["authorization"]["scope"] = "all_release_checks"
            raw = _canonical_json(waiver)
        else:  # pragma: no cover - exhaustive parametrization guard
            raise AssertionError(attack)
        waiver_path.write_bytes(raw)
        manifest["release_waiver"]["sha256"] = hashlib.sha256(raw).hexdigest()

    waiver, errors = _load_release_waiver(tmp_path, manifest, release_gates)
    assert waiver is None
    assert any(expected in error for error in errors)


def test_fail_closed_policy_does_not_apply_an_unreferenced_waiver() -> None:
    manifest = json.loads(
        (ROOT / "release/release-manifest.v1.json").read_text(encoding="utf-8")
    )
    manifest["release_policy"] = "fail_closed"
    del manifest["release_waiver"]
    waiver, errors = _load_release_waiver(
        ROOT, manifest, tuple(manifest["release_gates"])
    )
    assert waiver is None
    assert errors == ()


def test_release_waiver_never_covers_an_engineering_gate() -> None:
    manifest = json.loads(
        (ROOT / "release/release-manifest.v1.json").read_text(encoding="utf-8")
    )
    checks = dict.fromkeys(manifest["release_gates"], True)
    checks["artifact_inventory"] = False
    for gate in ("source_license", "phase12_acceptance", "desktop_matrix"):
        checks[gate] = False
    result = _release_result(
        ROOT,
        manifest,
        checks=checks,
        reasons=("raw failures are retained",),
        details={},
    )
    assert result.status == "blocked"
    assert result.effective_checks["artifact_inventory"] is False
    assert result.blocking_reasons == (
        "release gate remains blocked: artifact_inventory",
    )
    assert result.waived_gates == (
        "source_license",
        "phase12_acceptance",
        "desktop_matrix",
    )


def test_release_check_pins_manifest_tool_dependency_lock(tmp_path: Path) -> None:
    lock = tmp_path / "tools/model-manifest/uv.lock"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"frozen lock\n")
    manifest = {
        "dependency_lock_hashes": {
            "tools/model-manifest/uv.lock": (
                "f847f785a87717ac36c739c4ace76099df067fd3ce2e3b90bdac4c9b0fddd7e4"
            )
        }
    }
    assert _release_dependency_lock_errors(tmp_path, manifest) == ()

    lock.write_bytes(b"changed lock\n")
    assert _release_dependency_lock_errors(tmp_path, manifest) == (
        "release dependency lock hash differs: tools/model-manifest/uv.lock",
    )


def test_release_check_requires_manifest_tool_dependency_lock_pin(tmp_path: Path) -> None:
    assert _release_dependency_lock_errors(tmp_path, {"dependency_lock_hashes": {}}) == (
        "manifest tool dependency lock is not pinned",
    )


def _bundle_release_fixture(
    tmp_path: Path,
) -> tuple[Path, ModelRegistry, dict[str, Any]]:
    shutil.copytree(ROOT / "config", tmp_path / "config")
    registry = load_registry(tmp_path / "config/model-registry.v1.yaml")
    for relative in {model.dependency_lock for model in registry.models}:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    release_manifest = json.loads(
        (ROOT / "release/release-manifest.v1.json").read_text(encoding="utf-8")
    )
    return tmp_path, registry, release_manifest


def test_release_check_verifies_complete_model_bundle_inventory(tmp_path: Path) -> None:
    root, registry, release_manifest = _bundle_release_fixture(tmp_path)
    assert _model_manifest_bundle_errors(root, registry, release_manifest) == ()


@pytest.mark.parametrize(
    ("attack", "expected"),
    [
        ("delete_member", "model manifest bundle is invalid"),
        ("mutate_member", "model manifest bundle is invalid"),
        ("registry_revision", "model manifest bundle is invalid"),
        ("extra_member", "model manifest bundle directory contents differ from index"),
        ("member_sha", "model manifest bundle is invalid"),
        ("selection", "model file selection differs from manifest kinds"),
        ("inventory", "release manifest omits model bundle artifacts"),
        ("generator_lock", "model bundle generator lock differs from release manifest"),
    ],
)
def test_release_check_rejects_every_model_bundle_release_attack(
    tmp_path: Path, attack: str, expected: str
) -> None:
    root, registry, release_manifest = _bundle_release_fixture(tmp_path)
    bundle_path = root / "config/model-manifests/v1/bundle.v1.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    member_path = root / "config/model-manifests/v1" / bundle["manifests"][0]["path"]
    if attack == "delete_member":
        member_path.unlink()
    elif attack == "mutate_member":
        member_path.write_bytes(member_path.read_bytes() + b" ")
    elif attack == "registry_revision":
        value = registry.model_dump(mode="json")
        value["registry_revision"] = 2
        registry = ModelRegistry.model_validate(value)
    elif attack == "extra_member":
        (bundle_path.parent / "extra.json").write_text("{}\n", encoding="utf-8")
    elif attack == "member_sha":
        bundle["manifests"][0]["sha256"] = "0" * 64
        bundle_path.write_text(
            json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    elif attack == "selection":
        selection_path = root / "config/model-file-selection.v1.yaml"
        selection_path.write_text(
            selection_path.read_text(encoding="utf-8").replace(
                "      added_tokens.json: tokenizer",
                "      added_tokens.json: other",
                1,
            ),
            encoding="utf-8",
        )
    elif attack == "inventory":
        release_manifest["required_artifacts"]["source_and_locks"].remove(
            "config/model-manifests/v1/ark_asr_3b.json"
        )
    elif attack == "generator_lock":
        release_manifest["dependency_lock_hashes"]["tools/model-manifest/uv.lock"] = (
            "0" * 64
        )
    else:  # pragma: no cover - exhaustive parametrization guard
        raise AssertionError(attack)

    errors = _model_manifest_bundle_errors(root, registry, release_manifest)
    assert any(expected in error for error in errors)


def test_regression_audio_recipe_materializes_canonical_deterministic_wav(
    tmp_path: Path,
) -> None:
    manifest = ROOT / "tests/audio_fixtures/regression-audio.v1.json"
    first = materialize(manifest, tmp_path / "first")
    second = materialize(manifest, tmp_path / "second")
    assert [item.name for item in first] == [
        "silence-1s.wav",
        "boundary-tone-2s.wav",
        "speaker-window-cues-4s.wav",
    ]
    assert [item.read_bytes() for item in first] == [item.read_bytes() for item in second]
    with wave.open(str(first[0]), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        assert audio.getnframes() == 16000
        assert set(audio.readframes(16000)) == {0}
