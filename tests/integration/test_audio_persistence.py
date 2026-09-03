from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.audio import AudioArtifactRepository, LanguageRoutingSpan, SpeechRegionResult
from classscribe.contracts import LanguageMode
from classscribe.db import create_sqlite_engine, make_session_factory
from classscribe.db.models import (
    CheckpointStatus,
    Job,
    JobCheckpoint,
    JobStage,
    JobStatus,
    LanguageSpan,
    Recording,
    SpeechRegion,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


def create_job(sessions: sessionmaker[Session], duration: int) -> str:
    with sessions.begin() as session:
        recording = Recording(
            source_name="class.wav",
            source_sha256="a" * 64,
            source_path="/tmp/class.wav",
            duration_samples=duration,
            sample_rate=SAMPLE_RATE,
            channels=1,
            audio_qc_json={},
        )
        session.add(recording)
        session.flush()
        job = Job(
            recording_id=recording.id,
            language_mode=LanguageMode.AUTO_MIXED,
            profile_id="classroom",
            status=JobStatus.RUNNING,
            stage=JobStage.AUDIO_IMPORT,
            progress=0,
        )
        session.add(job)
        session.flush()
        return job.id


def test_speech_and_language_artifacts_replace_idempotently_with_raw_decisions(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, sessions, _ = database
    duration = 30 * SAMPLE_RATE
    job_id = create_job(sessions, duration)
    repository = AudioArtifactRepository(sessions)
    regions = (
        SpeechRegionResult(AudioSpan(1_000, 40_000), 0.91, "speech", "firered_vad"),
        SpeechRegionResult(AudioSpan(50_000, duration), 0.88, "speech", "firered_vad"),
    )
    languages = (
        LanguageRoutingSpan(
            AudioSpan(0, 15 * SAMPLE_RATE),
            LanguageMode.JAPANESE,
            0.87,
            {"observations": [{"ja": 0.87}], "rule": "two_windows"},
        ),
        LanguageRoutingSpan(
            AudioSpan(15 * SAMPLE_RATE, duration),
            LanguageMode.ENGLISH,
            0.83,
            {"observations": [{"en": 0.83}], "transition_from": "ja"},
        ),
    )
    repository.replace_speech_regions(job_id, regions)
    repository.replace_language_spans(job_id, languages)
    repository.replace_speech_regions(job_id, regions)
    repository.replace_language_spans(job_id, languages)

    with sessions() as session:
        stored_regions = session.scalars(
            select(SpeechRegion)
            .where(SpeechRegion.job_id == job_id)
            .order_by(SpeechRegion.start_sample)
        ).all()
        stored_languages = session.scalars(
            select(LanguageSpan)
            .where(LanguageSpan.job_id == job_id)
            .order_by(LanguageSpan.start_sample)
        ).all()
        assert [(item.start_sample, item.end_sample) for item in stored_regions] == [
            (item.span.start_sample, item.span.end_sample) for item in regions
        ]
        assert [item.language for item in stored_languages] == [
            LanguageMode.JAPANESE,
            LanguageMode.ENGLISH,
        ]
        assert stored_languages[1].decision_json["transition_from"] == "ja"
        assert stored_languages[0].confidence_raw == 0.87


def test_out_of_range_or_nonmonotonic_artifacts_rollback_without_data_loss(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, sessions, _ = database
    duration = 10 * SAMPLE_RATE
    job_id = create_job(sessions, duration)
    repository = AudioArtifactRepository(sessions)
    original = (SpeechRegionResult(AudioSpan(0, duration), 0.9, "speech", "firered_vad"),)
    repository.replace_speech_regions(job_id, original)

    with pytest.raises(ClassScribeError) as outside:
        repository.replace_speech_regions(
            job_id,
            (SpeechRegionResult(AudioSpan(0, duration + 1), 0.9, "speech", "firered_vad"),),
        )
    assert outside.value.code is ErrorCode.AUDIO_TIMELINE_INVALID
    with pytest.raises(ClassScribeError) as reverse:
        repository.replace_speech_regions(
            job_id,
            (
                SpeechRegionResult(AudioSpan(20, 30), 0.9, "speech", "firered_vad"),
                SpeechRegionResult(AudioSpan(0, 10), 0.9, "speech", "firered_vad"),
            ),
        )
    assert reverse.value.code is ErrorCode.AUDIO_TIMELINE_INVALID
    with sessions() as session:
        stored = session.scalars(select(SpeechRegion).where(SpeechRegion.job_id == job_id)).all()
        assert len(stored) == 1
        assert stored[0].end_sample == duration


def test_restart_resumes_first_incomplete_audio_checkpoint(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    engine, sessions, path = database
    job_id = create_job(sessions, 90 * 60 * SAMPLE_RATE)
    with sessions.begin() as session:
        session.add_all(
            (
                JobCheckpoint(
                    job_id=job_id,
                    stage=JobStage.AUDIO_IMPORT,
                    checkpoint_key="canonical_master",
                    position=0,
                    status=CheckpointStatus.COMPLETED,
                    parameter_hash="a" * 64,
                ),
                JobCheckpoint(
                    job_id=job_id,
                    stage=JobStage.AUDIO_QC,
                    checkpoint_key="quality_report",
                    position=1,
                    status=CheckpointStatus.COMPLETED,
                    parameter_hash="b" * 64,
                ),
            )
        )
    engine.dispose()
    restarted_engine = create_sqlite_engine(path)
    restarted_sessions = make_session_factory(restarted_engine)
    try:
        repository = AudioArtifactRepository(restarted_sessions)
        assert repository.next_audio_stage(job_id) == (JobStage.VAD, "speech_regions")
    finally:
        restarted_engine.dispose()
