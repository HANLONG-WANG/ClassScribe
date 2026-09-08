"""Persistent, re-entrant orchestration for the complete classroom pipeline."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.db.models import (
    CheckpointStatus,
    Job,
    JobCheckpoint,
    JobStage,
    JobStatus,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.jobs.state_machine import CheckpointSpec, JobStateMachine


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    sequence: int
    job_id: str
    kind: str
    occurred_at: str
    payload: dict[str, Any]


class PipelineEventBroker:
    """Thread-safe bounded replay log used by SSE clients."""

    def __init__(self, *, retained_per_job: int = 512) -> None:
        self._retained = retained_per_job
        self._events: dict[str, list[PipelineEvent]] = {}
        self._sequence: dict[str, int] = {}
        self._lock = threading.Lock()

    def publish(self, job_id: str, kind: str, **payload: Any) -> PipelineEvent:
        with self._lock:
            sequence = self._sequence.get(job_id, 0) + 1
            self._sequence[job_id] = sequence
            event = PipelineEvent(
                sequence,
                job_id,
                kind,
                datetime.now(UTC).isoformat(),
                dict(payload),
            )
            events = self._events.setdefault(job_id, [])
            events.append(event)
            del events[: max(0, len(events) - self._retained)]
            return event

    def after(self, job_id: str, sequence: int = 0) -> tuple[PipelineEvent, ...]:
        with self._lock:
            return tuple(
                event for event in self._events.get(job_id, ()) if event.sequence > sequence
            )


class StageRunner(Protocol):
    def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None: ...


StageHandler = Callable[[Session, Job, JobCheckpoint], None]


class ComposableStageRunner:
    """Bind real audio/model/postprocess functions without duplicating pipeline state logic."""

    def __init__(self, handlers: Mapping[str, StageHandler]) -> None:
        self._handlers = dict(handlers)

    def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        handler = self._handlers.get(checkpoint.checkpoint_key)
        if handler is None:
            handler = self._handlers.get(checkpoint.stage.value)
        if handler is None:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT,
                f"no runtime handler registered for {checkpoint.checkpoint_key}",
            )
        handler(session, job, checkpoint)


class SafeBoundaryPause:
    """IBus preemption flag consumed only between natural classroom checkpoints."""

    def __init__(self) -> None:
        self._requested: set[str] = set()
        self._block_all = False
        self._lock = threading.Lock()

    def request(self, job_id: str) -> None:
        with self._lock:
            self._requested.add(job_id)

    def consume(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._requested:
                return False
            self._requested.remove(job_id)
            return True

    def block_all(self) -> None:
        with self._lock:
            self._block_all = True

    def release_all(self) -> None:
        with self._lock:
            self._block_all = False
            self._requested.clear()

    @property
    def blocks_new_dispatch(self) -> bool:
        with self._lock:
            return self._block_all


GLOBAL_CHECKPOINTS = (
    (JobStage.AUDIO_IMPORT, "upload_validate", 0),
    (JobStage.AUDIO_IMPORT, "normalize_audio_master", 1),
    (JobStage.AUDIO_QC, "audio_qc", 0),
    (JobStage.VAD, "vad", 0),
    (JobStage.LID, "lid", 0),
    (JobStage.STRUCTURE, "moss_structure", 0),
    (JobStage.EXPORT, "automatic_exports", 0),
)
SEGMENT_CHECKPOINTS = (
    (JobStage.TRANSCRIPTION, "primary_asr", 0),
    (JobStage.QUALITY, "quality_and_review", 0),
    (JobStage.POSTPROCESS, "terminology", 0),
    (JobStage.POSTPROCESS, "punctuation", 1),
    (JobStage.ALIGNMENT, "forced_alignment", 0),
    (JobStage.ALIGNMENT, "final_validation", 1),
)


class ClassroomPipeline:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        runner: StageRunner,
        *,
        broker: PipelineEventBroker | None = None,
        preemption: SafeBoundaryPause | None = None,
    ) -> None:
        self.sessions = sessions
        self.runner = runner
        self.state = JobStateMachine()
        self.broker = broker or PipelineEventBroker()
        self.preemption = preemption or SafeBoundaryPause()
        self._tasks: set[asyncio.Task[None]] = set()
        self._startup_recovered = False
        self._preempted_jobs: set[str] = set()
        self._deferred_jobs: set[str] = set()
        self._preempted_lock = threading.Lock()

    def preflight(self, parameters: Mapping[str, Any], session: Session | None = None) -> None:
        """Reject jobs before persistence when production prerequisites are absent."""

        check = getattr(self.runner, "preflight", None)
        if check is not None:
            check(parameters, session)

    def initialize(self, job_id: str, parameters: Mapping[str, Any]) -> None:
        with self.sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "job does not exist")
            if job.checkpoints:
                return
            self.state.create_checkpoints(
                session,
                job,
                (
                    CheckpointSpec(stage, key, position, dict(parameters))
                    for stage, key, position in GLOBAL_CHECKPOINTS
                ),
            )
        self.broker.publish(job_id, "pipeline_initialized", stage=JobStage.CREATED.value)

    def start_recovered_jobs(self) -> None:
        """Run once before serving requests; never scan beneath an active executor."""
        if self._startup_recovered or self._tasks:
            return
        job_ids = self.recover()
        self._startup_recovered = True
        for job_id in job_ids:
            self.schedule(job_id)

    def recover(self) -> tuple[str, ...]:
        with self.sessions.begin() as session:
            plans = self.state.recovery_scan(session)
        for plan in plans:
            self.broker.publish(
                plan.job_id,
                "pipeline_recovered",
                stage=plan.stage.value,
                checkpoint_key=plan.checkpoint_key,
                attempt_count=plan.attempt_count,
            )
        return tuple(plan.job_id for plan in plans)

    def run_until_blocked(self, job_id: str) -> None:
        while self.run_next(job_id):
            pass

    def run_next(self, job_id: str) -> bool:
        with self.sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "job does not exist")
            if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED}:
                return False
            if job.status is JobStatus.CANCELLING:
                self.state.acknowledge_cancel(job)
                self.broker.publish(job_id, "job_cancelled", stage=job.stage.value)
                return False
            if self.preemption.consume(job_id) or self.preemption.blocks_new_dispatch:
                event_kind = "job_paused"
                if job.status is JobStatus.RUNNING:
                    self.state.pause(job)
                    with self._preempted_lock:
                        self._preempted_jobs.add(job_id)
                elif job.status is JobStatus.PENDING:
                    with self._preempted_lock:
                        self._deferred_jobs.add(job_id)
                    event_kind = "job_deferred"
                self.broker.publish(
                    job_id,
                    event_kind,
                    stage=job.stage.value,
                    reason="ibus_preempted_at_safe_segment_boundary",
                )
                return False
            if job.status is JobStatus.PAUSED:
                return False
            if job.status is JobStatus.PENDING:
                self.state.start(job)
            checkpoint = self.state.first_incomplete(job)
            if checkpoint is None:
                return False
            checkpoint_id = checkpoint.id
            stage = checkpoint.stage.value
            key = checkpoint.checkpoint_key
            segment_id = checkpoint.segment_id
        self.broker.publish(
            job_id,
            "checkpoint_started",
            stage=stage,
            checkpoint_key=key,
            segment_id=segment_id,
        )

        def operation(session: Session, checkpoint: JobCheckpoint) -> None:
            self.runner.run(session, session.get_one(Job, job_id), checkpoint)

        try:
            self.state.run_checkpoint(self.sessions, job_id, checkpoint_id, operation)
        except Exception as exc:
            self.broker.publish(
                job_id,
                "checkpoint_failed",
                stage=stage,
                checkpoint_key=key,
                segment_id=segment_id,
                error=type(exc).__name__,
            )
            return False
        if key == "moss_structure":
            self._ensure_segment_checkpoints(job_id)
        snapshot = self.snapshot(job_id)
        self.broker.publish(
            job_id,
            "checkpoint_completed",
            stage=stage,
            checkpoint_key=key,
            segment_id=segment_id,
            progress=snapshot["progress"],
        )
        if snapshot["status"] == JobStatus.COMPLETED.value:
            self.broker.publish(job_id, "job_completed", stage=JobStage.COMPLETED.value)
            return False
        return True

    def schedule(self, job_id: str) -> None:
        loop = asyncio.get_running_loop()

        async def execute() -> None:
            await asyncio.to_thread(self.run_until_blocked, job_id)

        task = loop.create_task(execute(), name=f"classscribe-job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def publish_telemetry(
        self,
        job_id: str,
        *,
        model_id: str,
        segment_ordinal: int,
        realtime_factor: float,
        vram_mb: float,
    ) -> None:
        self.broker.publish(
            job_id,
            "runtime_telemetry",
            model_id=model_id,
            segment_ordinal=segment_ordinal,
            realtime_factor=realtime_factor,
            vram_mb=vram_mb,
        )

    def pause(self, job_id: str) -> None:
        with self.sessions.begin() as session:
            job = session.get_one(Job, job_id)
            self.state.pause(job)
        self.broker.publish(job_id, "job_paused", reason="user_requested")

    def resume(self, job_id: str) -> None:
        with self.sessions.begin() as session:
            job = session.get_one(Job, job_id)
            self.state.resume(job)
        self.broker.publish(job_id, "job_resumed")

    def cancel(self, job_id: str) -> None:
        with self.sessions.begin() as session:
            job = session.get_one(Job, job_id)
            self.state.request_cancel(job)
        self.broker.publish(job_id, "job_cancelling")

    def retry_segment(self, job_id: str, segment_id: str) -> int:
        with self.sessions.begin() as session:
            job = session.get_one(Job, job_id)
            segment = session.get(TranscriptSegment, segment_id)
            if segment is None or segment.job_id != job_id or not segment.is_active:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "active job segment not found")
            checkpoints = tuple(
                session.scalars(
                    select(JobCheckpoint).where(
                        JobCheckpoint.job_id == job_id,
                        JobCheckpoint.segment_id == segment_id,
                    )
                ).all()
            )
            for checkpoint in checkpoints:
                checkpoint.status = CheckpointStatus.PENDING
                checkpoint.attempt_count = 0
                checkpoint.error_code = None
                checkpoint.error_detail = None
            job.status = JobStatus.PENDING
            job.error_code = None
            job.error_detail = None
        self.broker.publish(job_id, "segment_retry_queued", segment_id=segment_id)
        return len(checkpoints)

    def refresh_segments(self, job_id: str) -> None:
        self._ensure_segment_checkpoints(job_id)

    def request_safe_preemption(self) -> tuple[str, ...]:
        """Request every running classroom job to yield at its next checkpoint boundary."""

        self.preemption.block_all()
        with self.sessions() as session:
            identifiers = tuple(
                session.scalars(select(Job.id).where(Job.status == JobStatus.RUNNING)).all()
            )
        for job_id in identifiers:
            self.preemption.request(job_id)
            self.broker.publish(job_id, "preemption_requested", reason="ibus_dictation")
        return identifiers

    async def arm_dictation_at_safe_boundary(
        self, *, timeout_seconds: float = 30.0
    ) -> tuple[str, ...]:
        """Stop new classroom dispatch and wait until active work has yielded."""

        identifiers = self.request_safe_preemption()
        async with asyncio.timeout(timeout_seconds):
            while True:
                with self.sessions() as session:
                    running = session.scalar(
                        select(func.count()).select_from(Job).where(Job.status == JobStatus.RUNNING)
                    )
                if not running:
                    return identifiers
                await asyncio.sleep(0.01)

    def resume_preempted(self) -> tuple[str, ...]:
        """Resume only jobs actually paused by IBus, never jobs the user paused."""

        self.preemption.release_all()
        with self._preempted_lock:
            identifiers = tuple(self._preempted_jobs | self._deferred_jobs)
            self._preempted_jobs.clear()
            self._deferred_jobs.clear()
        resumed: list[str] = []
        for job_id in identifiers:
            with self.sessions.begin() as session:
                job = session.get(Job, job_id)
                if job is None or job.status not in {JobStatus.PAUSED, JobStatus.PENDING}:
                    continue
                event_kind = "job_dispatch_resumed"
                if job.status is JobStatus.PAUSED:
                    self.state.resume(job)
                    event_kind = "job_resumed"
            self.broker.publish(job_id, event_kind, reason="ibus_dictation_finished")
            self.schedule(job_id)
            resumed.append(job_id)
        return tuple(resumed)

    def snapshot(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get_one(Job, job_id)
            current = self.state.first_incomplete(job)
            telemetry = next(
                (
                    event.payload
                    for event in reversed(self.broker.after(job_id))
                    if event.kind == "runtime_telemetry"
                ),
                {},
            )
            return {
                "job_id": job.id,
                "status": job.status.value,
                "stage": job.stage.value,
                "progress": job.progress,
                "current_checkpoint": current.checkpoint_key if current else None,
                "current_segment_id": current.segment_id if current else None,
                "error_code": job.error_code,
                "error_detail": job.error_detail,
                "runtime": telemetry,
            }

    def _ensure_segment_checkpoints(self, job_id: str) -> None:
        with self.sessions.begin() as session:
            job = session.get_one(Job, job_id)
            existing = {
                (checkpoint.segment_id, checkpoint.checkpoint_key) for checkpoint in job.checkpoints
            }
            segments = tuple(
                session.scalars(
                    select(TranscriptSegment)
                    .where(
                        TranscriptSegment.job_id == job_id,
                        TranscriptSegment.is_active.is_(True),
                    )
                    .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
                ).all()
            )
            specs: list[CheckpointSpec] = []
            for ordinal, segment in enumerate(segments):
                for stage, key, offset in SEGMENT_CHECKPOINTS:
                    if (segment.id, key) not in existing:
                        specs.append(
                            CheckpointSpec(
                                stage,
                                key,
                                ordinal * 10 + offset,
                                {"segment_id": segment.id, "pipeline_version": 1},
                                segment_id=segment.id,
                            )
                        )
            self.state.create_checkpoints(session, job, specs)
