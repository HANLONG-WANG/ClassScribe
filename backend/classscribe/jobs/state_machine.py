"""Re-entrant persistent job and per-segment checkpoint state machine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.db.models import (
    CheckpointStatus,
    Job,
    JobCheckpoint,
    JobStage,
    JobStatus,
)
from classscribe.errors import ClassScribeError, ErrorCode, public_error_detail

STAGE_ORDER = {stage: index for index, stage in enumerate(JobStage)}
TERMINAL_JOB_STATUSES = {JobStatus.CANCELLED, JobStatus.COMPLETED}


@dataclass(frozen=True, slots=True)
class CheckpointSpec:
    stage: JobStage
    checkpoint_key: str
    position: int
    parameters: dict[str, Any]
    segment_id: str | None = None
    max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    job_id: str
    stage: JobStage
    checkpoint_id: str
    checkpoint_key: str
    segment_id: str | None
    attempt_count: int
    max_attempts: int


def parameter_hash(parameters: dict[str, Any]) -> str:
    canonical = json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CheckpointInterrupted(Exception):
    """A cooperative safe boundary; this is not a failed model attempt."""


class JobStateMachine:
    def create_checkpoints(
        self, session: Session, job: Job, specs: Iterable[CheckpointSpec]
    ) -> list[JobCheckpoint]:
        checkpoints: list[JobCheckpoint] = []
        for spec in specs:
            if spec.max_attempts < 1:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "checkpoint max_attempts must be positive"
                )
            checkpoint = JobCheckpoint(
                job_id=job.id,
                stage=spec.stage,
                checkpoint_key=spec.checkpoint_key,
                segment_id=spec.segment_id,
                position=spec.position,
                parameter_hash=parameter_hash(spec.parameters),
                max_attempts=spec.max_attempts,
            )
            session.add(checkpoint)
            checkpoints.append(checkpoint)
        return checkpoints

    def start(self, job: Job) -> None:
        if job.status not in {JobStatus.PENDING, JobStatus.FAILED}:
            raise self._state_error(job, "start")
        job.status = JobStatus.RUNNING
        job.error_code = None
        job.error_detail = None
        job.started_at = job.started_at or datetime.now(UTC)

    def pause(self, job: Job) -> None:
        if job.status is not JobStatus.RUNNING:
            raise self._state_error(job, "pause")
        job.status = JobStatus.PAUSED

    def resume(self, job: Job) -> None:
        if job.status is not JobStatus.PAUSED:
            raise self._state_error(job, "resume")
        job.status = JobStatus.RUNNING
        job.error_code = None
        job.error_detail = None

    def request_cancel(self, job: Job) -> None:
        if job.status in TERMINAL_JOB_STATUSES:
            return
        if job.status in {JobStatus.PENDING, JobStatus.PAUSED, JobStatus.FAILED}:
            job.status = JobStatus.CANCELLED
            job.completed_at = datetime.now(UTC)
        elif job.status is JobStatus.RUNNING:
            job.status = JobStatus.CANCELLING
        else:
            raise self._state_error(job, "cancel")

    def acknowledge_cancel(self, job: Job, checkpoint: JobCheckpoint | None = None) -> None:
        if job.status not in {JobStatus.CANCELLING, JobStatus.CANCELLED}:
            raise self._state_error(job, "acknowledge cancel")
        if checkpoint is not None and checkpoint.status is CheckpointStatus.RUNNING:
            checkpoint.status = CheckpointStatus.CANCELLED
            checkpoint.completed_at = datetime.now(UTC)
        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.now(UTC)

    def retry(self, job: Job) -> None:
        if job.status is not JobStatus.FAILED:
            raise self._state_error(job, "retry")
        checkpoint = self.first_incomplete(job)
        if checkpoint is None or checkpoint.attempt_count >= checkpoint.max_attempts:
            raise ClassScribeError(
                ErrorCode.CHECKPOINT_RETRY_EXHAUSTED,
                "the first incomplete checkpoint exhausted its parameter-specific retry limit",
            )
        checkpoint.status = CheckpointStatus.RETRYABLE
        job.status = JobStatus.PENDING
        job.error_code = None
        job.error_detail = None

    def begin_checkpoint(self, job: Job, checkpoint: JobCheckpoint) -> None:
        if job.status is not JobStatus.RUNNING:
            raise self._state_error(job, "begin checkpoint")
        if checkpoint.status is CheckpointStatus.COMPLETED:
            return
        if checkpoint.status not in {CheckpointStatus.PENDING, CheckpointStatus.RETRYABLE}:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT,
                f"checkpoint {checkpoint.id} cannot begin from {checkpoint.status.value}",
            )
        if checkpoint.attempt_count >= checkpoint.max_attempts:
            checkpoint.status = CheckpointStatus.FAILED
            job.status = JobStatus.FAILED
            job.error_code = ErrorCode.CHECKPOINT_RETRY_EXHAUSTED.value
            raise ClassScribeError(
                ErrorCode.CHECKPOINT_RETRY_EXHAUSTED,
                f"checkpoint {checkpoint.id} exhausted retries",
            )
        checkpoint.attempt_count += 1
        checkpoint.status = CheckpointStatus.RUNNING
        checkpoint.started_at = datetime.now(UTC)
        checkpoint.error_code = None
        checkpoint.error_detail = None
        job.stage = checkpoint.stage

    def complete_checkpoint(self, job: Job, checkpoint: JobCheckpoint) -> None:
        if checkpoint.status is CheckpointStatus.COMPLETED:
            return
        if checkpoint.status is not CheckpointStatus.RUNNING:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT,
                f"checkpoint {checkpoint.id} is not running",
            )
        checkpoint.status = CheckpointStatus.COMPLETED
        checkpoint.completed_at = datetime.now(UTC)
        self._update_progress(job)

    def fail_checkpoint(
        self,
        job: Job,
        checkpoint: JobCheckpoint,
        *,
        code: ErrorCode,
        detail: str,
    ) -> None:
        if checkpoint.status is not CheckpointStatus.RUNNING:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT,
                f"checkpoint {checkpoint.id} is not running",
            )
        if job.status in {JobStatus.CANCELLING, JobStatus.CANCELLED}:
            self.acknowledge_cancel(job, checkpoint)
            return
        retryable = checkpoint.attempt_count < checkpoint.max_attempts
        checkpoint.status = CheckpointStatus.RETRYABLE if retryable else CheckpointStatus.FAILED
        checkpoint.error_code = code.value
        checkpoint.error_detail = detail
        job.status = (
            JobStatus.PAUSED
            if retryable and job.status is JobStatus.PAUSED
            else JobStatus.PENDING
            if retryable
            else JobStatus.FAILED
        )
        job.error_code = code.value
        job.error_detail = detail

    def first_incomplete(self, job: Job) -> JobCheckpoint | None:
        candidates = [
            checkpoint
            for checkpoint in job.checkpoints
            if checkpoint.status is not CheckpointStatus.COMPLETED
        ]
        return min(
            candidates,
            key=lambda checkpoint: (STAGE_ORDER[checkpoint.stage], checkpoint.position),
            default=None,
        )

    def recovery_scan(self, session: Session) -> list[RecoveryPlan]:
        statement: Select[tuple[Job]] = select(Job).where(
            Job.status.in_((JobStatus.RUNNING, JobStatus.CANCELLING, JobStatus.PENDING))
        )
        plans: list[RecoveryPlan] = []
        for job in session.scalars(statement).unique():
            if job.status is JobStatus.CANCELLING:
                active_checkpoint = next(
                    (
                        checkpoint
                        for checkpoint in job.checkpoints
                        if checkpoint.status is CheckpointStatus.RUNNING
                    ),
                    None,
                )
                self.acknowledge_cancel(job, active_checkpoint)
                continue
            for checkpoint in job.checkpoints:
                if checkpoint.status is CheckpointStatus.RUNNING:
                    checkpoint.status = (
                        CheckpointStatus.RETRYABLE
                        if checkpoint.attempt_count < checkpoint.max_attempts
                        else CheckpointStatus.FAILED
                    )
            next_checkpoint = self.first_incomplete(job)
            if next_checkpoint is None:
                self._complete_job(job)
                continue
            if next_checkpoint.status is CheckpointStatus.FAILED:
                job.status = JobStatus.FAILED
                job.error_code = ErrorCode.CHECKPOINT_RETRY_EXHAUSTED.value
                continue
            job.status = JobStatus.PENDING
            job.stage = next_checkpoint.stage
            plans.append(
                RecoveryPlan(
                    job_id=job.id,
                    stage=next_checkpoint.stage,
                    checkpoint_id=next_checkpoint.id,
                    checkpoint_key=next_checkpoint.checkpoint_key,
                    segment_id=next_checkpoint.segment_id,
                    attempt_count=next_checkpoint.attempt_count,
                    max_attempts=next_checkpoint.max_attempts,
                )
            )
        return plans

    def run_checkpoint(
        self,
        factory: sessionmaker[Session],
        job_id: str,
        checkpoint_id: str,
        operation: Callable[[Session, JobCheckpoint], None],
    ) -> None:
        with factory.begin() as session:
            job = session.get(Job, job_id)
            checkpoint = session.get(JobCheckpoint, checkpoint_id)
            if job is None or checkpoint is None or checkpoint.job_id != job_id:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "job/checkpoint not found")
            self.begin_checkpoint(job, checkpoint)

        try:
            with factory.begin() as session:
                job = session.get_one(Job, job_id)
                checkpoint = session.get_one(JobCheckpoint, checkpoint_id)
                operation(session, checkpoint)
                session.flush()
                # Controls may have changed the job while a model was running.
                session.refresh(job, attribute_names=["status"])
                self.complete_checkpoint(job, checkpoint)
        except CheckpointInterrupted:
            with factory.begin() as session:
                checkpoint = session.get_one(JobCheckpoint, checkpoint_id)
                checkpoint.status = CheckpointStatus.PENDING
                checkpoint.attempt_count = max(0, checkpoint.attempt_count - 1)
            raise
        except Exception as exc:
            with factory.begin() as session:
                job = session.get_one(Job, job_id)
                checkpoint = session.get_one(JobCheckpoint, checkpoint_id)
                self.fail_checkpoint(
                    job,
                    checkpoint,
                    code=exc.code if isinstance(exc, ClassScribeError) else ErrorCode.WORKER_CRASH,
                    detail=public_error_detail(
                        exc.detail
                        if isinstance(exc, ClassScribeError)
                        else f"{type(exc).__name__}: {exc}"
                    )[:1000],
                )
            raise

    def _update_progress(self, job: Job) -> None:
        total = len(job.checkpoints)
        complete = sum(
            checkpoint.status is CheckpointStatus.COMPLETED for checkpoint in job.checkpoints
        )
        job.progress = complete / total if total else 1.0
        next_checkpoint = self.first_incomplete(job)
        if next_checkpoint is None:
            if job.status in {JobStatus.CANCELLING, JobStatus.CANCELLED}:
                self.acknowledge_cancel(job)
            else:
                self._complete_job(job)
        else:
            job.stage = next_checkpoint.stage

    @staticmethod
    def _complete_job(job: Job) -> None:
        job.status = JobStatus.COMPLETED
        job.stage = JobStage.COMPLETED
        job.progress = 1.0
        job.completed_at = datetime.now(UTC)

    @staticmethod
    def _state_error(job: Job, action: str) -> ClassScribeError:
        return ClassScribeError(
            ErrorCode.JOB_STATE_CONFLICT,
            f"cannot {action} job {job.id} from {job.status.value}",
        )
