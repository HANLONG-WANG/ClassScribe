from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from classscribe.models.registry import RegistryStore, load_registry

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config/model-registry.v1.yaml"


def test_bootstrap_registry_is_complete_pinned_and_lock_verified() -> None:
    registry = load_registry(REGISTRY_PATH, repository_root=ROOT)
    assert registry.schema_version == 1
    assert registry.facts_as_of == "2026-09-03"
    assert len(registry.models) == 20
    assert len({item.revision for item in registry.models}) == 20
    assert all(len(item.revision) == 40 for item in registry.models)
    assert all(item.input_sample_rate == 16_000 for item in registry.models)
    assert all(item.input_channels == 1 for item in registry.models)
    assert all(item.installation.state == "not_installed" for item in registry.models)
    assert {item.worker for item in registry.models} == {
        "ark",
        "firered",
        "funasr_experimental",
        "granite",
        "moss_en",
        "moss_td",
        "nemotron",
        "pyannote",
        "qwen",
        "vibevoice",
        "voxtral",
    }
    for entry in registry.models:
        lock = ROOT / entry.dependency_lock
        assert hashlib.sha256(lock.read_bytes()).hexdigest() == entry.dependency_lock_sha256


def test_bootstrap_rankings_preserve_language_specific_order_and_disable_experiments() -> None:
    registry = load_registry(REGISTRY_PATH)
    assert registry.rankings["classroom.zh"][:3] == (
        "firered_asr2_aed",
        "ark_asr_3b",
        "qwen3_asr_1_7b",
    )
    assert registry.rankings["classroom.ja"][:3] == (
        "granite_speech_4_1_2b",
        "qwen3_asr_1_7b",
        "ark_asr_3b",
    )
    assert registry.rankings["classroom.en"][:2] == (
        "moss_transcribe_preview_2b",
        "ark_asr_3b",
    )
    japanese = registry.candidates("classroom.ja", language="ja", task="asr", mode="batch")
    assert [item.id for item in japanese] == [
        "granite_speech_4_1_2b",
        "qwen3_asr_1_7b",
        "ark_asr_3b",
        "moss_td_0_9b",
    ]
    assert registry.model("fun_asr_nano_2512").disable_reason
    assert registry.model("voxtral_mini_4b_realtime_2602").resources.vram_class == "too_large"
    assert "ja" not in registry.model("firered_asr2_aed").languages


def test_registry_mutations_are_atomic_audited_and_rollbackable(tmp_path: Path) -> None:
    target = tmp_path / "config/model-registry.yaml"
    store = RegistryStore(target)
    initial = store.initialize_from(REGISTRY_PATH)
    assert target.stat().st_mode & 0o777 == 0o600

    disabled = store.disable("granite_speech_4_1_2b", "local health check failed")
    assert not disabled.model("granite_speech_4_1_2b").enabled
    assert disabled.registry_revision == initial.registry_revision + 1
    enabled = store.enable("granite_speech_4_1_2b")
    assert enabled.model("granite_speech_4_1_2b").enabled

    revision = "a" * 40
    upgraded = store.upgrade("granite_speech_4_1_2b", revision)
    assert upgraded.model("granite_speech_4_1_2b").revision == revision
    rolled_back = store.rollback("granite_speech_4_1_2b")
    assert (
        rolled_back.model("granite_speech_4_1_2b").revision
        == initial.model("granite_speech_4_1_2b").revision
    )
    assert [item["action"] for item in rolled_back.history] == [
        "disable",
        "enable",
        "upgrade",
        "rollback",
    ]


def test_registry_records_installation_benchmark_and_user_ranking(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.yaml")
    registry = store.initialize_from(REGISTRY_PATH)
    entry = registry.model("qwen3_asr_0_6b")
    installed = store.mark_installation(
        entry.id,
        revision=entry.revision,
        artifact_sha256="b" * 64,
        measured_vram_mb=3120,
    )
    assert installed.model(entry.id).resources.measured_vram_mb == 3120
    assert installed.model(entry.id).installation.artifact_sha256 == "b" * 64

    benchmarked = store.record_benchmark(
        entry.id,
        run_id="run-1",
        dataset_id="ja-dictation-v1",
        metrics={"cer": 0.17, "rtf": 0.2},
    )
    assert benchmarked.model(entry.id).benchmark.source == "local_gold"
    ranked = store.set_ranking("ibus.ja.fast", [entry.id])
    assert ranked.rankings["ibus.ja.fast"] == (entry.id,)
    restored = store.rollback_ranking("ibus.ja.fast")
    assert restored.rankings["ibus.ja.fast"] != (entry.id,)
    with pytest.raises(ValueError, match="quality_probability"):
        store.record_benchmark(
            entry.id,
            run_id="run-2",
            dataset_id="bad",
            metrics={"quality_probability": 0.9},
        )

    calibrated = store.record_calibrated_benchmark(
        entry.id,
        run_id="run-3",
        dataset_id="ja-dictation-calibrated-v1",
        metrics={"cer": 0.12},
        calibration_artifact_sha256="c" * 64,
    )
    assert calibrated.model(entry.id).benchmark.latest_run_id == "run-3"
    upgraded = store.upgrade(entry.id, "d" * 40)
    assert upgraded.model(entry.id).benchmark.latest_run_id is None
    assert upgraded.model(entry.id).benchmark.source == "bootstrap_public_facts"


def test_registry_fails_closed_on_lock_drift(tmp_path: Path) -> None:
    registry = load_registry(REGISTRY_PATH)
    root = tmp_path / "repo"
    for item in registry.models:
        target = root / item.dependency_lock
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / item.dependency_lock).read_bytes())
    (root / registry.models[0].dependency_lock).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="lock hash mismatch"):
        load_registry(REGISTRY_PATH, repository_root=root)
