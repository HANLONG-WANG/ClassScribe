"""Add terminal synthetic jobs for manual pagination QA; no pipeline calls."""

# ruff: noqa: RUF001
import hashlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from classscribe.contracts import LanguageMode
from classscribe.db import create_sqlite_engine, make_session_factory
from classscribe.db.models import (
    Job,
    JobStage,
    JobStatus,
    Recording,
    TimingQuality,
    TranscriptSegment,
)

root = Path("/tmp/classscribe-manual-phase1/data/classscribe")
sessions = make_session_factory(create_sqlite_engine(root / "classscribe.sqlite3"))
source = Path(__file__).with_name("silence-2s.wav")
sha = hashlib.sha256(source.read_bytes()).hexdigest()
with sessions.begin() as s:
    for i in range(2, 32):
        rid = f"10000000-0000-4000-8000-{i:012d}"
        jid = f"20000000-0000-4000-8000-{i:012d}"
        if s.get(Job, jid):
            continue
        target = root / "recordings" / rid / "source" / "qa.wav"
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        s.add(
            Recording(
                id=rid,
                source_name=f"Phase1 分页测试 {i:02d}（人工）.wav",
                source_sha256=sha,
                source_path=str(target),
                duration_samples=32000,
                sample_rate=16000,
                channels=1,
            )
        )
        s.flush()
        s.add(
            Job(
                id=jid,
                recording_id=rid,
                language_mode=LanguageMode.CHINESE,
                profile_id="manual-qa-pagination",
                status=JobStatus.COMPLETED,
                stage=JobStage.COMPLETED,
                progress=1,
                created_at=datetime(2026, 9, 13, tzinfo=UTC) + timedelta(seconds=i),
                completed_at=datetime(2026, 9, 13, tzinfo=UTC) + timedelta(seconds=i),
                options_json={"manual_qa_fixture": True},
            )
        )
        s.flush()
        if i == 31:
            s.add(
                TranscriptSegment(
                    job_id=jid,
                    start_sample=0,
                    end_sample=32000,
                    speaker_id="test",
                    language=LanguageMode.CHINESE,
                    raw_text="跨页测试",
                    faithful_text="跨页测试。",
                    smart_corrected_text="跨页测试。",
                    quality_score=0.9,
                    timing_quality=TimingQuality.STRUCTURE,
                )
            )
print("Synthetic pagination fixture ready: 31 completed jobs, two with transcripts")
