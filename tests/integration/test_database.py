from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from classscribe.contracts import LanguageMode
from classscribe.db.models import Job, Recording, TranscriptSegment
from classscribe.db.session import sqlite_pragmas
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

EXPECTED_TABLES = {
    "app_settings",
    "asr_candidates",
    "benchmark_items",
    "benchmark_runs",
    "decision_events",
    "dictation_sessions",
    "export_artifacts",
    "glossaries",
    "glossary_materials",
    "glossary_terms",
    "job_checkpoints",
    "jobs",
    "language_spans",
    "model_installations",
    "profile_settings",
    "recordings",
    "speaker_display_names",
    "speaker_spans",
    "speech_regions",
    "structure_segments",
    "token_spans",
    "transcript_segments",
}
REQUIRED_COLUMNS = {
    "recordings": {
        "id",
        "source_name",
        "source_sha256",
        "source_path",
        "duration_samples",
        "sample_rate",
        "channels",
        "created_at",
        "audio_qc_json",
    },
    "jobs": {
        "id",
        "recording_id",
        "language_mode",
        "profile_id",
        "status",
        "stage",
        "progress",
        "error_code",
        "error_detail",
        "created_at",
        "started_at",
        "completed_at",
        "options_json",
    },
    "speech_regions": {
        "id",
        "job_id",
        "start_sample",
        "end_sample",
        "vad_score",
        "acoustic_class",
        "source",
    },
    "language_spans": {
        "id",
        "job_id",
        "start_sample",
        "end_sample",
        "language",
        "confidence_raw",
        "decision_json",
    },
    "speaker_spans": {
        "id",
        "job_id",
        "window_ordinal",
        "start_sample",
        "end_sample",
        "speaker_global_id",
        "speaker_local_id",
        "overlap",
        "source_model",
        "source_revision",
        "confidence",
        "provenance_json",
    },
    "structure_segments": {
        "id",
        "job_id",
        "window_ordinal",
        "start_sample",
        "end_sample",
        "speaker_global_id",
        "speaker_local_id",
        "coarse_text",
        "acoustic_events_json",
        "overlap",
        "exclusive",
        "fallback",
        "source_model",
        "source_revision",
        "confidence_raw",
        "selection_score",
        "text_role",
        "adopted_as_final",
        "provenance_json",
    },
    "speaker_display_names": {
        "id",
        "job_id",
        "speaker_global_id",
        "display_name",
        "identity_scope",
    },
    "transcript_segments": {
        "id",
        "job_id",
        "start_sample",
        "end_sample",
        "speaker_id",
        "language",
        "raw_text",
        "faithful_text",
        "smart_corrected_text",
        "user_text",
        "auto_final_source",
        "quality_score",
        "timing_quality",
        "review_status",
        "version",
        "is_active",
        "supersedes_segment_ids_json",
    },
    "asr_candidates": {
        "id",
        "segment_id",
        "model_id",
        "model_revision",
        "raw_text",
        "normalized_text",
        "confidence_raw",
        "confidence_calibrated",
        "quality_features_json",
        "warnings_json",
        "decode_config_json",
        "inference_metrics_json",
    },
    "token_spans": {
        "id",
        "candidate_id",
        "segment_id",
        "start_sample",
        "end_sample",
        "token",
        "normalized_token",
        "confidence",
        "provenance_json",
    },
    "decision_events": {
        "id",
        "segment_id",
        "event_type",
        "input_json",
        "output_json",
        "rule_version",
        "created_at",
    },
    "glossary_terms": {
        "canonical",
        "reading",
        "aliases",
        "language",
        "weight",
        "source",
        "user_confirmed",
    },
    "model_installations": {
        "repository",
        "revision",
        "local_path",
        "sha256",
        "environment_json",
        "measured_vram_mb",
        "health_status",
    },
    "benchmark_runs": {
        "manifest_version",
        "hardware_json",
        "parameters_json",
        "metrics_json",
        "ranking_json",
    },
    "benchmark_items": {
        "gold_json",
        "prediction_json",
        "metrics_json",
        "model_id",
        "model_revision",
    },
    "dictation_sessions": {
        "language_mode",
        "profile_id",
        "status",
        "retain_history",
        "committed_text",
        "audio_path",
    },
    "glossary_materials": {
        "glossary_id",
        "source_name",
        "source_kind",
        "file_id",
        "relative_path",
        "sha256",
        "suggestion_count",
    },
    "export_artifacts": {
        "job_id",
        "output_format",
        "text_layer",
        "view",
        "file_name",
        "relative_path",
        "content_type",
        "sha256",
        "size_bytes",
    },
    "profile_settings": {
        "language",
        "scenario",
        "config_json",
        "benchmark_run_id",
        "version",
    },
    "app_settings": {"key", "value_json", "updated_at"},
}


def test_complete_schema_and_sqlite_safety_pragmas(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    engine, _, _ = database
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    inspector = inspect(engine)
    for table, columns in REQUIRED_COLUMNS.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        assert actual >= columns, f"{table} is missing {sorted(columns - actual)}"
    pragmas = sqlite_pragmas(engine)
    assert pragmas["journal_mode"] == "wal"
    assert pragmas["foreign_keys"] == 1
    assert pragmas["synchronous"] == 2


def test_foreign_keys_and_timeline_checks_are_enforced(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(
            Job(
                recording_id="00000000-0000-0000-0000-000000000000",
                language_mode=LanguageMode.JAPANESE,
                profile_id="auto_best",
            )
        )
    recording = Recording(
        source_name="lecture.wav",
        source_sha256="a" * 64,
        source_path="jobs/source",
        duration_samples=16_000,
        sample_rate=16_000,
        channels=1,
    )
    job = Job(
        recording=recording,
        language_mode=LanguageMode.JAPANESE,
        profile_id="auto_best",
    )
    with factory.begin() as session:
        session.add(job)
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(
            TranscriptSegment(
                job_id=job.id,
                start_sample=2,
                end_sample=1,
                language=LanguageMode.JAPANESE,
            )
        )


def test_alembic_initial_migration_round_trip_and_model_match(tmp_path: Path) -> None:
    database_path = tmp_path / "migrated.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    from sqlalchemy import create_engine

    migrated_engine = create_engine(f"sqlite:///{database_path}")
    assert set(inspect(migrated_engine).get_table_names()) == EXPECTED_TABLES | {"alembic_version"}
    migrated_engine.dispose()
    command.check(config)
    command.downgrade(config, "base")
    downgraded_engine = create_engine(f"sqlite:///{database_path}")
    assert set(inspect(downgraded_engine).get_table_names()) == {"alembic_version"}
    downgraded_engine.dispose()


def test_all_required_indexes_are_present(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    engine, _, _ = database
    inspector = inspect(engine)
    assert {item["name"] for item in inspector.get_indexes("jobs")} >= {
        "ix_jobs_recovery",
        "ix_jobs_status",
    }
    assert {item["name"] for item in inspector.get_indexes("transcript_segments")} >= {
        "ix_transcript_segments_timeline",
        "ix_transcript_segments_is_active",
    }
    assert {item["name"] for item in inspector.get_indexes("asr_candidates")} >= {
        "ix_asr_candidates_active",
        "ix_asr_candidates_model",
    }


def test_production_upgrade_uses_authoritative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.db.session import create_sqlite_engine, upgrade_schema

    path = tmp_path / "old.sqlite3"
    config = Config()
    config.set_main_option(
        "script_location", str(Path("backend/classscribe/db/migrations").resolve())
    )
    config.attributes["database_url"] = f"sqlite:///{path}"
    command.upgrade(config, "eeec80ee63cf")
    monkeypatch.setenv("CLASSSCRIBE_DATABASE_URL", f"sqlite:///{tmp_path / 'wrong.sqlite3'}")
    engine = create_sqlite_engine(path)
    upgrade_schema(engine)
    assert "options_json" in {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert "is_active" in {c["name"] for c in inspect(engine).get_columns("transcript_segments")}
    assert not (tmp_path / "wrong.sqlite3").exists()
    upgrade_schema(engine)
    engine.dispose()
