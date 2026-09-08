from pathlib import Path

from classscribe.api.service import ClassScribeService
from classscribe.asr.models import decode_policy
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    Job,
    JobStatus,
    Recording,
    ReviewStatus,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.quality.recovery import recover_repetition_markers
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


def test_recovery_is_idempotent_preserves_edits_and_exposes_persistent_warning(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, sessions, _ = database
    with sessions.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="d" * 64,
            source_path="/data/lecture.wav",
            duration_samples=160000,
            sample_rate=16000,
            channels=1,
        )
        session.add(recording)
        session.flush()
        job = Job(
            recording_id=recording.id,
            language_mode=LanguageMode.JAPANESE,
            profile_id="classroom.ja",
            status=JobStatus.COMPLETED,
        )
        session.add(job)
        session.flush()
        ids = []
        for kind in ("recover", "topic", "edited", "versioned", "hard", "audited"):
            segment = TranscriptSegment(
                job_id=job.id,
                start_sample=0,
                end_sample=160000,
                language=LanguageMode.JAPANESE,
                faithful_text="old marker",
                smart_corrected_text="old marker",
                auto_final_source="inaudible_marker",
                quality_score=0.0,
                user_text="人工修订" if kind == "edited" else None,
                version=2 if kind == "versioned" else 1,
            )
            session.add(segment)
            session.flush()
            if kind == "versioned":
                segment.version = 2
                session.flush()
            ids.append(segment.id)
            if kind == "audited":
                session.add(
                    DecisionEvent(
                        segment_id=segment.id,
                        event_type="user_edit",
                        actor_type="user",
                        input_json={},
                        output_json={},
                        rule_version="edit-v1",
                    )
                )
            for index in range(2):
                text = "あなたはだれですか。" * 5
                if kind == "topic":
                    text = (
                        "ネパールの産業を学びます。ネパールの通貨は何ですか。"
                        "ネパールの人口も確認します。ネパールには山があります。"
                    )
                session.add(
                    ASRCandidate(
                        segment_id=segment.id,
                        model_id=f"model-{index}",
                        model_revision="c" * 40,
                        raw_text=text,
                        normalized_text=text,
                        is_valid=False,
                        is_adopted=False,
                        quality_features_json={
                            "rule_version": "quality-gate-v1",
                            "valid_for_consensus": False,
                            "issues": [
                                "repeated_3_to_10_token_ngram",
                                "low_word_timestamp_coverage",
                            ]
                            + (["manual_language_script_mismatch"] if kind == "hard" else []),
                            "acoustic_model_features": {"vad_speech_ratio": 1.0},
                        },
                        warnings_json=[],
                        decode_config_json=decode_policy(160000),
                        inference_metrics_json={},
                    )
                )
    with sessions.begin() as session:
        assert recover_repetition_markers(session) == 3
    with sessions.begin() as session:
        assert recover_repetition_markers(session) == 0
    service = object.__new__(ClassScribeService)
    with sessions() as session:
        recovered = session.get_one(TranscriptSegment, ids[0])
        assert recovered.faithful_text == "あなたはだれですか。" * 5
        assert recovered.review_status == ReviewStatus.NEEDS_REVIEW
        assert recovered.version == 2
        payload = service._segment_payload(session, recovered)
        assert payload["repetition_warning"]["all_candidates"]
        assert payload["repetition_warning"]["candidates"][0]["fragment"]
        assert len(payload["repetition_warning"]["candidates"]) == 2
        assert session.scalar(select(TokenSpan).where(TokenSpan.segment_id == recovered.id))
        assert session.scalar(
            select(DecisionEvent).where(
                DecisionEvent.segment_id == recovered.id,
                DecisionEvent.event_type == "repetition_marker_recovered",
            )
        )
        topic = session.get_one(TranscriptSegment, ids[1])
        assert topic.faithful_text != "old marker"
        assert service._segment_payload(session, topic)["repetition_warning"] is None
        assert session.get_one(TranscriptSegment, ids[3]).faithful_text != "old marker"
        for identifier in (ids[2], *ids[4:]):
            assert session.get_one(TranscriptSegment, identifier).faithful_text == "old marker"
        assert session.get_one(TranscriptSegment, ids[2]).user_text == "人工修订"
