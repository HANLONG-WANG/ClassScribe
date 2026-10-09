from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from classscribe.contracts import LanguageMode
from classscribe.db.models import Job, Recording, TranscriptSegment
from classscribe.db.session import sqlite_pragmas
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


@pytest.mark.parametrize("unversioned", [False, True])
def test_online_retry_migration_preserves_old_attempts(tmp_path: Path, unversioned: bool) -> None:
    from datetime import UTC, datetime

    from classscribe.db.models import OnlineRequestAttempt
    from classscribe.db.session import create_sqlite_engine, upgrade_schema
    from sqlalchemy import select, text

    path = tmp_path / "previous-online.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "f2a6c8419d30")
    engine = create_sqlite_engine(path)
    factory = sessionmaker(engine)
    with factory.begin() as session:
        job = Job(
            recording=Recording(
                source_name="old.wav",
                source_path="old.wav",
                source_sha256="a" * 64,
                duration_samples=16000,
                sample_rate=16000,
                channels=1,
            ),
            language_mode=LanguageMode.ENGLISH,
            profile_id="balanced",
        )
        session.add(job)
        session.flush()
        job_id = job.id
        session.execute(
            text(
                "INSERT INTO online_request_attempts "
                "(id,job_id,request_fingerprint,audio_sha256,status,"
                "service_request_id,error_code,created_at,updated_at) "
                "VALUES (:id,:job,:fingerprint,:audio,'failed',"
                "'original-request','MAI_HTTP_503',:now,:now)"
            ),
            {
                "id": "original-attempt",
                "job": job_id,
                "fingerprint": "b" * 64,
                "audio": "a" * 64,
                "now": datetime.now(UTC).isoformat(" "),
            },
        )
        if unversioned:
            session.execute(text("DROP TABLE alembic_version"))
    upgrade_schema(engine)
    upgrade_schema(engine)
    with factory.begin() as session:
        attempt = session.get_one(OnlineRequestAttempt, "original-attempt")
        assert attempt.attempt_number == 1 and attempt.status == "failed"
        assert attempt.service_request_id == "original-request"
        assert attempt.diagnostics_json == {}
        session.add(
            OnlineRequestAttempt(
                job_id=job_id,
                attempt_number=2,
                status="prepared",
                request_fingerprint="b" * 64,
                audio_sha256="a" * 64,
            )
        )
    command.check(config)
    with pytest.raises(RuntimeError, match="manual retries"):
        command.downgrade(config, "f2a6c8419d30")
    with factory() as session:
        assert len(list(session.scalars(select(OnlineRequestAttempt)))) == 2
    engine.dispose()


EXPECTED_TABLES = {
    "online_request_attempts",
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


def _populated_previous_database(path: Path) -> tuple[Config, Engine]:
    from classscribe.db.session import create_sqlite_engine

    config = Config()
    config.set_main_option(
        "script_location", str(Path("backend/classscribe/db/migrations").resolve())
    )
    config.attributes["database_url"] = f"sqlite:///{path}"
    command.upgrade(config, "b12d940ac831")
    engine = create_sqlite_engine(path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO recordings "
            "(id, source_name, source_sha256, source_path, duration_samples, "
            "sample_rate, channels, created_at, audio_qc_json) "
            "VALUES ('recording', 'lecture.wav', ?, 'jobs/source', 16000, "
            "16000, 1, CURRENT_TIMESTAMP, '{}')",
            ("a" * 64,),
        )
        connection.exec_driver_sql(
            "INSERT INTO jobs "
            "(id, recording_id, language_mode, profile_id, status, stage, progress, "
            "created_at, updated_at, options_json) "
            "VALUES ('job', 'recording', 'ja', 'auto_best', 'completed', 'completed', "
            "1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}')"
        )
    with Session(engine) as session:
        session.add(
            TranscriptSegment(
                job_id="job",
                start_sample=0,
                end_sample=16000,
                language=LanguageMode.JAPANESE,
                faithful_text="Existing transcript",
            )
        )
        session.commit()
    return config, engine


@pytest.mark.parametrize("failed_batch_table", [False, True])
def test_populated_recordings_migration_preserves_history(
    tmp_path: Path, failed_batch_table: bool
) -> None:
    from classscribe.db.session import upgrade_schema

    config, engine = _populated_previous_database(tmp_path / "populated.sqlite3")
    with engine.begin() as connection:
        recordings_before = connection.exec_driver_sql("SELECT * FROM recordings").all()
        jobs_before = connection.exec_driver_sql("SELECT * FROM jobs").all()
        transcripts_before = connection.exec_driver_sql("SELECT * FROM transcript_segments").all()
        if failed_batch_table:
            connection.exec_driver_sql(
                "CREATE TABLE _alembic_tmp_recordings AS SELECT * FROM recordings WHERE 0"
            )
    upgrade_schema(engine)
    upgrade_schema(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == ("c7b93a140e52")
        recordings_after = connection.exec_driver_sql("SELECT * FROM recordings").all()
        assert [row[: len(recordings_before[0])] for row in recordings_after] == recordings_before
        assert connection.exec_driver_sql("SELECT * FROM jobs").all() == jobs_before
        assert connection.exec_driver_sql("SELECT * FROM transcript_segments").all() == (
            transcripts_before
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert "_alembic_tmp_recordings" not in inspect(engine).get_table_names()
    assert sqlite_pragmas(engine)["foreign_keys"] == 1
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM recordings WHERE id='recording'")
    command.check(config)
    command.downgrade(config, "b12d940ac831")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT * FROM recordings").all() == recordings_before
        assert connection.exec_driver_sql("SELECT * FROM jobs").all() == jobs_before
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    upgrade_schema(engine)
    engine.dispose()


def test_recordings_migration_preserves_populated_temporary_table(tmp_path: Path) -> None:
    from classscribe.db.session import upgrade_schema

    _, engine = _populated_previous_database(tmp_path / "temporary-data.sqlite3")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE _alembic_tmp_recordings AS SELECT * FROM recordings"
        )
    with pytest.raises(RuntimeError, match="contains temporary data"):
        upgrade_schema(engine)
    assert "parent_recording_id" not in {
        column["name"] for column in inspect(engine).get_columns("recordings")
    }
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM _alembic_tmp_recordings").scalar_one()
            == 1
        )
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == ("b12d940ac831")
    engine.dispose()


@pytest.mark.parametrize("failure", ["exception", "foreign_key"])
def test_recordings_migration_rolls_back_schema_and_data_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from alembic import op
    from classscribe.db.session import upgrade_schema

    _, engine = _populated_previous_database(tmp_path / "rollback.sqlite3")
    create_table = op.create_table

    def fail_after_recordings_rebuild(*args: Any, **kwargs: Any) -> object:
        if args[0] == "online_request_attempts":
            if failure == "exception":
                raise RuntimeError("Injected migration failure")
            op.get_bind().exec_driver_sql("UPDATE jobs SET recording_id='missing' WHERE id='job'")
        return create_table(*args, **kwargs)

    monkeypatch.setattr(op, "create_table", fail_after_recordings_rebuild)
    message = "Injected migration failure" if failure == "exception" else "foreign key constraints"
    with pytest.raises(RuntimeError, match=message):
        upgrade_schema(engine)
    assert "parent_recording_id" not in {
        column["name"] for column in inspect(engine).get_columns("recordings")
    }
    assert "_alembic_tmp_recordings" not in inspect(engine).get_table_names()
    assert "online_request_attempts" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == ("b12d940ac831")
        assert connection.exec_driver_sql("SELECT recording_id FROM jobs").scalar_one() == (
            "recording"
        )
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM transcript_segments").scalar_one() == 1
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert sqlite_pragmas(engine)["foreign_keys"] == 1
    engine.dispose()
