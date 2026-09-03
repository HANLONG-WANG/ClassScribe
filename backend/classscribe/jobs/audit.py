"""Optimistic segment editing and immutable decision-event audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from classscribe.db.models import ASRCandidate, DecisionEvent, TranscriptSegment
from classscribe.errors import ClassScribeError, ErrorCode


class ActorType(StrEnum):
    AUTOMATIC = "automatic"
    HUMAN = "human"


class EditableLayer(StrEnum):
    FAITHFUL = "faithful_text"
    CORRECTED = "smart_corrected_text"
    USER = "user_text"


@dataclass(frozen=True, slots=True)
class EditResult:
    segment_id: str
    previous_version: int
    version: int


class AuditService:
    def edit_segment(
        self,
        session: Session,
        *,
        segment_id: str,
        expected_version: int,
        layer: EditableLayer,
        text: str,
        actor_type: ActorType,
        actor_id: str | None,
        rule_version: str,
    ) -> EditResult:
        segment = session.get(TranscriptSegment, segment_id)
        if segment is None:
            raise ClassScribeError(ErrorCode.SEGMENT_VERSION_CONFLICT, "segment does not exist")
        if actor_type is ActorType.AUTOMATIC and layer is EditableLayer.USER:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT, "automatic processes may not edit the user layer"
            )
        previous_text = getattr(segment, layer.value)
        statement = (
            update(TranscriptSegment)
            .where(
                TranscriptSegment.id == segment_id,
                TranscriptSegment.version == expected_version,
            )
            .values(
                {
                    layer.value: text,
                    TranscriptSegment.version: expected_version + 1,
                    TranscriptSegment.updated_at: datetime.now(UTC),
                }
            )
        )
        result = cast(CursorResult[Any], session.execute(statement))
        if result.rowcount != 1:
            session.expire(segment)
            raise ClassScribeError(
                ErrorCode.SEGMENT_VERSION_CONFLICT,
                f"expected segment version {expected_version} is stale",
            )
        session.add(
            DecisionEvent(
                segment_id=segment_id,
                event_type="segment_text_edited",
                actor_type=actor_type.value,
                actor_id=actor_id,
                input_json={
                    "layer": layer.value,
                    "text": previous_text,
                    "version": expected_version,
                },
                output_json={
                    "layer": layer.value,
                    "text": text,
                    "version": expected_version + 1,
                },
                rule_version=rule_version,
            )
        )
        return EditResult(segment_id, expected_version, expected_version + 1)

    def adopt_candidate(
        self,
        session: Session,
        *,
        candidate_id: str,
        actor_type: ActorType,
        actor_id: str | None,
        rule_version: str,
    ) -> None:
        candidate = session.get(ASRCandidate, candidate_id)
        if candidate is None or candidate.deleted_at is not None or not candidate.is_valid:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "candidate is not adoptable")
        if candidate.is_adopted:
            return
        candidate.is_adopted = True
        session.add(
            DecisionEvent(
                segment_id=candidate.segment_id,
                event_type="candidate_adopted",
                actor_type=actor_type.value,
                actor_id=actor_id,
                input_json={"candidate_id": candidate.id},
                output_json={
                    "model_id": candidate.model_id,
                    "model_revision": candidate.model_revision,
                    "normalized_text": candidate.normalized_text,
                },
                rule_version=rule_version,
            )
        )

    def soft_delete_candidate(self, session: Session, candidate_id: str) -> None:
        candidate = session.get(ASRCandidate, candidate_id)
        if candidate is None or candidate.deleted_at is not None:
            return
        if candidate.is_adopted:
            raise ClassScribeError(
                ErrorCode.CANDIDATE_ALREADY_ADOPTED,
                "adopted candidates are immutable and cannot be deleted",
            )
        candidate.deleted_at = datetime.now(UTC)
