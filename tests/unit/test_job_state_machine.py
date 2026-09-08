from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    CheckpointStatus,
    Job,
    JobCheckpoint,
    JobStage,
    JobStatus,
    Recording,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.jobs.state_machine import CheckpointSpec, JobStateMachine
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_job(factory: sessionmaker[Session]) -> tuple[str, list[str]]:
    machine = JobStateMachine()
    with factory.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="a" * 64,
            source_path=f"jobs/{id(factory)}/source",
            duration_samples=160_000,
            sample_rate=16_000,
            channels=1,
        )
        job = Job(
            recording=recording,
            language_mode=LanguageMode.JAPANESE,
            profile_id="auto_best",
        )
        session.add(job)
        session.flush()
        checkpoints = machine.create_checkpoints(
            session,
            job,
            (
                CheckpointSpec(JobStage.VAD, "segment-0", 0, {"threshold": 0.5}, "s0"),
                CheckpointSpec(JobStage.VAD, "segment-1", 1, {"threshold": 0.5}, "s1"),
            ),
        )
        session.flush()
        return job.id, [checkpoint.id for checkpoint in checkpoints]


def test_restart_resumes_first_incomplete_checkpoint_without_state_loss(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    engine, factory, _ = database
    job_id, checkpoint_ids = make_job(factory)
    machine = JobStateMachine()
    with factory.begin() as session:
        job = session.get_one(Job, job_id)
        first = session.get_one(JobCheckpoint, checkpoint_ids[0])
        second = session.get_one(JobCheckpoint, checkpoint_ids[1])
        machine.start(job)
        machine.begin_checkpoint(job, first)
        machine.complete_checkpoint(job, first)
        machine.begin_checkpoint(job, second)
    engine.dispose()

    from classscribe.db import create_sqlite_engine, make_session_factory

    restarted_engine = create_sqlite_engine(database[2])
    restarted_factory = make_session_factory(restarted_engine)
    try:
        with restarted_factory.begin() as session:
            plans = machine.recovery_scan(session)
            assert len(plans) == 1
            assert plans[0].checkpoint_id == checkpoint_ids[1]
            assert plans[0].attempt_count == 1
        with restarted_factory.begin() as session:
            job = session.get_one(Job, job_id)
            first = session.get_one(JobCheckpoint, checkpoint_ids[0])
            second = session.get_one(JobCheckpoint, checkpoint_ids[1])
            assert job.status is JobStatus.PENDING
            assert first.status is CheckpointStatus.COMPLETED
            assert second.status is CheckpointStatus.RETRYABLE
            machine.start(job)

        machine.run_checkpoint(restarted_factory, job_id, checkpoint_ids[1], lambda *_: None)
        with restarted_factory() as session:
            job = session.get_one(Job, job_id)
            assert job.status is JobStatus.COMPLETED
            assert job.progress == 1.0
    finally:
        restarted_engine.dispose()


def test_worker_crash_retries_only_current_checkpoint(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job_id, checkpoint_ids = make_job(factory)
    machine = JobStateMachine()
    with factory.begin() as session:
        machine.start(session.get_one(Job, job_id))

    def crash(_: Session, __: JobCheckpoint) -> None:
        raise RuntimeError("simulated worker crash containing no user text")

    with pytest.raises(RuntimeError, match="simulated"):
        machine.run_checkpoint(factory, job_id, checkpoint_ids[0], crash)
    with factory.begin() as session:
        job = session.get_one(Job, job_id)
        first = session.get_one(JobCheckpoint, checkpoint_ids[0])
        second = session.get_one(JobCheckpoint, checkpoint_ids[1])
        assert job.status is JobStatus.PENDING
        assert job.error_code == ErrorCode.WORKER_CRASH.value
        assert first.status is CheckpointStatus.RETRYABLE
        assert first.attempt_count == 1
        assert second.status is CheckpointStatus.PENDING
        machine.start(job)
    machine.run_checkpoint(factory, job_id, checkpoint_ids[0], lambda *_: None)


def test_pause_resume_cancel_and_idempotent_cancel(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job_id, _ = make_job(factory)
    machine = JobStateMachine()
    with factory.begin() as session:
        job = session.get_one(Job, job_id)
        machine.start(job)
        machine.pause(job)
        machine.resume(job)
        machine.request_cancel(job)
        assert job.status is JobStatus.CANCELLING
        machine.acknowledge_cancel(job)
        machine.request_cancel(job)
        assert job.status.value == "cancelled"


def test_illegal_transition_and_retry_exhaustion_are_explicit(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job_id, checkpoint_ids = make_job(factory)
    machine = JobStateMachine()
    with factory.begin() as session:
        job = session.get_one(Job, job_id)
        with pytest.raises(ClassScribeError) as illegal:
            machine.pause(job)
        assert illegal.value.code is ErrorCode.JOB_STATE_CONFLICT
        checkpoint = session.get_one(JobCheckpoint, checkpoint_ids[0])
        checkpoint.max_attempts = 1
        machine.start(job)
        machine.begin_checkpoint(job, checkpoint)
        machine.fail_checkpoint(job, checkpoint, code=ErrorCode.WORKER_CRASH, detail="RuntimeError")
        assert job.status is JobStatus.FAILED
        with pytest.raises(ClassScribeError) as exhausted:
            machine.retry(job)
        assert exhausted.value.code is ErrorCode.CHECKPOINT_RETRY_EXHAUSTED


def test_checkpoint_parameter_hash_is_stable_and_unique_per_key(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    _, checkpoint_ids = make_job(factory)
    with factory() as session:
        first = session.get_one(JobCheckpoint, checkpoint_ids[0])
        second = session.get_one(JobCheckpoint, checkpoint_ids[1])
        assert first.parameter_hash == second.parameter_hash
        assert len(first.parameter_hash) == 64
        assert first.checkpoint_key != second.checkpoint_key


def test_worker_failure_does_not_resume_a_user_paused_job(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job_id, checkpoint_ids = make_job(factory)
    machine = JobStateMachine()
    with factory.begin() as session:
        job = session.get_one(Job, job_id)
        checkpoint = session.get_one(JobCheckpoint, checkpoint_ids[0])
        machine.start(job)
        machine.begin_checkpoint(job, checkpoint)
        machine.pause(job)
        machine.fail_checkpoint(
            job, checkpoint, code=ErrorCode.WORKER_CRASH, detail="worker failure"
        )
        assert job.status is JobStatus.PAUSED
        assert checkpoint.status is CheckpointStatus.RETRYABLE
        machine.resume(job)
        assert job.error_code is None


def test_checkpoint_retains_specific_safe_error_details(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    from classscribe.errors import ClassScribeError

    _, factory, _ = database
    job_id, checkpoint_ids = make_job(factory)
    machine = JobStateMachine()
    with factory.begin() as session:
        machine.start(session.get_one(Job, job_id))

    def fail(session: Session, checkpoint: JobCheckpoint) -> None:
        raise ClassScribeError(
            ErrorCode.LID_FAILED,
            "window 10: classifier execution failed at /home/private/model "
            "Authorization: Bearer secret-credential",
        )

    with pytest.raises(ClassScribeError):
        machine.run_checkpoint(factory, job_id, checkpoint_ids[0], fail)
    with factory() as session:
        job = session.get_one(Job, job_id)
        assert job.error_code == ErrorCode.LID_FAILED.value
        assert "classifier execution failed" in (job.error_detail or "")
        assert "secret-credential" not in (job.error_detail or "")
        assert "/home/private" not in (job.error_detail or "")
