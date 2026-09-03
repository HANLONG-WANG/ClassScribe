from __future__ import annotations

from pathlib import Path

from classscribe.alignment import AlignedToken, AlignmentRepository, CanonicalTiming, TimingSource
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    GlossaryTerm,
    Job,
    Recording,
    TimingQuality,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.punctuation import PunctuationRepository, punctuate_japanese
from classscribe.terminology import (
    CorrectionEvidence,
    CourseConfig,
    CourseTerm,
    TerminologyRepository,
    TextLayers,
    apply_terminology,
)
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

REVISION = "f" * 40


def test_four_text_layers_terms_punctuation_timing_and_audit_are_persisted(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _engine, sessions, _path = database
    span = AudioSpan(2 * SAMPLE_RATE, 5 * SAMPLE_RATE)
    with sessions.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="a" * 64,
            source_path="/data/lecture.wav",
            duration_samples=6 * SAMPLE_RATE,
            sample_rate=SAMPLE_RATE,
            channels=1,
        )
        session.add(recording)
        session.flush()
        job = Job(
            recording_id=recording.id,
            language_mode=LanguageMode.JAPANESE,
            profile_id="classroom.ja",
        )
        session.add(job)
        session.flush()
        segment = TranscriptSegment(
            job_id=job.id,
            start_sample=span.start_sample,
            end_sample=span.end_sample,
            language=LanguageMode.JAPANESE,
            raw_text="大和橘を学ぶ",
            faithful_text="大和橘を学ぶ",
            smart_corrected_text="大和橘を学ぶ",
        )
        session.add(segment)
        session.flush()
        candidate = ASRCandidate(
            segment_id=segment.id,
            model_id="granite_speech_4_1_2b",
            model_revision=REVISION,
            raw_text=segment.raw_text,
            normalized_text=segment.raw_text,
            confidence_raw=0.8,
            confidence_calibrated=None,
            quality_features_json={},
            warnings_json=[],
            decode_config_json={},
            inference_metrics_json={},
        )
        session.add(candidate)
        session.flush()
        session.add(
            TokenSpan(
                candidate_id=None,
                segment_id=segment.id,
                start_sample=span.start_sample,
                end_sample=span.end_sample,
                token=segment.faithful_text,
                normalized_token=segment.faithful_text,
                confidence=0.8,
                provenance_json={
                    "source_type": "candidate_vote",
                    "candidate_sources": [{"candidate_id": candidate.id, "source_index": 0}],
                },
            )
        )
        segment_id, candidate_id = segment.id, candidate.id

    punctuation = punctuate_japanese("大和橘を学ぶ", granite_proposal="大和橘を学ぶ。")
    PunctuationRepository(sessions).apply(segment_id, faithful=punctuation)
    term = CourseTerm("ヤマトタチバナ", "やまとたちばな", ("大和橘",), language="ja")
    repository = TerminologyRepository(sessions)
    glossary_id = repository.store_course(CourseConfig("biology", "ja", "生物学", (), (term,)))
    correction = apply_terminology(
        TextLayers("大和橘を学ぶ", "大和橘を学ぶ。", "大和橘を学ぶ。"),
        (term,),
        {"ヤマトタチバナ": CorrectionEvidence(frozenset({"大和橘"}))},
        language="ja",
    )
    repository.apply_correction(segment_id, correction)
    timing = CanonicalTiming(
        TimingSource.QWEN_FORCED,
        span,
        "ヤマトタチバナを学ぶ。",
        (AlignedToken("ヤマトタチバナを学ぶ。", AudioSpan(34_000, 76_000)),),
        coarse_timing=False,
        speech_coverage=0.9,
    )
    AlignmentRepository(sessions).apply(segment_id, timing)

    with sessions() as session:
        persisted = session.get(TranscriptSegment, segment_id)
        assert persisted is not None
        assert persisted.raw_text == "大和橘を学ぶ"
        assert persisted.faithful_text == "大和橘を学ぶ。"
        assert persisted.smart_corrected_text == "ヤマトタチバナを学ぶ。"
        assert persisted.user_text is None
        assert persisted.timing_quality is TimingQuality.ALIGNED
        glossary_term = session.scalar(
            select(GlossaryTerm).where(GlossaryTerm.glossary_id == glossary_id)
        )
        assert glossary_term is not None and glossary_term.user_confirmed
        final = session.scalar(
            select(TokenSpan).where(
                TokenSpan.segment_id == segment_id, TokenSpan.candidate_id.is_(None)
            )
        )
        assert final is not None and final.start_sample == 34_000
        assert final.provenance_json["candidate_sources"][0]["candidate_id"] == candidate_id
        event_count = session.scalar(
            select(func.count())
            .select_from(DecisionEvent)
            .where(DecisionEvent.segment_id == segment_id)
        )
        assert event_count == 3
