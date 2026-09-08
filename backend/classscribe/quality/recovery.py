"""Idempotent recovery of untouched transcripts rejected by the old repetition gate."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from classscribe.asr.models import ASRCandidateEvidence, ASRContractError, CandidateRole
from classscribe.consensus.models import ConsensusCandidate, ReliabilityProfile
from classscribe.consensus.network import ConfusionNetwork, ConsensusInputs
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    Job,
    JobStatus,
    ReviewStatus,
    TimingQuality,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.quality.features import _ISSUE_PENALTIES
from classscribe.quality.loop import inspect_decode_loop
from classscribe.quality.models import (
    REPETITION_ISSUES,
    QualityIssue,
    QualityReport,
    blocking_issues,
)
from classscribe.timeline import SAMPLE_RATE, AudioSpan


def recover_repetition_markers(session: Session) -> int:
    """Run in a caller-owned transaction; never re-run models or overwrite human edits."""
    restored = 0
    segments = session.scalars(
        select(TranscriptSegment)
        .join(Job)
        .where(
            Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]),
            TranscriptSegment.is_active.is_(True),
            TranscriptSegment.auto_final_source == "inaudible_marker",
            TranscriptSegment.user_text.is_(None),
        )
    ).all()
    for segment in segments:
        if session.scalar(
            select(DecisionEvent.id)
            .where(DecisionEvent.segment_id == segment.id, DecisionEvent.actor_type != "automatic")
            .limit(1)
        ):
            continue
        # Pipeline stages also increment versions; use the audit trail to distinguish edits.
        latest = session.scalar(
            select(DecisionEvent)
            .where(
                DecisionEvent.segment_id == segment.id,
                DecisionEvent.event_type == "automatic_consensus_adopted",
            )
            .order_by(DecisionEvent.created_at.desc(), DecisionEvent.id.desc())
            .limit(1)
        )
        if latest and segment.faithful_text != latest.output_json.get("faithful_text"):
            continue
        if segment.smart_corrected_text != segment.faithful_text:
            continue
        rows = tuple(
            session.scalars(
                select(ASRCandidate).where(
                    ASRCandidate.segment_id == segment.id,
                    ASRCandidate.deleted_at.is_(None),
                )
            )
        )
        latest_ids = latest.input_json.get("candidate_ids") if latest else None
        if isinstance(latest_ids, list):
            rows = tuple(row for row in rows if row.id in latest_ids)
        superseded = {row.supersedes_candidate_id for row in rows}
        rows = tuple(row for row in rows if row.id not in superseded)
        span = AudioSpan(segment.start_sample, segment.end_sample)
        reviewed = []
        reports = {}
        for row in rows:
            old = row.quality_features_json
            # Recovery is intentionally restricted to known, previously scored reports.
            if old.get("rule_version") != "quality-gate-v1" or not row.normalized_text.strip():
                continue
            try:
                old_issues = tuple(QualityIssue(issue) for issue in old["issues"])
            except (KeyError, ValueError):
                continue
            if not REPETITION_ISSUES.intersection(old_issues):
                continue
            if blocking_issues(old_issues):
                continue
            ratio = float(old.get("acoustic_model_features", {}).get("vad_speech_ratio", 1))
            loop = inspect_decode_loop(
                row.normalized_text,
                segment.language.value,
                voiced_seconds=span.duration_samples / SAMPLE_RATE * ratio,
            )
            issues = tuple(
                dict.fromkeys(
                    [
                        *(issue for issue in old_issues if issue not in REPETITION_ISSUES),
                        *loop.issues,
                    ]
                )
            )
            if blocking_issues(issues):
                continue
            report = QualityReport(
                row.id,
                row.model_id,
                True,
                round(max(0.0, 1.0 - sum(_ISSUE_PENALTIES[i] for i in issues)), 6),
                False,
                issues,
                old.get("acoustic_model_features", {}),
                {**old.get("text_features", {}), **loop.metrics},
                old.get("timing_features", {}),
                old.get("multi_model_features", {}),
                None,
            )
            try:
                evidence = ASRCandidateEvidence(
                    row.model_id,
                    row.model_revision,
                    CandidateRole.PRIMARY,
                    segment.language.value,
                    segment.language.value,
                    span,
                    span,
                    row.raw_text,
                    row.normalized_text,
                    row.confidence_raw,
                    (),
                    dict(row.decode_config_json),
                    dict(row.inference_metrics_json),
                    (),
                    dict(old.get("provenance", {})),
                )
            except ASRContractError:
                # Old/incomplete evidence must not prevent the service from starting.
                continue
            reviewed.append(ConsensusCandidate(row.id, evidence, report))
            reports[row.id] = report
        if not reviewed:
            continue
        result = ConfusionNetwork().resolve(
            tuple(reviewed),
            canonical_span=span,
            language=segment.language.value,
            inputs=ConsensusInputs(
                ReliabilityProfile({}, "historical-recovery-uncalibrated", False, "lecture"),
                {},
                (span,),
            ),
        )
        previous = {
            "faithful_text": segment.faithful_text,
            "smart_corrected_text": segment.smart_corrected_text,
            "quality_score": segment.quality_score,
            "auto_final_source": segment.auto_final_source,
            "version": segment.version,
        }
        selected = {
            str(source["candidate_id"])
            for token in result.tokens
            for source in token.provenance.get("candidate_sources", [])
        }
        for row in rows:
            row.is_adopted = row.id in selected
            if row.id in reports:
                session.add(
                    DecisionEvent(
                        segment_id=segment.id,
                        event_type="candidate_quality_reassessed",
                        actor_type="automatic",
                        input_json={
                            "candidate_id": row.id,
                            "previous_quality": row.quality_features_json,
                        },
                        output_json=reports[row.id].as_dict(),
                        rule_version="repetition-recovery-v2",
                    )
                )
                row.is_valid = True
                row.quality_features_json = {
                    **row.quality_features_json,
                    **reports[row.id].as_dict(),
                }
        session.execute(
            delete(TokenSpan).where(
                TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None)
            )
        )
        session.add_all(
            TokenSpan(
                segment_id=segment.id,
                candidate_id=None,
                token=token.text,
                normalized_token=token.text.casefold(),
                start_sample=token.span.start_sample,
                end_sample=token.span.end_sample,
                confidence=token.support_score,
                provenance_json={
                    **token.provenance,
                    "is_final_consensus_token": True,
                    "timing_source": "historical_coarse_recovery",
                },
            )
            for token in result.tokens
        )
        segment.faithful_text = result.text
        segment.smart_corrected_text = result.text
        segment.auto_final_source = result.strategy
        segment.quality_score = result.consensus_support_score
        segment.timing_quality = TimingQuality.STRUCTURE
        # Human review is useful even after a false positive was removed.
        segment.review_status = ReviewStatus.NEEDS_REVIEW
        segment.version += 1
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="repetition_marker_recovered",
                actor_type="automatic",
                input_json=previous,
                output_json={
                    "faithful_text": result.text,
                    "strategy": result.strategy,
                    "warnings": list(result.warnings),
                    "version": segment.version,
                },
                rule_version="repetition-recovery-v2",
            )
        )
        restored += 1
    return restored
