"""Synthetic manual QA data. No model or pipeline is invoked."""

# ruff: noqa: RUF001
import hashlib
import wave
from datetime import UTC, datetime
from pathlib import Path

from classscribe.contracts import LanguageMode
from classscribe.db import create_sqlite_engine, make_session_factory
from classscribe.db.models import (
    ASRCandidate,
    Job,
    JobStage,
    JobStatus,
    Recording,
    ReviewStatus,
    TimingQuality,
    TokenSpan,
    TranscriptSegment,
)

root = Path("/tmp/classscribe-manual-phase1/data/classscribe")
sessions = make_session_factory(create_sqlite_engine(root / "classscribe.sqlite3"))
rid = "10000000-0000-4000-8000-000000000001"
jid = "20000000-0000-4000-8000-000000000001"
with sessions.begin() as s:
    if s.get(Job, jid):
        raise SystemExit("Fixture exists; refusing to overwrite edits")
    source = root / "recordings" / rid / "source" / "qa-fixture.wav"
    source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(384000))
    s.add(
        Recording(
            id=rid,
            source_name="Phase1 人工测试稿（非模型输出）.wav",
            source_path=str(source),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            duration_samples=192000,
            sample_rate=16000,
            channels=1,
        )
    )
    s.flush()
    s.add(
        Job(
            id=jid,
            recording_id=rid,
            language_mode=LanguageMode.AUTO_MIXED,
            profile_id="manual-qa-fixture",
            status=JobStatus.COMPLETED,
            stage=JobStage.COMPLETED,
            progress=1,
            completed_at=datetime.now(UTC),
            options_json={
                "manual_qa_fixture": True,
                "language": "auto",
                "outputs": ["txt", "md", "json", "srt", "vtt", "csv"],
            },
        )
    )
    s.flush()
    texts = [
        ("zh", "teacher", "今天学习量子力学", "今天学习量子力学。", "今天学习量子力学。", 0.95),
        ("ja", "student", "これはテストです", "これはテストです。", "これはテストです。", 0.35),
        (
            "en",
            "teacher",
            "Machine learning costs 12 dollars",
            "Machine learning costs 12 dollars.",
            "Machine learning costs $12.",
            0.82,
        ),
    ]
    for i, (lang, speaker, raw, faithful, smart, quality) in enumerate(texts):
        sid = f"30000000-0000-4000-8000-{i + 1:012d}"
        s.add(
            TranscriptSegment(
                id=sid,
                job_id=jid,
                start_sample=i * 64000,
                end_sample=(i + 1) * 64000,
                speaker_id=speaker,
                language=LanguageMode(lang),
                raw_text=raw,
                faithful_text=faithful,
                smart_corrected_text=smart,
                quality_score=quality,
                timing_quality=TimingQuality.STRUCTURE if i == 1 else TimingQuality.ALIGNED,
                review_status=ReviewStatus.NEEDS_REVIEW if i == 1 else ReviewStatus.AUTO,
            )
        )
        s.flush()
        candidate = ASRCandidate(
            segment_id=sid,
            model_id="manual_qa_fixture",
            model_revision="0" * 40,
            raw_text=raw,
            normalized_text=faithful,
            confidence_raw=quality,
            confidence_calibrated=quality,
            quality_features_json={"manual_qa_fixture": True},
            warnings_json=[],
            decode_config_json={"synthetic": True},
            inference_metrics_json={},
        )
        s.add(candidate)
        s.flush()
        s.add(
            TokenSpan(
                segment_id=sid,
                candidate_id=None,
                start_sample=i * 64000,
                end_sample=(i + 1) * 64000,
                token=faithful,
                normalized_token=faithful,
                confidence=quality,
                provenance_json={
                    "manual_qa_fixture": True,
                    "candidate_sources": [{"candidate_id": candidate.id}],
                    "timing_source": "manual_fixture",
                },
            )
        )
print("Created synthetic completed job", jid, "with 3 segments")
