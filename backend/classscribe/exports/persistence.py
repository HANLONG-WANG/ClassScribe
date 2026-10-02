"""Shared database snapshot mapping for automatic and requested exports."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from classscribe.db.models import Job, Recording, SpeakerDisplayName, TokenSpan, TranscriptSegment
from classscribe.exports.models import ExportSegment, ExportToken
from classscribe.timeline import SAMPLE_RATE, AudioSpan


def load_export_segments(session: Session, job_id: str) -> tuple[ExportSegment, ...]:
    recording = session.scalar(
        select(Recording).join(Job, Job.recording_id == Recording.id).where(Job.id == job_id)
    )
    names = {
        item.speaker_global_id: item.display_name
        for item in session.scalars(
            select(SpeakerDisplayName).where(SpeakerDisplayName.job_id == job_id)
        )
    }
    result: list[ExportSegment] = []
    previous_end: int | None = None
    for segment in session.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.job_id == job_id, TranscriptSegment.is_active.is_(True))
        .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
    ):
        tokens = tuple(
            ExportToken(
                token.token,
                AudioSpan(token.start_sample, token.end_sample),
                protected_group=str(token.provenance_json["protected_group"])
                if token.provenance_json.get("protected_group")
                else None,
                provenance=dict(token.provenance_json),
            )
            for token in session.scalars(
                select(TokenSpan)
                .where(TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None))
                .order_by(TokenSpan.start_sample, TokenSpan.id)
            )
        )
        result.append(
            ExportSegment(
                segment.id,
                AudioSpan(segment.start_sample, segment.end_sample),
                segment.language.value,
                segment.raw_text,
                segment.faithful_text,
                segment.smart_corrected_text,
                segment.user_text,
                tokens,
                smart_tokens=tokens,
                user_tokens=tokens if segment.user_text is not None else (),
                speaker=names.get(segment.speaker_id, segment.speaker_id)
                if segment.speaker_id
                else None,
                pause_before_ms=max(0, (segment.start_sample - previous_end) * 1000 // SAMPLE_RATE)
                if previous_end is not None
                else 0,
                timing_quality=segment.timing_quality.value,
                source_recording_id=recording.parent_recording_id if recording else None,
                source_offset_sample=recording.source_start_sample if recording else None,
            )
        )
        previous_end = segment.end_sample
    return tuple(result)
