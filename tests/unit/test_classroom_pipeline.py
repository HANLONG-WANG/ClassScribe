from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from classscribe.classroom import ClassroomPipeline, ComposableStageRunner, SafeBoundaryPause
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    CheckpointStatus,
    Job,
    JobCheckpoint,
    JobStatus,
    Recording,
    TranscriptSegment,
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


def make_job(factory: sessionmaker[Session]) -> str:
    with factory.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="a" * 64,
            source_path=f"jobs/{uuid4()}/source.wav",
            duration_samples=640_000,
            sample_rate=16_000,
            channels=1,
        )
        job = Job(
            recording=recording,
            language_mode=LanguageMode.JAPANESE,
            profile_id="balanced",
        )
        session.add(job)
        session.flush()
        return job.id


def test_full_pipeline_is_dynamic_reentrant_and_auto_exports(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job_id = make_job(factory)
    calls: list[tuple[str, str | None]] = []

    def handle(session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        calls.append((checkpoint.checkpoint_key, checkpoint.segment_id))
        if checkpoint.checkpoint_key == "moss_structure":
            session.add_all(
                (
                    TranscriptSegment(
                        job_id=job.id,
                        start_sample=0,
                        end_sample=320_000,
                        speaker_id="speaker-1",
                        language=LanguageMode.JAPANESE,
                    ),
                    TranscriptSegment(
                        job_id=job.id,
                        start_sample=320_000,
                        end_sample=640_000,
                        speaker_id="speaker-2",
                        language=LanguageMode.JAPANESE,
                    ),
                )
            )

    pipeline = ClassroomPipeline(
        factory,
        ComposableStageRunner(
            {
                stage: handle
                for stage in (
                    "audio_import",
                    "audio_qc",
                    "vad",
                    "lid",
                    "structure",
                    "transcription",
                    "quality",
                    "postprocess",
                    "alignment",
                    "export",
                )
            }
        ),
    )
    pipeline.initialize(job_id, {"outputs": ["json", "md", "srt", "vtt"]})
    pipeline.run_until_blocked(job_id)

    with factory() as session:
        job = session.get_one(Job, job_id)
        checkpoints = tuple(
            session.scalars(select(JobCheckpoint).where(JobCheckpoint.job_id == job_id))
        )
        assert job.status is JobStatus.COMPLETED
        assert job.progress == 1.0
        assert len(checkpoints) == 19
        assert all(item.status is CheckpointStatus.COMPLETED for item in checkpoints)
    assert calls[-1] == ("automatic_exports", None)
    assert sum(key == "primary_asr" for key, _ in calls) == 2
    assert pipeline.snapshot(job_id)["status"] == "completed"
    assert any(item.kind == "job_completed" for item in pipeline.broker.after(job_id))

    pipeline.run_until_blocked(job_id)
    assert len(calls) == 19


def test_pause_preemption_recovery_and_segment_retry(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job_id = make_job(factory)

    def handle(session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        if checkpoint.checkpoint_key == "moss_structure":
            session.add(
                TranscriptSegment(
                    job_id=job.id,
                    start_sample=0,
                    end_sample=640_000,
                    speaker_id="speaker-1",
                    language=LanguageMode.JAPANESE,
                )
            )

    preemption = SafeBoundaryPause()
    pipeline = ClassroomPipeline(
        factory,
        ComposableStageRunner(
            {
                stage: handle
                for stage in (
                    "audio_import",
                    "audio_qc",
                    "vad",
                    "lid",
                    "structure",
                    "transcription",
                    "quality",
                    "postprocess",
                    "alignment",
                    "export",
                )
            }
        ),
        preemption=preemption,
    )
    pipeline.initialize(job_id, {})
    assert pipeline.run_next(job_id)
    preemption.request(job_id)
    assert not pipeline.run_next(job_id)
    assert pipeline.snapshot(job_id)["status"] == "paused"
    assert pipeline.broker.after(job_id)[-1].payload["reason"].startswith("ibus_preempted")
    pipeline.resume(job_id)
    pipeline.run_until_blocked(job_id)

    with factory() as session:
        segment_id = session.scalar(
            select(TranscriptSegment.id).where(TranscriptSegment.job_id == job_id)
        )
        assert segment_id is not None
    assert pipeline.retry_segment(job_id, segment_id) == 6
    with factory() as session:
        pending = tuple(
            session.scalars(
                select(JobCheckpoint).where(
                    JobCheckpoint.segment_id == segment_id,
                    JobCheckpoint.status == CheckpointStatus.PENDING,
                )
            )
        )
        assert len(pending) == 6
        assert session.get_one(Job, job_id).status is JobStatus.PENDING
    assert pipeline.recover() == (job_id,)
    pipeline.run_until_blocked(job_id)
    assert pipeline.snapshot(job_id)["status"] == "completed"


def test_dictation_arming_waits_for_safe_boundary_and_defers_new_jobs(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    async def scenario() -> None:
        _, factory, _ = database
        first_id = make_job(factory)
        pipeline = ClassroomPipeline(
            factory,
            ComposableStageRunner(
                {
                    stage: lambda _session, _job, _checkpoint: None
                    for stage in (
                        "audio_import",
                        "audio_qc",
                        "vad",
                        "lid",
                        "structure",
                        "transcription",
                        "quality",
                        "postprocess",
                        "alignment",
                        "export",
                    )
                }
            ),
        )
        pipeline.initialize(first_id, {})
        assert pipeline.run_next(first_id)

        armed = asyncio.create_task(pipeline.arm_dictation_at_safe_boundary(timeout_seconds=0.5))
        await asyncio.sleep(0.02)
        assert not armed.done()
        assert not pipeline.run_next(first_id)
        assert await armed == (first_id,)
        assert pipeline.snapshot(first_id)["status"] == "paused"

        second_id = make_job(factory)
        pipeline.initialize(second_id, {})
        assert not pipeline.run_next(second_id)
        assert pipeline.snapshot(second_id)["status"] == "pending"
        assert pipeline.broker.after(second_id)[-1].kind == "job_deferred"

        assert set(pipeline.resume_preempted()) == {first_id, second_id}
        for _ in range(100):
            if all(
                pipeline.snapshot(identifier)["status"] == "completed"
                for identifier in (first_id, second_id)
            ):
                break
            await asyncio.sleep(0.01)
        assert pipeline.snapshot(first_id)["status"] == "completed"
        assert pipeline.snapshot(second_id)["status"] == "completed"

    asyncio.run(scenario())


def test_application_lifespan_recovers_once_and_preserves_pause(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    from types import SimpleNamespace
    from typing import Any, cast

    from classscribe.api.app import create_app

    _, factory, _ = database
    pending = make_job(factory)
    paused = make_job(factory)
    pipeline = ClassroomPipeline(factory, ComposableStageRunner({}))
    pipeline.initialize(pending, {})
    pipeline.initialize(paused, {})
    with factory.begin() as session:
        session.get_one(Job, paused).status = JobStatus.PAUSED
        job = session.get_one(Job, pending)
        job.status = JobStatus.RUNNING
        job.checkpoints[0].status = CheckpointStatus.RUNNING
        job.checkpoints[0].attempt_count = 1
        checkpoint_id = job.checkpoints[0].id
    scheduled: list[str] = []
    pipeline.schedule = scheduled.append  # type: ignore[method-assign,assignment]
    app = create_app(service=cast(Any, SimpleNamespace(pipeline=pipeline)))

    async def scenario() -> None:
        for _ in range(2):
            async with app.router.lifespan_context(app):
                pass

    asyncio.run(scenario())
    assert scheduled == [pending]
    with factory() as session:
        assert session.get_one(Job, paused).status is JobStatus.PAUSED
        assert session.get_one(JobCheckpoint, checkpoint_id).status is CheckpointStatus.RETRYABLE


def test_activity_snapshot_is_visible_before_checkpoint_transaction_commits(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    from classscribe.activity import report_activity

    _, factory, _ = database
    job_id = make_job(factory)
    snapshots = []

    def handler(session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        # Hold an uncommitted write while a separate session reads the activity.
        job.error_detail = "uncommitted"
        session.flush()
        report_activity("verify_model", completed=123, total=456, force=True)
        snapshots.append(pipeline.snapshot(job.id))

    pipeline = ClassroomPipeline(factory, ComposableStageRunner({"audio_import": handler}))
    pipeline.initialize(job_id, {})
    pipeline.run_next(job_id)
    assert snapshots[0]["activity"]["completed"] == 123
    assert snapshots[0]["activity"]["attempt"] == 1
    assert snapshots[0]["error_detail"] != "uncommitted"
    assert pipeline.snapshot(job_id)["activity"] is None
