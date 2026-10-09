from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    Job,
    Recording,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.deletion import DELETE_ALL_CONFIRMATION, DeletionLevel, DeletionService
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import AppPaths
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


def create_job_data(
    paths: AppPaths, factory: sessionmaker[Session]
) -> tuple[str, Path, Path, Path]:
    from classscribe.security import SecureFileLocator

    job_id, directories = SecureFileLocator(paths).allocate_job()
    derived = directories[2] / "derived.bin"
    candidates = directories[3] / "candidate.json"
    export = directories[4] / "final.json"
    derived.write_bytes(b"derived")
    candidates.write_text("candidate", encoding="utf-8")
    export.write_text("final", encoding="utf-8")
    with factory.begin() as session:
        recording = Recording(
            source_name="lecture.wav",
            source_sha256="d" * 64,
            source_path=str(directories[1] / "source.upload"),
            duration_samples=16_000,
            sample_rate=16_000,
            channels=1,
        )
        job = Job(
            id=job_id,
            recording=recording,
            language_mode=LanguageMode.JAPANESE,
            profile_id="auto_best",
        )
        segment = TranscriptSegment(
            job=job,
            start_sample=0,
            end_sample=16_000,
            language=LanguageMode.JAPANESE,
        )
        decision = DecisionEvent(
            segment=segment,
            event_type="test",
            actor_type="automatic",
            input_json={},
            output_json={},
            rule_version="v1",
        )
        session.add(decision)
    return job_id, derived, candidates, export


def test_derived_deletion_preserves_export_and_database_decisions(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    job_id, derived, candidate, export = create_job_data(paths, factory)
    report = DeletionService(paths).delete_derived(job_id)
    assert report.level is DeletionLevel.DERIVED_ONLY
    assert not derived.exists()
    assert not candidate.exists()
    assert export.read_text(encoding="utf-8") == "final"
    with factory() as session:
        assert session.scalar(select(DecisionEvent.id)) is not None


def test_explicit_job_deletion_removes_job_files_and_database_chain(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    job_id, _, _, export = create_job_data(paths, factory)
    with factory.begin() as session:
        report = DeletionService(paths).delete_job(session, job_id)
        assert report.level is DeletionLevel.JOB
    assert not export.exists()
    with factory() as session:
        assert session.get(Job, job_id) is None
        assert session.scalar(select(DecisionEvent.id)) is None
        assert session.scalar(select(Recording.id)) is None


def test_clear_all_requires_exact_confirmation_and_stays_within_app_roots(tmp_path: Path) -> None:
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    sentinel = tmp_path / "outside.txt"
    sentinel.write_text("keep", encoding="utf-8")
    for root in (paths.config, paths.data, paths.cache, paths.state, paths.runtime):
        (root / "private").write_text("delete", encoding="utf-8")
    service = DeletionService(paths)
    with pytest.raises(ValueError, match="confirmation"):
        service.clear_all(confirmation="yes")
    report = service.clear_all(confirmation=DELETE_ALL_CONFIRMATION)
    assert report.level is DeletionLevel.ALL_LOCAL_DATA
    assert sentinel.read_text(encoding="utf-8") == "keep"
    persistent_roots = (paths.config, paths.data, paths.cache, paths.state)
    assert all(not any(root.iterdir()) for root in persistent_roots)


def test_recording_directory_is_shared_and_restored_on_rollback(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    first, _, _, _ = create_job_data(paths, factory)
    with factory.begin() as session:
        recording_id = session.get_one(Job, first).recording_id
        second = Job(recording_id=recording_id, language_mode=LanguageMode.ENGLISH, profile_id="en")
        session.add(second)
        session.flush()
        second_id = second.id
    root = paths.data_path("recordings", recording_id)
    root.mkdir(parents=True)
    (root / "source.upload").write_bytes(b"original")
    (root / "master.wav").write_bytes(b"canonical")
    service = DeletionService(paths)
    with factory.begin() as session:
        service.delete_job(session, first)
    assert root.exists()
    with pytest.raises(RuntimeError), factory.begin() as session:
        service.delete_job(session, second_id)
        raise RuntimeError("transaction failed")
    assert (root / "source.upload").read_bytes() == b"original"
    with factory.begin() as session:
        assert session.get(Job, second_id) is not None
        service.delete_job(session, second_id)
    assert not root.exists()
    assert not list(root.parent.glob(".delete-*"))


def add_candidate_revisions(factory: sessionmaker[Session], job_id: str) -> tuple[str, str]:
    # IDs deliberately put the older candidate first in ORM delete order.
    old_id, new_id = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
    with factory.begin() as session:
        segment = session.scalar(
            select(TranscriptSegment).where(TranscriptSegment.job_id == job_id)
        )
        assert segment is not None
        session.add(
            ASRCandidate(
                id=old_id,
                segment_id=segment.id,
                model_id="fixture",
                model_revision="v1",
                raw_text="old",
                normalized_text="old",
            )
        )
        session.flush()
        session.add(
            ASRCandidate(
                id=new_id,
                segment_id=segment.id,
                model_id="fixture",
                model_revision="v2",
                raw_text="new",
                normalized_text="new",
                supersedes_candidate_id=old_id,
            )
        )
        session.flush()
        session.add(
            TokenSpan(
                segment_id=segment.id,
                candidate_id=new_id,
                start_sample=0,
                end_sample=16000,
                token="word",
                normalized_token="word",
                provenance_json={},
            )
        )
    return old_id, new_id


@pytest.mark.parametrize("preloaded", [False, True])
def test_job_deletion_removes_candidate_revisions_and_tokens_without_fk_conflict(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path, preloaded: bool
) -> None:
    _, factory, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    job_id, _, _, export = create_job_data(paths, factory)
    old_id, new_id = add_candidate_revisions(factory, job_id)
    with factory.begin() as session:
        if preloaded:
            candidates = list(session.scalars(select(ASRCandidate)))
            assert any(candidate.supersedes_candidate_id == old_id for candidate in candidates)
        DeletionService(paths).delete_job(session, job_id)
    with factory() as session:
        assert session.get(Job, job_id) is None
        assert session.get(ASRCandidate, old_id) is None
        assert session.get(ASRCandidate, new_id) is None
        assert session.scalar(select(TokenSpan.id)) is None
        assert session.scalar(select(DecisionEvent.id)) is None
    assert not export.exists()


def test_failed_job_deletion_restores_candidate_history_and_staged_files(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    job_id, _, _, export = create_job_data(paths, factory)
    old_id, new_id = add_candidate_revisions(factory, job_id)
    with pytest.raises(RuntimeError), factory.begin() as session:
        DeletionService(paths).delete_job(session, job_id)
        assert not export.exists()
        raise RuntimeError("rollback")
    with factory() as session:
        assert session.get(Job, job_id) is not None
        assert session.get_one(ASRCandidate, new_id).supersedes_candidate_id == old_id
        assert session.scalar(select(TokenSpan.id)) is not None
    assert export.read_text() == "final"
    assert not list(export.parents[2].glob(".delete-*"))


def test_deletion_preserves_external_candidate_references_and_returns_a_clear_error(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    first, _, _, export = create_job_data(paths, factory)
    old_id, new_id = add_candidate_revisions(factory, first)
    second, _, _, second_export = create_job_data(paths, factory)
    with factory.begin() as session:
        segment = session.scalar(
            select(TranscriptSegment).where(TranscriptSegment.job_id == second)
        )
        assert segment is not None
        external = ASRCandidate(
            segment_id=segment.id,
            model_id="fixture",
            model_revision="external",
            raw_text="external",
            normalized_text="external",
            supersedes_candidate_id=old_id,
        )
        session.add(external)
        session.flush()
        external_id = external.id
    with (
        pytest.raises(ClassScribeError, match="其他任务引用") as rejected,
        factory.begin() as session,
    ):
        DeletionService(paths).delete_job(session, first)
    assert rejected.value.code is ErrorCode.JOB_STATE_CONFLICT
    with factory() as session:
        assert session.get(Job, first) is not None
        assert session.get_one(ASRCandidate, new_id).supersedes_candidate_id == old_id
        assert session.get_one(ASRCandidate, external_id).supersedes_candidate_id == old_id
    assert export.exists() and second_export.exists()
    with factory.begin() as session:
        DeletionService(paths).delete_job(session, second)
    with factory.begin() as session:
        DeletionService(paths).delete_job(session, first)
    assert not export.exists() and not second_export.exists()
