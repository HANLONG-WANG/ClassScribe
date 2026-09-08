from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from classscribe.classroom import ProductionStageRunner
from classscribe.config import load_config
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    Job,
    JobCheckpoint,
    JobStage,
    LanguageSpan,
    Recording,
    SpeechRegion,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import ModelEntry, ModelManager, SandboxedModelInvoker, load_registry
from classscribe.paths import AppPaths
from classscribe.timeline import SAMPLE_RATE
from classscribe_protocol import RPCRequest, RPCResponse
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


@pytest.mark.parametrize("worker_failure", [False, True])
def test_lid_skips_silence_smooths_unknowns_and_preserves_real_worker_errors(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_failure: bool,
) -> None:
    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    runner = ProductionStageRunner(
        paths,
        load_config(environment={}),
        load_registry(Path("config/model-registry.v1.yaml")),
        cast(ModelManager, object()),
        cast(SandboxedModelInvoker, object()),
    )
    calls: list[int] = []

    async def classify(job: Job, entry: ModelEntry, request: RPCRequest) -> RPCResponse:
        start, end = request.params["start_sample"], request.params["end_sample"]
        calls.append(start)
        if worker_failure:
            return RPCResponse(
                request.request_id,
                job.id,
                False,
                entry.id,
                entry.revision,
                error_code="internal_error",
                error_detail="classifier execution failed",
            )
        unknown = start == 5 * SAMPLE_RATE
        weak = start == 7.5 * SAMPLE_RATE
        language = None if unknown else "en" if weak else "ja"
        probability = 0.243 if unknown or weak else 0.99
        return RPCResponse(
            request.request_id,
            job.id,
            True,
            entry.id,
            entry.revision,
            language=language,
            segments=({"start_sample": start, "end_sample": end, "confidence_raw": probability},),
            result={
                "language_status": "unknown" if unknown else "classified",
                "raw_language": "pa" if unknown else language,
            },
        )

    monkeypatch.setattr(runner, "_window_call", classify)
    with sessions.begin() as session:
        recording = Recording(
            source_name="sample.wav",
            source_path="sample.wav",
            source_sha256="a" * 64,
            duration_samples=35 * SAMPLE_RATE,
            channels=1,
            sample_rate=SAMPLE_RATE,
        )
        job = Job(recording=recording, language_mode=LanguageMode.AUTO_MIXED, profile_id="balanced")
        session.add(job)
        session.flush()
        for start, end in ((0, 20), (30, 35)):
            session.add(
                SpeechRegion(
                    job_id=job.id,
                    start_sample=start * SAMPLE_RATE,
                    end_sample=end * SAMPLE_RATE,
                    vad_score=0.99,
                    acoustic_class="speech",
                    source="firered_vad",
                )
            )
        checkpoint = JobCheckpoint(
            job_id=job.id,
            stage=JobStage.LID,
            checkpoint_key="lid",
            position=0,
            parameter_hash="a" * 64,
        )
        session.add(checkpoint)
        session.flush()
        if worker_failure:
            with pytest.raises(ClassScribeError) as error:
                runner._lid(session, job, checkpoint)
            assert error.value.code is ErrorCode.LID_FAILED
            assert "classifier execution failed" in error.value.detail
            assert "0.0-5.0" in error.value.detail
            return
        runner._lid(session, job, checkpoint)
        session.flush()
        rows = list(session.scalars(select(LanguageSpan).where(LanguageSpan.job_id == job.id)))
        assert len(rows) == 1 and rows[0].language is LanguageMode.JAPANESE
        assert rows[0].end_sample == 35 * SAMPLE_RATE
        assert 360000 not in calls  # The original failing 22.5-27.5s window has no speech.
        reasons = {item["reason"] for item in rows[0].decision_json["observations"]}
        assert {"no_speech", "unsupported_language", "low_confidence"} <= reasons
