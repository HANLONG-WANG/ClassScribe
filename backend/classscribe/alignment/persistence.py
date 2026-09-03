"""Persist canonical final timing while retaining ASR provenance and coarse fallback evidence."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.alignment.models import CanonicalTiming
from classscribe.db.models import DecisionEvent, TimingQuality, TokenSpan, TranscriptSegment
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.punctuation.guard import strip_punctuation_and_spacing
from classscribe.recovery import record_alignment_fallback


class AlignmentRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def apply(self, segment_id: str, timing: CanonicalTiming) -> None:
        with self._sessions.begin() as session:
            segment = session.get(TranscriptSegment, segment_id)
            if segment is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "segment does not exist")
            if (
                segment.start_sample != timing.canonical_span.start_sample
                or segment.end_sample != timing.canonical_span.end_sample
            ):
                raise ClassScribeError(
                    ErrorCode.CANDIDATE_TIMELINE_INVALID,
                    "canonical timing differs from the transcript segment range",
                )
            selected_text = (
                segment.user_text or segment.smart_corrected_text or segment.faithful_text
            )
            if selected_text != timing.final_text:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "canonical timing does not describe final text"
                )
            existing = tuple(
                session.scalars(
                    select(TokenSpan)
                    .where(TokenSpan.segment_id == segment_id, TokenSpan.candidate_id.is_(None))
                    .order_by(TokenSpan.start_sample, TokenSpan.id)
                ).all()
            )
            provenance = _combined_provenance(existing)
            session.execute(
                delete(TokenSpan).where(
                    TokenSpan.segment_id == segment_id, TokenSpan.candidate_id.is_(None)
                )
            )
            session.add_all(
                TokenSpan(
                    candidate_id=None,
                    segment_id=segment_id,
                    start_sample=token.span.start_sample,
                    end_sample=token.span.end_sample,
                    token=token.text,
                    normalized_token=token.text.casefold(),
                    confidence=None,
                    provenance_json={
                        **provenance,
                        "timing_source": timing.source.value,
                        "coarse_timing": timing.coarse_timing,
                    },
                )
                for token in timing.tokens
            )
            segment.timing_quality = (
                TimingQuality.STRUCTURE if timing.coarse_timing else TimingQuality.ALIGNED
            )
            if timing.coarse_timing:
                record_alignment_fallback(
                    session, segment, detail=";".join(timing.fallback_reasons) or "coarse fallback"
                )
            else:
                session.add(
                    DecisionEvent(
                        segment_id=segment_id,
                        event_type="canonical_timing_adopted",
                        actor_type="automatic",
                        actor_id=None,
                        input_json={"final_text": timing.final_text},
                        output_json={
                            "source": timing.source.value,
                            "token_count": len(timing.tokens),
                            "speech_coverage": timing.speech_coverage,
                            "coarse_timing": False,
                            "text_unchanged": True,
                        },
                        rule_version=timing.rule_version,
                    )
                )


def _combined_provenance(tokens: tuple[TokenSpan, ...]) -> dict[str, object]:
    if not tokens:
        return {"source_type": "final_text_alignment", "candidate_sources": []}
    sources: dict[str, dict[str, object]] = {}
    for token in tokens:
        raw_sources = token.provenance_json.get("candidate_sources", [])
        if isinstance(raw_sources, list):
            for source in raw_sources:
                if isinstance(source, dict) and isinstance(source.get("candidate_id"), str):
                    sources[str(source["candidate_id"])] = dict(source)
    combined_text = "".join(token.token for token in tokens)
    return {
        "source_type": "final_text_alignment",
        "candidate_sources": list(sources.values()),
        "prior_final_text_without_punctuation": strip_punctuation_and_spacing(combined_text),
    }
