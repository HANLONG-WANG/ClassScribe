"""Privacy-preserving persistence boundary for optional dictation history."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from classscribe.contracts import LanguageMode
from classscribe.db.models import DictationSession, DictationStatus


class DictationHistoryService:
    def begin(
        self,
        session: Session,
        *,
        language_mode: LanguageMode,
        profile_id: str,
        retain_history: bool,
    ) -> DictationSession | None:
        if not retain_history:
            return None
        record = DictationSession(
            language_mode=language_mode,
            profile_id=profile_id,
            retain_history=True,
        )
        session.add(record)
        return record

    def commit(
        self,
        record: DictationSession | None,
        *,
        committed_text: str,
        temporary_audio: Path | None,
        retained_audio: Path | None = None,
    ) -> None:
        if record is None:
            if temporary_audio is not None:
                temporary_audio.unlink(missing_ok=True)
            return
        record.status = DictationStatus.COMMITTED
        record.committed_text = committed_text
        record.audio_path = str(retained_audio) if retained_audio is not None else None
        if temporary_audio is not None and temporary_audio != retained_audio:
            temporary_audio.unlink(missing_ok=True)

    def cancel(self, record: DictationSession | None, *, temporary_audio: Path | None) -> None:
        if record is not None:
            record.status = DictationStatus.CANCELLED
            record.committed_text = None
            record.audio_path = None
        if temporary_audio is not None:
            temporary_audio.unlink(missing_ok=True)
