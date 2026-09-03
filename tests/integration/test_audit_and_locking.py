from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.contracts import LanguageMode
from classscribe.db.models import ASRCandidate, DecisionEvent, Job, Recording, TranscriptSegment
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.jobs.audit import ActorType, AuditService, EditableLayer
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError


def make_segment(factory: sessionmaker[Session]) -> tuple[str, str]:
    with factory.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="b" * 64,
            source_path=f"jobs/{id(factory)}/recording",
            duration_samples=32_000,
            sample_rate=16_000,
            channels=1,
        )
        job = Job(
            recording=recording,
            language_mode=LanguageMode.ENGLISH,
            profile_id="auto_best",
        )
        segment = TranscriptSegment(
            job=job,
            start_sample=0,
            end_sample=16_000,
            language=LanguageMode.ENGLISH,
            raw_text="raw",
            faithful_text="faithful",
            smart_corrected_text="corrected",
        )
        candidate = ASRCandidate(
            segment=segment,
            model_id="model-a",
            model_revision="revision-1",
            raw_text="raw",
            normalized_text="normalized",
        )
        session.add_all((recording, candidate))
        session.flush()
        return segment.id, candidate.id


def test_old_browser_edit_gets_explicit_conflict_and_audit_is_preserved(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    segment_id, _ = make_segment(factory)
    service = AuditService()
    stale_session = factory()
    stale_session.get_one(TranscriptSegment, segment_id)
    try:
        with factory.begin() as fresh:
            result = service.edit_segment(
                fresh,
                segment_id=segment_id,
                expected_version=1,
                layer=EditableLayer.USER,
                text="new user edit",
                actor_type=ActorType.HUMAN,
                actor_id="local-user",
                rule_version="human-edit-v1",
            )
            assert result.version == 2
        with pytest.raises(ClassScribeError) as conflict:
            service.edit_segment(
                stale_session,
                segment_id=segment_id,
                expected_version=1,
                layer=EditableLayer.USER,
                text="stale overwrite",
                actor_type=ActorType.HUMAN,
                actor_id="local-user",
                rule_version="human-edit-v1",
            )
        assert conflict.value.code is ErrorCode.SEGMENT_VERSION_CONFLICT
        stale_session.rollback()
    finally:
        stale_session.close()
    with factory() as session:
        segment = session.get_one(TranscriptSegment, segment_id)
        assert segment.user_text == "new user edit"
        assert segment.version == 2
        events = list(session.scalars(select(DecisionEvent)))
        assert len(events) == 1
        assert events[0].input_json["version"] == 1
        assert events[0].output_json["version"] == 2


def test_orm_version_guard_rejects_concurrent_direct_update(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    segment_id, _ = make_segment(factory)
    first = factory()
    second = factory()
    try:
        first_segment = first.get_one(TranscriptSegment, segment_id)
        second_segment = second.get_one(TranscriptSegment, segment_id)
        second_segment.faithful_text = "second wins"
        second.commit()
        first_segment.faithful_text = "must conflict"
        with pytest.raises(StaleDataError):
            first.commit()
        first.rollback()
    finally:
        first.close()
        second.close()


def test_automatic_edit_cannot_overwrite_user_layer(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    segment_id, _ = make_segment(factory)
    with pytest.raises(ClassScribeError), factory.begin() as session:
        AuditService().edit_segment(
            session,
            segment_id=segment_id,
            expected_version=1,
            layer=EditableLayer.USER,
            text="forbidden",
            actor_type=ActorType.AUTOMATIC,
            actor_id=None,
            rule_version="automatic-v1",
        )


def test_adopted_candidate_cannot_be_deleted_but_unadopted_is_soft_deleted(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    segment_id, candidate_id = make_segment(factory)
    service = AuditService()
    with factory.begin() as session:
        service.adopt_candidate(
            session,
            candidate_id=candidate_id,
            actor_type=ActorType.AUTOMATIC,
            actor_id=None,
            rule_version="consensus-v1",
        )
    with pytest.raises(ClassScribeError) as adopted, factory.begin() as session:
        service.soft_delete_candidate(session, candidate_id)
    assert adopted.value.code is ErrorCode.CANDIDATE_ALREADY_ADOPTED

    with factory.begin() as session:
        replacement = ASRCandidate(
            segment_id=segment_id,
            model_id="model-a",
            model_revision="revision-2",
            raw_text="new",
            normalized_text="new",
            supersedes_candidate_id=candidate_id,
        )
        session.add(replacement)
        session.flush()
        replacement_id = replacement.id
        service.soft_delete_candidate(session, replacement_id)
    with factory() as session:
        replacement = session.get_one(ASRCandidate, replacement_id)
        assert replacement.deleted_at is not None
        adopted_candidate = session.get_one(ASRCandidate, candidate_id)
        assert adopted_candidate.is_adopted is True
