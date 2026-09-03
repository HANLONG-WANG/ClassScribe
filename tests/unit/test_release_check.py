from __future__ import annotations

import json
import wave
from pathlib import Path

from classscribe.models import load_registry
from classscribe.release_check import validate_release, validate_revision_lock

from scripts.materialize_regression_audio import materialize

ROOT = Path(__file__).resolve().parents[2]


def test_revision_lock_exactly_matches_registry() -> None:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    lock = json.loads((ROOT / "config/model-revisions.lock.json").read_text(encoding="utf-8"))
    assert validate_revision_lock(registry, lock) == ()
    lock["models"][0]["revision"] = "0" * 40
    assert validate_revision_lock(registry, lock) == (
        "model revision lock differs for moss_td_0_9b",
    )


def test_release_check_passes_engineering_gates_and_reports_external_blocks() -> None:
    result = validate_release(ROOT)
    assert result.status == "blocked"
    assert result.checks == {
        "artifact_inventory": True,
        "model_revision_lock": True,
        "model_license_inventory": True,
        "dependency_lock_hashes": True,
        "final_architecture": True,
        "production_runtime_binding": True,
        "source_license": False,
        "phase12_acceptance": False,
        "desktop_matrix": False,
    }
    assert "ClassScribe source license is not finalized" in result.reasons
    assert "production classroom stage handlers are not bound" not in result.reasons
    assert "Phase 12 real-model acceptance is not passed" in result.reasons


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
