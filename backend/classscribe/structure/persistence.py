"""Short-transaction persistence for structural evidence and speaker presentation names."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.db.models import (
    Job,
    Recording,
    SpeakerDisplayName,
    SpeakerSpan,
    StructureSegmentRecord,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.structure.diarization import PyannoteResult
from classscribe.structure.models import STRUCTURE_TEXT_ROLE, StructureSegment
from classscribe.timeline import AudioSpan


@dataclass(frozen=True, slots=True)
class SpeakerTimelineSpan:
    window_ordinal: int
    span: AudioSpan
    speaker_global: str
    speaker_local: str
    overlap: bool
    source_model: str
    source_revision: str
    confidence: float
    provenance: dict[str, object]

    def __post_init__(self) -> None:
        if self.window_ordinal < 0 or self.span.duration_samples <= 0:
            raise ValueError("speaker timeline span is invalid")
        if not self.speaker_global or not self.speaker_local:
            raise ValueError("speaker timeline requires global and local labels")
        if len(self.source_revision) != 40:
            raise ValueError("speaker timeline source must use a full commit revision")
        if not 0 <= self.confidence <= 1:
            raise ValueError("speaker confidence must be within [0, 1]")


class StructureRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def replace_window(
        self,
        job_id: str,
        window_ordinal: int,
        segments: tuple[StructureSegment, ...],
        speaker_spans: tuple[SpeakerTimelineSpan, ...],
    ) -> None:
        """Atomically replace one window so a failure cannot erase other completed windows."""

        if window_ordinal < 0:
            raise ClassScribeError(ErrorCode.AUDIO_TIMELINE_INVALID, "invalid window ordinal")
        if any(item.window_ordinal != window_ordinal for item in segments) or any(
            item.window_ordinal != window_ordinal for item in speaker_spans
        ):
            raise ClassScribeError(
                ErrorCode.AUDIO_TIMELINE_INVALID,
                "window persistence payload mixes window ordinals",
            )
        with self._sessions.begin() as session:
            duration = self._job_duration(session, job_id)
            self._validate_segments(segments, duration)
            self._validate_speaker_spans(speaker_spans, duration)
            session.execute(
                delete(StructureSegmentRecord).where(
                    StructureSegmentRecord.job_id == job_id,
                    StructureSegmentRecord.window_ordinal == window_ordinal,
                )
            )
            session.execute(
                delete(SpeakerSpan).where(
                    SpeakerSpan.job_id == job_id,
                    SpeakerSpan.window_ordinal == window_ordinal,
                )
            )
            session.add_all(
                StructureSegmentRecord(
                    job_id=job_id,
                    window_ordinal=item.window_ordinal,
                    start_sample=item.span.start_sample,
                    end_sample=item.span.end_sample,
                    speaker_global_id=item.speaker_global,
                    speaker_local_id=item.speaker_local,
                    coarse_text=item.text,
                    acoustic_events_json=list(item.acoustic_events),
                    overlap=item.overlap,
                    exclusive=item.exclusive,
                    fallback=item.fallback,
                    source_model=item.source_model,
                    source_revision=item.source_revision,
                    confidence_raw=item.confidence_raw,
                    selection_score=item.selection_score,
                    text_role=STRUCTURE_TEXT_ROLE,
                    adopted_as_final=False,
                    provenance_json=dict(item.provenance),
                )
                for item in segments
            )
            session.add_all(
                SpeakerSpan(
                    job_id=job_id,
                    window_ordinal=item.window_ordinal,
                    start_sample=item.span.start_sample,
                    end_sample=item.span.end_sample,
                    speaker_global_id=item.speaker_global,
                    speaker_local_id=item.speaker_local,
                    overlap=item.overlap,
                    source_model=item.source_model,
                    source_revision=item.source_revision,
                    confidence=item.confidence,
                    provenance_json=dict(item.provenance),
                )
                for item in speaker_spans
            )

    def set_display_name(self, job_id: str, speaker_global: str, display_name: str) -> None:
        """Set a job-local UI label without editing any raw or stitched speaker evidence."""

        normalized = display_name.strip()
        if not speaker_global or not normalized or len(normalized) > 256:
            raise ValueError("speaker and display name must be non-empty and bounded")
        with self._sessions.begin() as session:
            if session.get(Job, job_id) is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "job does not exist")
            item = session.scalar(
                select(SpeakerDisplayName).where(
                    SpeakerDisplayName.job_id == job_id,
                    SpeakerDisplayName.speaker_global_id == speaker_global,
                )
            )
            if item is None:
                session.add(
                    SpeakerDisplayName(
                        job_id=job_id,
                        speaker_global_id=speaker_global,
                        display_name=normalized,
                        identity_scope="job",
                    )
                )
            else:
                item.display_name = normalized

    def display_names(self, job_id: str) -> dict[str, str]:
        with self._sessions() as session:
            rows = session.execute(
                select(
                    SpeakerDisplayName.speaker_global_id,
                    SpeakerDisplayName.display_name,
                ).where(SpeakerDisplayName.job_id == job_id)
            ).all()
        return {row.speaker_global_id: row.display_name for row in rows}

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
    def _validate_segments(segments: tuple[StructureSegment, ...], duration: int) -> None:
        if any(item.span.end_sample > duration for item in segments):
            raise ClassScribeError(
                ErrorCode.AUDIO_TIMELINE_INVALID, "structure segment exceeds canonical audio"
            )
        if [item.span.start_sample for item in segments] != sorted(
            item.span.start_sample for item in segments
        ):
            raise ClassScribeError(
                ErrorCode.AUDIO_TIMELINE_INVALID, "structure segments are not monotonic"
            )
        if any(
            item.text_role != STRUCTURE_TEXT_ROLE
            or item.provenance.get("adopted_as_final") is not False
            or item.provenance.get("absolute_samples") is not True
            for item in segments
        ):
            raise ClassScribeError(
                ErrorCode.CANDIDATE_TIMELINE_INVALID,
                "structure evidence lacks coarse-text and absolute-sample provenance",
            )

    @staticmethod
    def _validate_speaker_spans(
        speaker_spans: tuple[SpeakerTimelineSpan, ...], duration: int
    ) -> None:
        if any(item.span.end_sample > duration for item in speaker_spans):
            raise ClassScribeError(
                ErrorCode.AUDIO_TIMELINE_INVALID, "speaker span exceeds canonical audio"
            )


def speaker_timeline_from_segments(
    segments: tuple[StructureSegment, ...],
) -> tuple[SpeakerTimelineSpan, ...]:
    """Build the primary speaker track for native MOSS structure results."""

    return tuple(
        SpeakerTimelineSpan(
            window_ordinal=item.window_ordinal,
            span=item.span,
            speaker_global=item.speaker_global,
            speaker_local=item.speaker_local,
            overlap=False,
            source_model=item.source_model,
            source_revision=item.source_revision,
            confidence=(
                item.confidence_raw
                if item.confidence_raw is not None and 0 <= item.confidence_raw <= 1
                else item.selection_score
            ),
            provenance={**item.provenance, "speaker_track": "moss_native"},
        )
        for item in segments
        if not item.overlap and item.speaker_global is not None and item.speaker_local is not None
    )


def speaker_timeline_from_diarization(
    diarization: PyannoteResult,
    local_to_structure: dict[str, str],
    structure_to_global: dict[str, str],
) -> tuple[SpeakerTimelineSpan, ...]:
    """Persist exclusive primary spans and every participant in true overlaps."""

    spans: list[SpeakerTimelineSpan] = []
    for item in diarization.persistent_speaker_spans:
        structure_local = local_to_structure.get(item.speaker_local)
        global_id = (
            structure_to_global.get(structure_local) if structure_local is not None else None
        )
        if structure_local is None or global_id is None:
            continue
        confidence = item.confidence_raw if item.confidence_raw is not None else 0.5
        spans.append(
            SpeakerTimelineSpan(
                window_ordinal=diarization.window.ordinal,
                span=item.span,
                speaker_global=global_id,
                speaker_local=structure_local,
                overlap=item.overlap,
                source_model=item.source_model,
                source_revision=item.source_revision,
                confidence=max(0.0, min(1.0, confidence)),
                provenance={
                    **item.provenance,
                    "pyannote_local_speaker": item.speaker_local,
                    "structure_local_speaker": structure_local,
                    "speaker_track": "pyannote_true_overlap"
                    if item.overlap
                    else "pyannote_exclusive",
                },
            )
        )
    return tuple(
        sorted(
            spans,
            key=lambda item: (
                item.span.start_sample,
                item.span.end_sample,
                item.speaker_global,
                item.overlap,
            ),
        )
    )
