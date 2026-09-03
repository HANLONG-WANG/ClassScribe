"""Short-transaction persistence for canonical stage-4 audio artifacts."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.audio.lid import LanguageRoutingSpan
from classscribe.audio.media import AudioMaster, ImportedMedia
from classscribe.audio.qc import AudioQualityReport
from classscribe.audio.vad import SpeechRegionResult
from classscribe.db.models import (
    CheckpointStatus,
    Job,
    JobCheckpoint,
    JobStage,
    LanguageSpan,
    Recording,
    SpeechRegion,
)
from classscribe.errors import ClassScribeError, ErrorCode

AUDIO_CHECKPOINTS = (
    (JobStage.AUDIO_IMPORT, "canonical_master"),
    (JobStage.AUDIO_QC, "quality_report"),
    (JobStage.VAD, "speech_regions"),
    (JobStage.LID, "language_spans"),
    (JobStage.STRUCTURE, "structure_and_text_slices"),
)


class AudioArtifactRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def register_recording(
        self,
        imported: ImportedMedia,
        master: AudioMaster,
        report: AudioQualityReport,
    ) -> Recording:
        with self._sessions.begin() as session:
            recording = Recording(
                source_name=imported.source_name,
                source_sha256=imported.source_sha256,
                source_path=str(imported.source_path),
                duration_samples=master.duration_samples,
                sample_rate=master.sample_rate,
                channels=master.channels,
                audio_qc_json=report.as_dict(),
            )
            session.add(recording)
        return recording

    def update_quality_report(self, recording_id: str, report: AudioQualityReport) -> None:
        with self._sessions.begin() as session:
            recording = session.get(Recording, recording_id)
            if recording is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "recording does not exist")
            recording.audio_qc_json = report.as_dict()

    def replace_speech_regions(self, job_id: str, regions: Sequence[SpeechRegionResult]) -> None:
        with self._sessions.begin() as session:
            duration = self._job_duration(session, job_id)
            self._validate_spans([item.span.start_sample for item in regions], regions, duration)
            session.execute(delete(SpeechRegion).where(SpeechRegion.job_id == job_id))
            session.add_all(
                SpeechRegion(
                    job_id=job_id,
                    start_sample=item.span.start_sample,
                    end_sample=item.span.end_sample,
                    vad_score=item.vad_score,
                    acoustic_class=item.acoustic_class,
                    source=item.source,
                )
                for item in regions
            )

    def replace_language_spans(self, job_id: str, spans: Sequence[LanguageRoutingSpan]) -> None:
        with self._sessions.begin() as session:
            duration = self._job_duration(session, job_id)
            self._validate_spans([item.span.start_sample for item in spans], spans, duration)
            if spans and (
                spans[0].span.start_sample != 0
                or spans[-1].span.end_sample != duration
                or any(
                    left.span.end_sample != right.span.start_sample
                    for left, right in pairwise(spans)
                )
            ):
                raise ClassScribeError(
                    ErrorCode.AUDIO_TIMELINE_INVALID,
                    "language spans must be contiguous across the canonical timeline",
                )
            session.execute(delete(LanguageSpan).where(LanguageSpan.job_id == job_id))
            session.add_all(
                LanguageSpan(
                    job_id=job_id,
                    start_sample=item.span.start_sample,
                    end_sample=item.span.end_sample,
                    language=item.language,
                    confidence_raw=item.confidence_raw,
                    decision_json=dict(item.decision),
                )
                for item in spans
            )

    def next_audio_stage(self, job_id: str) -> tuple[JobStage, str] | None:
        """Resume after restart from the first incomplete durable audio checkpoint."""

        with self._sessions() as session:
            if session.get(Job, job_id) is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "job does not exist")
            completed = set(
                session.execute(
                    select(JobCheckpoint.stage, JobCheckpoint.checkpoint_key).where(
                        JobCheckpoint.job_id == job_id,
                        JobCheckpoint.status == CheckpointStatus.COMPLETED,
                    )
                ).all()
            )
        return next((item for item in AUDIO_CHECKPOINTS if item not in completed), None)

    @staticmethod
    def _job_duration(session: Session, job_id: str) -> int:
        duration = session.scalar(
            select(Recording.duration_samples)
            .join(Job, Job.recording_id == Recording.id)
            .where(Job.id == job_id)
        )
        if duration is None:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "job does not exist")
        return duration

    @staticmethod
    def _validate_spans(
        starts: Sequence[int],
        items: Sequence[SpeechRegionResult] | Sequence[LanguageRoutingSpan],
        duration: int,
    ) -> None:
        if list(starts) != sorted(starts):
            raise ClassScribeError(ErrorCode.AUDIO_TIMELINE_INVALID, "spans are not monotonic")
        ends = [item.span.end_sample for item in items]
        if ends != sorted(ends):
            raise ClassScribeError(ErrorCode.AUDIO_TIMELINE_INVALID, "span ends are not monotonic")
        if any(item.span.end_sample > duration for item in items):
            raise ClassScribeError(
                ErrorCode.AUDIO_TIMELINE_INVALID, "span exceeds canonical audio range"
            )


def quality_payload(report: AudioQualityReport) -> dict[str, Any]:
    return report.as_dict()
