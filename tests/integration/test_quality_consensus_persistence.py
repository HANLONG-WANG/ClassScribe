from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.asr.models import (
    ASRCandidateEvidence,
    ASRTokenEvidence,
    CandidateRole,
    decode_policy,
)
from classscribe.consensus import (
    ConfusionNetwork,
    ConsensusCandidate,
    ConsensusInputs,
    ConsensusRepository,
    ReliabilityProfile,
)
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    Job,
    Recording,
    ReviewStatus,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError
from classscribe.quality.models import QualityIssue, QualityReport
from classscribe.quality.routing import (
    ReviewRouter,
    SecondaryReviewDecision,
    SecondaryTrigger,
)
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

SPAN = AudioSpan(2 * SAMPLE_RATE, 8 * SAMPLE_RATE)
REVISION = "c" * 40


def _evidence(model_id: str, text: str) -> ASRCandidateEvidence:
    return ASRCandidateEvidence(
        model_id=model_id,
        model_revision=REVISION,
        role=CandidateRole.PRIMARY,
        language_requested="en",
        language_reported="en",
        audio_span=SPAN,
        core_span=SPAN,
        raw_text=text,
        normalized_text=text,
        confidence_raw=0.75,
        tokens=(ASRTokenEvidence(text, SPAN, 0.7, 0),),
        decode=decode_policy(SPAN.duration_samples),
        metrics={"rtf": 0.1},
        warnings=(),
        provenance={"absolute_samples": True},
    )


def _quality(candidate_id: str, model_id: str) -> QualityReport:
    return QualityReport(
        candidate_id=candidate_id,
        model_id=model_id,
        valid_for_consensus=True,
        quality_gate_score=0.9,
        score_is_calibrated_probability=False,
        issues=(),
        acoustic_model_features={"confidence_raw_model_local": 0.75},
        text_features={"script_detail": "latin"},
        timing_features={"timestamp_voiced_coverage": 1.0},
        multi_model_features={"structure_text_available": False},
        retry=None,
    )


def test_quality_routes_consensus_and_each_final_token_provenance_are_audited(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _engine, sessions, _path = database
    with sessions.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="d" * 64,
            source_path="/data/lecture.wav",
            duration_samples=10 * SAMPLE_RATE,
            sample_rate=SAMPLE_RATE,
            channels=1,
        )
        session.add(recording)
        session.flush()
        job = Job(
            recording_id=recording.id,
            language_mode=LanguageMode.ENGLISH,
            profile_id="classroom.en",
        )
        session.add(job)
        session.flush()
        segment = TranscriptSegment(
            job_id=job.id,
            start_sample=SPAN.start_sample,
            end_sample=SPAN.end_sample,
            language=LanguageMode.ENGLISH,
        )
        session.add(segment)
        session.flush()
        candidate = ASRCandidate(
            segment_id=segment.id,
            model_id="model-a",
            model_revision=REVISION,
            raw_text="photosynthesis",
            normalized_text="photosynthesis",
            confidence_raw=0.75,
            confidence_calibrated=None,
            quality_features_json={"raw_confidence_not_cross_model_probability": True},
            warnings_json=[],
            decode_config_json=decode_policy(SPAN.duration_samples),
            inference_metrics_json={"rtf": 0.1},
        )
        session.add(candidate)
        session.flush()
        segment_id, candidate_id = segment.id, candidate.id

    quality = _quality(candidate_id, "model-a")
    repository = ConsensusRepository(sessions)
    repository.record_quality(candidate_id, quality)
    secondary = SecondaryReviewDecision(
        True,
        (SecondaryTrigger.SUSPECTED_TERMINOLOGY_ERROR,),
        None,
    )
    repository.record_review_route(segment_id, secondary, None)
    result = ConfusionNetwork().resolve(
        (ConsensusCandidate(candidate_id, _evidence("model-a", "photosynthesis"), quality),),
        canonical_span=SPAN,
        language="en",
        inputs=ConsensusInputs(
            ReliabilityProfile({"model-a": 0.9}, "local-gold", True, "lecture"),
            {(candidate_id, 0): 0.8},
            (SPAN,),
        ),
    )
    repository.adopt_consensus(segment_id, result)

    with sessions() as session:
        persisted_candidate = session.get(ASRCandidate, candidate_id)
        persisted_segment = session.get(TranscriptSegment, segment_id)
        assert persisted_candidate is not None and persisted_candidate.is_valid
        assert persisted_candidate.quality_features_json["quality_gate_score"] == 0.9
        assert persisted_candidate.confidence_calibrated is None
        assert persisted_segment is not None
        assert persisted_segment.faithful_text == "photosynthesis"
        assert persisted_segment.smart_corrected_text == "photosynthesis"
        assert persisted_segment.review_status is ReviewStatus.AUTO
        final = session.scalar(
            select(TokenSpan).where(
                TokenSpan.segment_id == segment_id,
                TokenSpan.candidate_id.is_(None),
            )
        )
        assert final is not None
        assert final.provenance_json["candidate_sources"][0]["candidate_id"] == candidate_id
        assert final.provenance_json["is_final_consensus_token"] is True
        events = session.scalars(
            select(DecisionEvent).where(DecisionEvent.segment_id == segment_id)
        ).all()
        assert {event.event_type for event in events} == {
            "candidate_quality_gated",
            "automatic_model_review_routed",
            "automatic_consensus_adopted",
        }
        route = next(
            event for event in events if event.event_type == "automatic_model_review_routed"
        )
        assert route.output_json["secondary_triggers"] == ["suspected_terminology_error"]


def test_quality_rejection_persists_and_fake_final_candidate_provenance_is_refused(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _engine, sessions, _path = database
    with sessions.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="e" * 64,
            source_path="/data/rejected.wav",
            duration_samples=10 * SAMPLE_RATE,
            sample_rate=SAMPLE_RATE,
            channels=1,
        )
        session.add(recording)
        session.flush()
        job = Job(
            recording_id=recording.id,
            language_mode=LanguageMode.ENGLISH,
            profile_id="classroom.en",
        )
        session.add(job)
        session.flush()
        segment = TranscriptSegment(
            job_id=job.id,
            start_sample=SPAN.start_sample,
            end_sample=SPAN.end_sample,
            language=LanguageMode.ENGLISH,
        )
        session.add(segment)
        session.flush()
        candidate = ASRCandidate(
            segment_id=segment.id,
            model_id="model-a",
            model_revision=REVISION,
            raw_text="loop",
            normalized_text="loop",
            confidence_raw=0.9,
            confidence_calibrated=None,
            quality_features_json={},
            warnings_json=[],
            decode_config_json={},
            inference_metrics_json={},
        )
        session.add(candidate)
        session.flush()
        segment_id, candidate_id = segment.id, candidate.id
    rejected = QualityReport(
        candidate_id,
        "model-a",
        False,
        0.0,
        False,
        (QualityIssue.REPEATED_NGRAM,),
        {},
        {},
        {},
        {},
        None,
    )
    repository = ConsensusRepository(sessions)
    repository.record_quality(candidate_id, rejected)
    with sessions() as session:
        assert session.get(ASRCandidate, candidate_id).is_valid is False  # type: ignore[union-attr]

    valid = _quality(candidate_id, "model-a")
    result = ConfusionNetwork().resolve(
        (ConsensusCandidate(candidate_id, _evidence("model-a", "safe"), valid),),
        canonical_span=SPAN,
        language="en",
        inputs=ConsensusInputs(ReliabilityProfile({}, "neutral", False, "lecture"), {}, (SPAN,)),
    )
    result.tokens[0].provenance["candidate_sources"][0]["candidate_id"] = "foreign"
    with pytest.raises(ClassScribeError, match="outside its segment"):
        repository.adopt_consensus(segment_id, result)


def test_review_router_record_value_is_not_treated_as_calibrated_probability() -> None:
    report = _quality("candidate", "model")
    decision = ReviewRouter().secondary(report)
    assert not report.score_is_calibrated_probability
    assert not decision.run_secondary
