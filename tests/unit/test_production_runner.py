from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from classscribe.classroom import ProductionStageRunner
from classscribe.config import load_config
from classscribe.db.models import BenchmarkRun, BenchmarkStatus, ProfileSetting
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import ModelManager, SandboxedModelInvoker, load_registry
from classscribe.paths import AppPaths
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


class InstalledModels:
    def __init__(self, root: Path, identifiers: set[str]) -> None:
        self.root = root
        self.identifiers = identifiers

    def resolve_for_runtime(self, model_id: str) -> Path:
        return self.installed_revision_metadata(model_id)

    def installed_revision_metadata(self, model_id: str) -> Path:
        if model_id not in self.identifiers:
            raise FileNotFoundError(model_id)
        return self.root / model_id


def runner(tmp_path: Path, installed: set[str]) -> ProductionStageRunner:
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    return ProductionStageRunner(
        paths,
        load_config(environment={}),
        load_registry(Path("config/model-registry.v1.yaml")),
        cast(ModelManager, InstalledModels(tmp_path, installed)),
        cast(SandboxedModelInvoker, object()),
    )


def test_production_runner_binds_every_persistent_checkpoint(tmp_path: Path) -> None:
    value = runner(tmp_path, set())
    assert set(value._handlers) == {
        "upload_validate",
        "normalize_audio_master",
        "audio_qc",
        "vad",
        "lid",
        "moss_structure",
        "primary_asr",
        "quality_and_review",
        "terminology",
        "punctuation",
        "forced_alignment",
        "final_validation",
        "automatic_exports",
    }


def test_preflight_is_fail_closed_for_missing_pinned_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "classscribe.classroom.production.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    value = runner(tmp_path, {"firered_vad", "moss_td_0_9b"})
    with pytest.raises(ClassScribeError) as failure:
        value.preflight({"language": "ja", "primary_model_id": "granite_speech_4_1_2b"})
    assert failure.value.code is ErrorCode.MODEL_NOT_FULLY_INSTALLED
    assert "granite_speech_4_1_2b" in failure.value.detail


def test_preflight_accepts_manual_route_with_exact_installed_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "classscribe.classroom.production.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    value = runner(
        tmp_path,
        {"firered_vad", "moss_td_0_9b", "granite_speech_4_1_2b"},
    )
    value.preflight({"language": "ja"})


def test_auto_best_consumes_only_a_fresh_local_gold_profile(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, sessions, _ = database
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    qwen = registry.model("qwen3_asr_1_7b")
    granite = registry.model("granite_speech_4_1_2b")
    manifest_sha256 = "f" * 64
    calibration = {
        "schema_version": 1,
        "model_id": qwen.id,
        "model_revision": qwen.revision,
        "language": "ja",
        "scenario": "classroom",
        "manifest_sha256": manifest_sha256,
        "method": "isotonic",
        "error_threshold": 0.2,
        "parameters": {"thresholds": [1.0], "values": [0.9]},
        "validation_brier": 0.01,
        "test_brier": 0.02,
        "training_sha256": "e" * 64,
        "created_at": "2026-09-04T00:00:00+00:00",
    }
    with sessions.begin() as session:
        run = BenchmarkRun(
            manifest_version="private-v1",
            status=BenchmarkStatus.COMPLETED,
            hardware_json={"gpu": "local"},
            parameters_json={
                "production_gold": True,
                "real_model_execution": True,
                "synthetic_gold": False,
                "manifest_sha256": manifest_sha256,
                "calibrations": [calibration],
            },
            metrics_json={},
            ranking_json=[
                {
                    "language": "ja",
                    "scenario": "classroom",
                    "models": [qwen.id],
                    "passed": True,
                    "candidates": [
                        {
                            "model_id": qwen.id,
                            "model_revision": qwen.revision,
                            "eligible": True,
                            "metrics": {"normalized_cer": 0.1},
                        }
                    ],
                }
            ],
        )
        session.add(run)
        session.flush()
        session.add(
            ProfileSetting(
                language="ja",
                scenario="classroom",
                config_json={"models": [qwen.id], "history": []},
                benchmark_run_id=run.id,
            )
        )
    value = runner(tmp_path, {qwen.id, granite.id})
    with sessions() as session:
        selected = value._primary_entry({"model_selection": "auto_best"}, "ja", session)
    assert selected.id == qwen.id

    with sessions.begin() as session:
        stored = session.scalar(select(ProfileSetting))
        assert stored is not None
        run = session.get_one(BenchmarkRun, stored.benchmark_run_id)
        stale = dict(run.parameters_json)
        stale["calibrations"] = [{**calibration, "model_revision": "0" * 40}]
        run.parameters_json = stale
    with sessions() as session:
        selected = value._primary_entry({"model_selection": "auto_best"}, "ja", session)
    assert selected.id == granite.id


def test_production_body_routing_excludes_structure_model(tmp_path: Path) -> None:
    value = runner(tmp_path, {"moss_td_0_9b", "firered_vad"})
    assert value._primary_entry({}, "zh").id != "moss_td_0_9b"
    assert not value._fallback_entries({}, "zh", "qwen3_asr_1_7b")
    with pytest.raises(ClassScribeError, match="no compatible"):
        value._primary_entry({"primary_model_id": "moss_td_0_9b"}, "zh")


def test_english_coarse_timing_preserves_words_in_subtitles() -> None:
    from classscribe.classroom.production import _coarse_timing
    from classscribe.exports.models import ExportLayer, ExportSegment, ExportToken
    from classscribe.exports.subtitles import build_subtitle_cues
    from classscribe.timeline import AudioSpan

    span = AudioSpan(0, 32000)
    timing = _coarse_timing("Hello NASA!", span, "en")
    assert [token.text for token in timing.tokens] == ["Hello", "NASA!"]
    segment = ExportSegment(
        "s",
        span,
        "en",
        "Hello NASA!",
        "Hello NASA!",
        "Hello NASA!",
        None,
        tuple(ExportToken(token.text, token.span) for token in timing.tokens),
    )
    assert build_subtitle_cues((segment,), ExportLayer.FAITHFUL)[0].lines == ("Hello NASA!",)


def test_classroom_model_disclosure_does_not_load_or_hash_models(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sessions, _ = database
    value = runner(tmp_path, set())
    monkeypatch.setattr(
        value.manager,
        "resolve_for_runtime",
        lambda *_: pytest.fail("preview must not read model weights"),
    )
    with sessions() as session:
        assert value.classroom_model_order("ja", session) == value.registry.rankings["classroom.ja"]


@pytest.mark.parametrize("language", ["zh", "ja", "en", "auto_mixed"])
@pytest.mark.parametrize("manual", [False, True])
def test_job_creation_preflight_never_scans_model_weights(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    manual: bool,
) -> None:
    monkeypatch.setattr(
        "classscribe.classroom.production.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    value = runner(tmp_path, {"firered_vad", "firered_lid", "moss_td_0_9b", "qwen3_asr_1_7b"})
    monkeypatch.setattr(
        value.manager,
        "resolve_for_runtime",
        lambda *_: pytest.fail("task creation must not hash model files"),
    )
    parameters = {
        "language": language,
        "model_selection": "manual_primary" if manual else "auto_best",
    }
    if manual:
        parameters["primary_model_id"] = "qwen3_asr_1_7b"
    value.preflight(parameters)
