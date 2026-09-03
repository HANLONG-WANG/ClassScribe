from __future__ import annotations

from pathlib import Path

from classscribe.contracts import LanguageMode
from classscribe.db.models import DictationSession, DictationStatus
from classscribe.jobs.dictation import DictationHistoryService
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


def test_default_dictation_is_not_persisted_and_temporary_audio_is_cleaned(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    audio = tmp_path / "microphone.raw"
    audio.write_bytes(b"private audio")
    service = DictationHistoryService()
    with factory.begin() as session:
        record = service.begin(
            session,
            language_mode=LanguageMode.JAPANESE,
            profile_id="balanced",
            retain_history=False,
        )
        service.commit(record, committed_text="private dictation", temporary_audio=audio)
    assert not audio.exists()
    with factory() as session:
        assert list(session.scalars(select(DictationSession))) == []


def test_opted_in_history_retains_text_but_only_explicit_audio_path(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    temporary = tmp_path / "temporary.raw"
    retained = tmp_path / "retained.raw"
    temporary.write_bytes(b"temporary")
    retained.write_bytes(b"retained")
    service = DictationHistoryService()
    with factory.begin() as session:
        record = service.begin(
            session,
            language_mode=LanguageMode.ENGLISH,
            profile_id="balanced",
            retain_history=True,
        )
        assert record is not None
        service.commit(
            record,
            committed_text="kept by explicit opt-in",
            temporary_audio=temporary,
            retained_audio=retained,
        )
        session.flush()
        record_id = record.id
    assert not temporary.exists()
    assert retained.exists()
    with factory() as session:
        record = session.get_one(DictationSession, record_id)
        assert record.status is DictationStatus.COMMITTED
        assert record.committed_text == "kept by explicit opt-in"
        assert record.audio_path == str(retained)


def test_cancel_never_commits_preedit_or_temporary_audio(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, factory, _ = database
    audio = tmp_path / "temporary.raw"
    audio.write_bytes(b"partial")
    service = DictationHistoryService()
    with factory.begin() as session:
        record = service.begin(
            session,
            language_mode=LanguageMode.CHINESE,
            profile_id="balanced",
            retain_history=True,
        )
        assert record is not None
        service.cancel(record, temporary_audio=audio)
        session.flush()
        record_id = record.id
    assert not audio.exists()
    with factory() as session:
        record = session.get_one(DictationSession, record_id)
        assert record.status is DictationStatus.CANCELLED
        assert record.committed_text is None
        assert record.audio_path is None
