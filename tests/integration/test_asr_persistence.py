from __future__ import annotations

from pathlib import Path

from classscribe.asr.models import CandidateRole, build_asr_request, parse_asr_response
from classscribe.asr.persistence import ASRCandidateRepository
from classscribe.audio.segmentation import TranscriptChunk
from classscribe.contracts import LanguageMode
from classscribe.db.models import ASRCandidate, Job, Recording, TokenSpan, TranscriptSegment
from classscribe.db.session import create_sqlite_engine, make_session_factory
from classscribe.models.registry import load_registry
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from classscribe_protocol import RPCResponse
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]


def _chunk() -> TranscriptChunk:
    return TranscriptChunk(
        0,
        AudioSpan(2 * SAMPLE_RATE, 12 * SAMPLE_RATE),
        AudioSpan(SAMPLE_RATE, 13 * SAMPLE_RATE),
        "speaker_change",
        True,
        SAMPLE_RATE,
        SAMPLE_RATE,
    )


def test_natural_segment_and_candidate_persist_decode_metrics_words_and_supersession(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(tmp_path / "asr.sqlite3")
    from classscribe.db.base import Base

    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="a" * 64,
            source_path="/data/source.wav",
            duration_samples=20 * SAMPLE_RATE,
            sample_rate=SAMPLE_RATE,
            channels=1,
        )
        session.add(recording)
        session.flush()
        job = Job(
            recording_id=recording.id,
            language_mode=LanguageMode.CHINESE,
            profile_id="classroom.zh",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    repository = ASRCandidateRepository(sessions)
    segment_id = repository.ensure_natural_segment(
        job_id,
        _chunk(),
        language="zh",
        speaker_global_id="SPEAKER_01",
    )
    assert (
        repository.ensure_natural_segment(
            job_id,
            _chunk(),
            language="zh",
            speaker_global_id="SPEAKER_01",
        )
        == segment_id
    )
    entry = load_registry(ROOT / "config/model-registry.v1.yaml").model("firered_asr2_aed")
    request = build_asr_request(
        request_id="persist",
        job_id=job_id,
        audio_path=Path("/tmp/audio.wav"),
        chunk=_chunk(),
        entry=entry,
        language="zh",
        role=CandidateRole.PRIMARY,
    )
    response = RPCResponse(
        request_id=request.request_id,
        job_id=job_id,
        ok=True,
        model_id=entry.id,
        model_revision=entry.revision,
        raw_text="光合作用",
        normalized_text="光合作用",
        language="zh",
        segments=(
            {
                "start_sample": 2 * SAMPLE_RATE,
                "end_sample": 12 * SAMPLE_RATE,
                "text": "光合作用",
                "confidence_raw": 0.9,
                "words": [
                    {
                        "start_sample": 3 * SAMPLE_RATE,
                        "end_sample": 5 * SAMPLE_RATE,
                        "text": "光合作用",
                        "confidence_raw": 0.85,
                    }
                ],
            },
        ),
        metrics={"inference_ms": 50, "peak_vram_mb": 2000, "rtf": 0.01},
        warnings=("native confidence is model-local",),
    )
    evidence = parse_asr_response(response, request, entry, _chunk(), CandidateRole.PRIMARY)
    first_id = repository.add_candidate(segment_id, evidence)
    second_id = repository.add_candidate(segment_id, evidence)

    with sessions() as session:
        segment = session.get(TranscriptSegment, segment_id)
        assert segment is not None
        assert segment.raw_text == "" and segment.speaker_id == "SPEAKER_01"
        candidates = session.scalars(
            select(ASRCandidate).where(ASRCandidate.segment_id == segment_id)
        ).all()
        assert len(candidates) == 2
        second = session.get(ASRCandidate, second_id)
        assert second is not None and second.supersedes_candidate_id == first_id
        assert second.decode_config_json["batch_size"] == 1
        assert second.inference_metrics_json["rtf"] == 0.01
        assert second.confidence_calibrated is None and not second.is_adopted
        token = session.scalar(select(TokenSpan).where(TokenSpan.candidate_id == second_id))
        assert token is not None
        assert token.start_sample == 3 * SAMPLE_RATE
        assert token.provenance_json["native_model_time"] is True
