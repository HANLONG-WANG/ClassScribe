"""Explicit three-level local data deletion with scope validation."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from classscribe.db.models import Job, Recording
from classscribe.paths import AppPaths, PathSecurityError, validate_restricted_directory
from classscribe.security import parse_uuid


class DeletionLevel(StrEnum):
    DERIVED_ONLY = "derived_only"
    JOB = "job"
    ALL_LOCAL_DATA = "all_local_data"


DELETE_ALL_CONFIRMATION = "DELETE ALL CLASSSCRIBE DATA"


@dataclass(frozen=True, slots=True)
class DeletionReport:
    level: DeletionLevel
    removed: tuple[str, ...]
    preserved: tuple[str, ...]


def _remove_tree_contents(path: Path) -> list[str]:
    removed: list[str] = []
    if not path.exists():
        return removed
    for child in path.iterdir():
        removed.append(str(child))
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return removed


class DeletionService:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def delete_derived(self, job_id: str) -> DeletionReport:
        parse_uuid(job_id, field="job_id")
        job_root = self.paths.data_path("jobs", job_id)
        removed: list[str] = []
        for name in ("derived", "candidates"):
            removed.extend(_remove_tree_contents(job_root / name))
        for cache_root in (self.paths.cache / "resampled", self.paths.cache / "waveform-peaks"):
            cache_job = cache_root / job_id
            if cache_job.exists():
                removed.append(str(cache_job))
                shutil.rmtree(cache_job)
        return DeletionReport(
            DeletionLevel.DERIVED_ONLY,
            tuple(removed),
            (str(job_root / "exports"), "database decisions"),
        )

    def delete_job(self, session: Session, job_id: str) -> DeletionReport:
        parse_uuid(job_id, field="job_id")
        job = session.get(Job, job_id)
        if job is None:
            return DeletionReport(DeletionLevel.JOB, (), ())
        recording = session.get(Recording, job.recording_id)
        job_root = self.paths.data_path("jobs", job_id)
        session.delete(job)
        session.flush()
        other_jobs = session.scalar(
            select(func.count()).select_from(Job).where(Job.recording_id == job.recording_id)
        )
        roots = [job_root]
        if recording is not None and other_jobs == 0:
            parse_uuid(recording.id, field="recording_id")
            roots.append(self.paths.data_path("recordings", recording.id))
            session.delete(recording)
        staged: list[tuple[Path, Path]] = []

        def restore(_session: Session) -> None:
            for original, temporary in reversed(staged):
                if temporary.exists():
                    temporary.rename(original)
            staged.clear()

        def cleanup(_session: Session) -> None:
            for _, temporary in staged:
                shutil.rmtree(temporary)
            staged.clear()

        event.listen(session, "after_commit", cleanup, once=True)
        event.listen(session, "after_rollback", restore, once=True)
        try:
            for root in roots:
                if root.exists():
                    temporary = root.with_name(f".delete-{uuid4()}")
                    root.rename(temporary)
                    staged.append((root, temporary))
        except OSError:
            restore(session)
            raise
        return DeletionReport(DeletionLevel.JOB, tuple(str(root) for root, _ in staged), ())

    def clear_all(self, *, confirmation: str) -> DeletionReport:
        if confirmation != DELETE_ALL_CONFIRMATION:
            raise ValueError("exact destructive confirmation phrase is required")
        removed: list[str] = []
        for root in (
            self.paths.config,
            self.paths.data,
            self.paths.cache,
            self.paths.state,
            self.paths.runtime,
        ):
            if root.name != "classscribe" and root != self.paths.state / "run":
                raise PathSecurityError(f"refusing to clear unexpected root: {root}")
            if root.exists():
                validate_restricted_directory(root)
                removed.extend(_remove_tree_contents(root))
        return DeletionReport(DeletionLevel.ALL_LOCAL_DATA, tuple(removed), ())
