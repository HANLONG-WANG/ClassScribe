from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.contracts import LanguageMode
from classscribe.db.models import Job, Recording, SpeakerSpan, StructureSegmentRecord
from classscribe.errors import ClassScribeError
from classscribe.structure.models import STRUCTURE_TEXT_ROLE, StructureSegment
from classscribe.structure.persistence import (
    SpeakerTimelineSpan,
    StructureRepository,
)
from classscribe.timeline import AudioSpan
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

REVISION = "f" * 40


def _job(factory: sessionmaker[Session]) -> Job:
    recording = Recording(
        source_name="lecture.wav",
        source_sha256="a" * 64,
        source_path="jobs/structure/source.wav",
        duration_samples=1_000_000,
        sample_rate=16_000,
        channels=1,
    )
    job = Job(
        recording=recording,
        language_mode=LanguageMode.JAPANESE,
        profile_id="auto_best",
    )
    with factory.begin() as session:
        session.add(job)
    return job


def _segment(start: int, end: int, text: str = "coarse") -> StructureSegment:
    return StructureSegment(
        window_ordinal=0,
        span=AudioSpan(start, end),
        speaker_local="S01",
        speaker_global="SPEAKER_01",
        text=text,
        acoustic_events=("lecture",),
        source_model="moss_td_0_9b",
        source_revision=REVISION,
        selection_score=0.7,
        provenance={
            "absolute_samples": True,
            "adopted_as_final": False,
            "source_model": "moss_td_0_9b",
            "source_revision": REVISION,
        },
    )


def _speaker_span(start: int, end: int) -> SpeakerTimelineSpan:
    return SpeakerTimelineSpan(
        window_ordinal=0,
        span=AudioSpan(start, end),
        speaker_global="SPEAKER_01",
        speaker_local="S01",
        overlap=False,
        source_model="moss_td_0_9b",
        source_revision=REVISION,
        confidence=0.7,
        provenance={"absolute_samples": True},
    )


def test_window_replace_is_atomic_idempotent_and_keeps_structure_text_nonfinal(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job = _job(factory)
    repository = StructureRepository(factory)
    repository.replace_window(job.id, 0, (_segment(0, 100_000),), (_speaker_span(0, 100_000),))
    repository.replace_window(
        job.id,
        0,
        (_segment(10_000, 110_000, "replacement"),),
        (_speaker_span(10_000, 110_000),),
    )
    with factory() as session:
        record = session.scalar(select(StructureSegmentRecord))
        speaker = session.scalar(select(SpeakerSpan))
        assert record is not None and speaker is not None
        assert session.scalar(select(func.count(StructureSegmentRecord.id))) == 1
        assert session.scalar(select(func.count(SpeakerSpan.id))) == 1
        assert record.coarse_text == "replacement"
        assert record.text_role == STRUCTURE_TEXT_ROLE
        assert record.adopted_as_final is False
        assert record.provenance_json["absolute_samples"] is True
        assert speaker.source_revision == REVISION

    with pytest.raises(ClassScribeError, match="exceeds canonical"):
        repository.replace_window(
            job.id,
            0,
            (_segment(900_000, 1_100_000),),
            (_speaker_span(900_000, 1_100_000),),
        )
    with factory() as session:
        assert session.scalar(select(StructureSegmentRecord.coarse_text)) == "replacement"


def test_speaker_display_name_is_job_local_and_does_not_mutate_raw_diarization(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job = _job(factory)
    repository = StructureRepository(factory)
    repository.replace_window(job.id, 0, (_segment(0, 100_000),), (_speaker_span(0, 100_000),))
    repository.set_display_name(job.id, "SPEAKER_01", "老师")
    repository.set_display_name(job.id, "SPEAKER_01", "講師")
    assert repository.display_names(job.id) == {"SPEAKER_01": "講師"}
    with factory() as session:
        raw = session.scalar(select(SpeakerSpan))
        assert raw is not None
        assert raw.speaker_global_id == "SPEAKER_01"
        assert raw.speaker_local_id == "S01"


def test_database_constraint_rejects_promoting_structure_text_to_final(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    job = _job(factory)
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(
            StructureSegmentRecord(
                job_id=job.id,
                window_ordinal=0,
                start_sample=0,
                end_sample=10,
                coarse_text="must remain a candidate",
                acoustic_events_json=[],
                overlap=False,
                exclusive=True,
                fallback=False,
                source_model="moss_td_0_9b",
                source_revision=REVISION,
                selection_score=0.5,
                text_role=STRUCTURE_TEXT_ROLE,
                adopted_as_final=True,
                provenance_json={"absolute_samples": True, "adopted_as_final": False},
            )
        )
