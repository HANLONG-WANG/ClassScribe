from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.contracts import LanguageMode
from classscribe.db.models import DecisionEvent, Job, Recording, TranscriptSegment
from classscribe.deletion import DELETE_ALL_CONFIRMATION, DeletionLevel, DeletionService
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
