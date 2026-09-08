"""Short transactions for quality reports, review decisions, and final token evidence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.consensus.models import ConsensusResult
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    ReviewStatus,
    TimingQuality,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.quality.models import QualityReport
from classscribe.quality.routing import SecondaryReviewDecision, TertiaryReviewDecision


class ConsensusRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record_quality(self, candidate_id: str, report: QualityReport) -> None:
        with self._sessions.begin() as session:
            candidate = session.get(ASRCandidate, candidate_id)
            if candidate is None or candidate.segment_id is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "candidate does not exist")
            if report.candidate_id != candidate_id or report.model_id != candidate.model_id:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "quality report identity differs from persisted candidate",
                )
            candidate.quality_features_json = {
                **candidate.quality_features_json,
                **report.as_dict(),
            }
            candidate.is_valid = report.valid_for_consensus
            candidate.warnings_json = [
                *candidate.warnings_json,
                *({"code": "quality_issue", "detail": issue.value} for issue in report.issues),
            ]
            session.add(
                DecisionEvent(
                    segment_id=candidate.segment_id,
                    event_type="candidate_quality_gated",
                    actor_type="automatic",
                    actor_id=None,
                    input_json={
                        "candidate_id": candidate_id,
                        "model_id": candidate.model_id,
                        "model_revision": candidate.model_revision,
                        "confidence_raw_model_local": candidate.confidence_raw,
                    },
                    output_json=report.as_dict(),
                    rule_version=report.rule_version,
                )
            )

    def record_review_route(
        self,
        segment_id: str,
        secondary: SecondaryReviewDecision,
        tertiary: TertiaryReviewDecision | None,
    ) -> None:
        with self._sessions.begin() as session:
            if session.get(TranscriptSegment, segment_id) is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "segment does not exist")
            session.add(
                DecisionEvent(
                    segment_id=segment_id,
                    event_type="automatic_model_review_routed",
                    actor_type="automatic",
                    actor_id=None,
                    input_json={"quality_gate_completed": True},
                    output_json={
                        "run_secondary": secondary.run_secondary,
                        "secondary_triggers": [item.value for item in secondary.triggers],
                        "retry": _retry_json(secondary),
                        "run_tertiary": tertiary.run_tertiary if tertiary else False,
                        "tertiary_triggers": (
                            [item.value for item in tertiary.triggers] if tertiary else []
                        ),
                        "high_value_conflicts": (
                            list(tertiary.high_value_conflicts) if tertiary else []
                        ),
                    },
                    rule_version=(
                        f"{secondary.rule_version}+{tertiary.rule_version}"
                        if tertiary
                        else secondary.rule_version
                    ),
                )
            )

    def adopt_consensus(self, segment_id: str, result: ConsensusResult) -> None:
        with self._sessions.begin() as session:
            segment = session.get(TranscriptSegment, segment_id)
            if segment is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "segment does not exist")
            if (
                segment.start_sample != result.canonical_span.start_sample
                or segment.end_sample != result.canonical_span.end_sample
            ):
                raise ClassScribeError(
                    ErrorCode.CANDIDATE_TIMELINE_INVALID,
                    "consensus range differs from the natural transcript segment",
                )
            candidate_ids = set(
                session.scalars(
                    select(ASRCandidate.id).where(ASRCandidate.segment_id == segment_id)
                ).all()
            )
            for token in result.tokens:
                _validate_provenance(token.provenance, candidate_ids)
            session.execute(
                delete(TokenSpan).where(
                    TokenSpan.segment_id == segment_id,
                    TokenSpan.candidate_id.is_(None),
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
                    confidence=token.support_score,
                    provenance_json={
                        **token.provenance,
                        "is_final_consensus_token": True,
                        "consensus_rule_version": result.rule_version,
                        "support_score_is_calibrated_probability": False,
                    },
                )
                for token in result.tokens
            )
            previous = {
                "faithful_text": segment.faithful_text,
                "smart_corrected_text": segment.smart_corrected_text,
                "quality_score": segment.quality_score,
                "version": segment.version,
            }
            segment.faithful_text = result.text
            segment.smart_corrected_text = result.text
            segment.auto_final_source = result.strategy
            segment.quality_score = result.consensus_support_score
            segment.timing_quality = TimingQuality.ALIGNED
            segment.review_status = (
                ReviewStatus.NEEDS_REVIEW if result.low_confidence else ReviewStatus.AUTO
            )
            session.add(
                DecisionEvent(
                    segment_id=segment_id,
                    event_type="automatic_consensus_adopted",
                    actor_type="automatic",
                    actor_id=None,
                    input_json=previous,
                    output_json={
                        "faithful_text": result.text,
                        "punctuation_sources": list(result.punctuation_sources),
                        "strategy": result.strategy,
                        "consensus_support_score": result.consensus_support_score,
                        "score_is_calibrated_probability": False,
                        "low_confidence": result.low_confidence,
                        "warnings": list(result.warnings),
                        "token_count": len(result.tokens),
                        "token_provenance_complete": True,
                    },
                    rule_version=result.rule_version,
                )
            )


def _retry_json(decision: SecondaryReviewDecision) -> dict[str, Any] | None:
    if decision.retry is None:
        return None
    return {
        "retry_spans": [
            {"start_sample": span.start_sample, "end_sample": span.end_sample}
            for span in decision.retry.retry_spans
        ],
        "preserves_full_canonical_coverage": True,
        "switch_model": decision.retry.switch_model,
        "reject_candidate": decision.retry.reject_candidate,
        "reason": decision.retry.reason,
    }


def _validate_provenance(provenance: dict[str, Any], candidate_ids: set[str]) -> None:
    source_type = provenance.get("source_type")
    if source_type == "inaudible_marker":
        return
    sources = provenance.get("candidate_sources")
    if not isinstance(sources, list) or not sources:
        raise ClassScribeError(
            ErrorCode.JOB_STATE_CONFLICT,
            "final token lacks ASR candidate provenance",
        )
    source_ids = {source.get("candidate_id") for source in sources if isinstance(source, dict)}
    if not source_ids or not source_ids <= candidate_ids:
        raise ClassScribeError(
            ErrorCode.JOB_STATE_CONFLICT,
            "final token references a candidate outside its segment",
        )
    if source_type == "deterministic_terminology_rule" and not provenance.get("rule_id"):
        raise ClassScribeError(
            ErrorCode.JOB_STATE_CONFLICT,
            "terminology-standardized token lacks an explicit rule ID",
        )
