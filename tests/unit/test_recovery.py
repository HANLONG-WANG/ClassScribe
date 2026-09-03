from __future__ import annotations

import os
from pathlib import Path

from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    Job,
    Recording,
    TimingQuality,
    TranscriptSegment,
)
from classscribe.recovery import (
    DictationFailure,
    atomic_write_text,
    dictation_fallback,
    record_alignment_fallback,
    record_window_dedup_conflict,
    validate_candidate_timeline,
)
from classscribe.timeline import AudioSpan
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


def make_candidate(
    factory: sessionmaker[Session],
) -> tuple[str, str]:
    with factory.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="c" * 64,
            source_path=f"jobs/{id(factory)}/source",
            duration_samples=32_000,
            sample_rate=16_000,
            channels=1,
        )
        job = Job(
            recording=recording,
            language_mode=LanguageMode.CHINESE,
            profile_id="auto_best",
        )
        segment = TranscriptSegment(
            job=job,
            start_sample=10_000,
            end_sample=20_000,
            language=LanguageMode.CHINESE,
        )
        candidate = ASRCandidate(
            segment=segment,
            model_id="model",
            model_revision="revision",
            raw_text="raw",
            normalized_text="normalized",
        )
        session.add(candidate)
        session.flush()
        return segment.id, candidate.id


def test_atomic_export_replaces_complete_file_and_leaves_no_temp(tmp_path: Path) -> None:
    destination = tmp_path / "exports" / "transcript.json"
    atomic_write_text(destination, "first")
    atomic_write_text(destination, "second")
    assert destination.read_text(encoding="utf-8") == "second"
    assert not list(destination.parent.glob(".*.tmp"))
    assert os.stat(destination).st_size == len("second")


def test_dictation_failures_never_silently_commit_partial_text() -> None:
    daemon = dictation_fallback(DictationFailure.DAEMON_UNAVAILABLE)
    assert daemon.cancel_preedit is True
    assert daemon.commit_preedit is False
    assert daemon.pass_through_keys is True
    microphone = dictation_fallback(DictationFailure.MICROPHONE_DISCONNECTED)
    socket = dictation_fallback(DictationFailure.SOCKET_DISCONNECTED)
    assert microphone.cancel_preedit and not microphone.commit_preedit
    assert socket.cancel_preedit and not socket.commit_preedit
    oom = dictation_fallback(DictationFailure.GPU_OUT_OF_MEMORY)
    assert oom.fallback_target == "small_model_or_cpu"
    assert not oom.commit_preedit


def test_invalid_timing_rejects_whole_candidate_and_records_decision(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    segment_id, candidate_id = make_candidate(factory)
    with factory.begin() as session:
        segment = session.get_one(TranscriptSegment, segment_id)
        candidate = session.get_one(ASRCandidate, candidate_id)
        accepted = validate_candidate_timeline(
            session,
            candidate,
            segment,
            [AudioSpan(10_000, 15_000), AudioSpan(14_000, 19_000)],
        )
        assert accepted is False
    with factory() as session:
        candidate = session.get_one(ASRCandidate, candidate_id)
        assert candidate.is_valid is False
        assert candidate.warnings_json[0]["error_code"] == "CANDIDATE_TIMELINE_INVALID"
        assert session.scalar(select(DecisionEvent.event_type)) == "candidate_rejected"


def test_valid_timing_passes_without_audit_event(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    segment_id, candidate_id = make_candidate(factory)
    with factory.begin() as session:
        segment = session.get_one(TranscriptSegment, segment_id)
        candidate = session.get_one(ASRCandidate, candidate_id)
        assert validate_candidate_timeline(
            session,
            candidate,
            segment,
            [AudioSpan(10_000, 15_000), AudioSpan(15_000, 20_000)],
        )
    with factory() as session:
        assert list(session.scalars(select(DecisionEvent))) == []


def test_alignment_failure_uses_coarse_time_and_dedup_conflict_keeps_candidates(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    segment_id, candidate_id = make_candidate(factory)
    with factory.begin() as session:
        segment = session.get_one(TranscriptSegment, segment_id)
        segment.timing_quality = TimingQuality.ALIGNED
        record_alignment_fallback(session, segment, detail="low alignment score")
        record_window_dedup_conflict(session, segment, [candidate_id, "candidate-2"])
    with factory() as session:
        segment = session.get_one(TranscriptSegment, segment_id)
        assert segment.timing_quality is TimingQuality.STRUCTURE
        events = list(session.scalars(select(DecisionEvent).order_by(DecisionEvent.created_at)))
        assert {event.event_type for event in events} == {
            "alignment_fallback",
            "window_dedup_conflict",
        }
        conflict = next(event for event in events if event.event_type == "window_dedup_conflict")
        assert conflict.output_json["kept_all"] is True
