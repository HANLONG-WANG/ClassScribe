from __future__ import annotations

from pathlib import Path

from classscribe.models import load_registry
from classscribe.models.support import (
    IMPLEMENTED_MODEL_WORKERS,
    REQUIRED_WORKER_FILES,
    load_worker_support,
    model_installability,
    worker_implemented,
    worker_lock_matches,
)

ROOT = Path(__file__).resolve().parents[2]
UNIMPLEMENTED_MODEL_IDS = {
    "firered_asr2_llm",
    "granite_speech_5_0_turboctc_470m",
    "vibevoice_asr_streaming_1_5b",
    "voxtral_mini_4b_realtime_2602",
}


def test_worker_support_matrix_matches_audited_registry_and_source_tree() -> None:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    registry_ids = {entry.id for entry in registry.models}

    assert load_worker_support(ROOT) == IMPLEMENTED_MODEL_WORKERS
    assert registry_ids - set(IMPLEMENTED_MODEL_WORKERS) == UNIMPLEMENTED_MODEL_IDS
    for entry in registry.models:
        assert worker_implemented(entry, ROOT) is (entry.id not in UNIMPLEMENTED_MODEL_IDS)


def test_worker_support_requires_safe_complete_worker_source(tmp_path: Path) -> None:
    entry = load_registry(ROOT / "config/model-registry.v1.yaml").model("qwen3_asr_1_7b")
    support = tmp_path / "config/model-worker-support.v1.json"
    support.parent.mkdir(parents=True)
    support.write_bytes((ROOT / "config/model-worker-support.v1.json").read_bytes())
    worker = tmp_path / "workers" / entry.worker
    worker.mkdir(parents=True)
    for filename in REQUIRED_WORKER_FILES:
        (worker / filename).write_text("fixture\n", encoding="utf-8")
    assert worker_implemented(entry, tmp_path) is True

    (worker / "worker.py").unlink()
    assert worker_implemented(entry, tmp_path) is False
    (worker / "worker.py").symlink_to(worker / "adapter.py")
    assert worker_implemented(entry, tmp_path) is False


def test_installability_requires_manifest_worker_lock_and_registry_policy(tmp_path: Path) -> None:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    qwen = registry.model("qwen3_asr_1_7b")
    assert worker_lock_matches(qwen, ROOT) is True
    assert model_installability(qwen, ROOT, manifest_available=False) == (
        False,
        "manifest_unavailable",
    )
    assert model_installability(qwen, ROOT, manifest_available=True) == (True, None)

    worker = tmp_path / "workers" / qwen.worker
    support = tmp_path / "config/model-worker-support.v1.json"
    support.parent.mkdir(parents=True)
    support.write_bytes((ROOT / "config/model-worker-support.v1.json").read_bytes())
    worker.mkdir(parents=True)
    for filename in REQUIRED_WORKER_FILES:
        (worker / filename).write_text("fixture\n", encoding="utf-8")
    assert worker_implemented(qwen, tmp_path) is True
    assert worker_lock_matches(qwen, tmp_path) is False
    assert model_installability(qwen, tmp_path, manifest_available=True) == (
        False,
        "worker_lock_unavailable_or_mismatched",
    )

    disabled = registry.model("whisper_tiny_reference")
    assert model_installability(disabled, ROOT, manifest_available=True) == (
        False,
        "registry_policy_disabled",
    )
