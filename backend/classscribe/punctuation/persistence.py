"""Persist strict punctuation without collapsing the four text layers."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from classscribe.db.models import DecisionEvent, TranscriptSegment
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.punctuation.guard import require_character_invariance
from classscribe.punctuation.models import PunctuationResult


class PunctuationRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def apply(
        self,
        segment_id: str,
        *,
        faithful: PunctuationResult,
        smart: PunctuationResult | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            segment = session.get(TranscriptSegment, segment_id)
            if segment is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "segment does not exist")
            before_faithful = segment.faithful_text
            before_smart = segment.smart_corrected_text
            try:
                require_character_invariance(before_faithful, faithful.text)
                if smart is not None:
                    require_character_invariance(before_smart, smart.text)
            except ValueError as exc:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "punctuation persistence rejected a character-changing result",
                ) from exc
            segment.faithful_text = faithful.text
            if smart is not None:
                segment.smart_corrected_text = smart.text
            elif before_smart == before_faithful:
                segment.smart_corrected_text = faithful.text
            session.add(
                DecisionEvent(
                    segment_id=segment_id,
                    event_type="strict_punctuation_applied",
                    actor_type="automatic",
                    actor_id=None,
                    input_json={
                        "faithful_text": before_faithful,
                        "smart_corrected_text": before_smart,
                    },
                    output_json={
                        "faithful_text": segment.faithful_text,
                        "smart_corrected_text": segment.smart_corrected_text,
                        "faithful_source": faithful.source,
                        "smart_source": smart.source if smart else None,
                        "character_sequence_preserved": True,
                        "user_text_unchanged": True,
                    },
                    rule_version=faithful.rule_version,
                )
            )
