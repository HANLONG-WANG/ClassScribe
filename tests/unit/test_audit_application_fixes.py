from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from classscribe.api.schemas import (
    BenchmarkCreate,
    GlossaryCreate,
    GlossaryTermInput,
    GlossaryTermsUpdate,
    SegmentMerge,
)
from classscribe.benchmark import BenchmarkReport
from classscribe.classroom import ClassroomPipeline, ComposableStageRunner
from classscribe.classroom.pipeline import PipelineEventBroker
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    BenchmarkRun,
    CheckpointStatus,
    GlossaryTerm,
    Job,
    JobCheckpoint,
    JobStage,
    JobStatus,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError
from classscribe.jobs.state_machine import CheckpointSpec, JobStateMachine
from sqlalchemy import func, select

from tests.integration.test_api_v1 import service_fixture
from tests.unit.test_classroom_pipeline import make_job


def test_paused_checkpoint_is_repaired_on_restart_without_unpausing(tmp_path: Path) -> None:
    service, sessions = service_fixture(tmp_path)
    identifier = make_job(sessions)
    pipeline = ClassroomPipeline(sessions, ComposableStageRunner({"audio_import": lambda *_: None}))
    pipeline.initialize(identifier, {})
    with sessions.begin() as session:
        job = session.get_one(Job, identifier)
        pipeline.state.start(job)
        checkpoint = pipeline.state.first_incomplete(job)
        assert checkpoint is not None
        pipeline.state.begin_checkpoint(job, checkpoint)
        pipeline.state.pause(job)
    assert pipeline.recover() == ()
    assert pipeline.snapshot(identifier)["status"] == "paused"
    pipeline.resume(identifier)
    assert pipeline.run_next(identifier)
    with sessions() as session:
        job = session.get_one(Job, identifier)
        assert job.checkpoints[0].status is CheckpointStatus.COMPLETED
    service.sessions.kw["bind"].dispose()


def test_checkpoint_creation_is_idempotent_including_null_segment(tmp_path: Path) -> None:
    _, sessions = service_fixture(tmp_path)
    identifier = make_job(sessions)
    machine = JobStateMachine()
    spec = CheckpointSpec(JobStage.AUDIO_IMPORT, "normalize_audio_master", 0, {"version": 1})
    with sessions.begin() as session:
        job = session.get_one(Job, identifier)
        machine.create_checkpoints(session, job, [spec, spec])
        session.flush()
        machine.create_checkpoints(session, job, [spec])
        assert session.scalar(select(func.count()).select_from(JobCheckpoint)) == 1
        with pytest.raises(ClassScribeError, match="different parameters"):
            machine.create_checkpoints(
                session,
                job,
                [CheckpointSpec(JobStage.AUDIO_IMPORT, spec.checkpoint_key, 0, {"version": 2})],
            )


def test_segment_rerun_invalidates_auto_export_and_preserves_historical_files(
    tmp_path: Path,
) -> None:
    service, sessions = service_fixture(tmp_path)
    identifier = make_job(sessions)
    exported: list[str] = []

    def handle(session: Any, job: Any, checkpoint: Any) -> None:
        if checkpoint.checkpoint_key == "moss_structure":
            session.add(
                TranscriptSegment(
                    job_id=job.id,
                    start_sample=0,
                    end_sample=16000,
                    language=LanguageMode.ENGLISH,
                )
            )
        if checkpoint.checkpoint_key == "automatic_exports":
            exported.append(job.id)

    pipeline = ClassroomPipeline(
        sessions,
        ComposableStageRunner(
            {
                key: handle
                for key in (
                    "audio_import",
                    "audio_qc",
                    "vad",
                    "lid",
                    "structure",
                    "transcription",
                    "quality",
                    "postprocess",
                    "alignment",
                    "export",
                )
            }
        ),
    )
    pipeline.initialize(identifier, {})
    pipeline.run_until_blocked(identifier)
    with sessions() as session:
        segment = session.scalar(
            select(TranscriptSegment).where(TranscriptSegment.job_id == identifier)
        )
        assert segment is not None
        segment_id = segment.id
    historical = service.paths.data_path("jobs", identifier, "exports", "previous.txt")
    historical.parent.mkdir(parents=True)
    historical.write_text("previous snapshot")
    pipeline.retry_segment(identifier, segment_id)
    assert pipeline.snapshot(identifier)["progress"] < 1
    pipeline.run_until_blocked(identifier)
    assert len(exported) == 2
    assert historical.read_text() == "previous snapshot"


def test_merge_respects_explicit_empty_user_layer(tmp_path: Path) -> None:
    service, sessions = service_fixture(tmp_path)
    identifier = make_job(sessions)
    with sessions.begin() as session:
        left = TranscriptSegment(
            job_id=identifier,
            start_sample=0,
            end_sample=16000,
            language=LanguageMode.ENGLISH,
            faithful_text="removed",
            user_text="",
        )
        right = TranscriptSegment(
            job_id=identifier,
            start_sample=16000,
            end_sample=32000,
            language=LanguageMode.ENGLISH,
            faithful_text="kept",
        )
        session.add_all([left, right])
        session.flush()
        identifiers = (left.id, right.id)
    merged = service.merge_segments(identifiers[0], SegmentMerge(segment_ids=identifiers))
    assert service.segment(merged["segment_id"])["user_text"] == "kept"


def test_atomic_term_language_edit_rejects_collision_without_overwrite(tmp_path: Path) -> None:
    service, sessions = service_fixture(tmp_path)
    glossary = service.create_glossary(GlossaryCreate(name="course"))
    english = GlossaryTermInput(canonical="CPU", reading="see pee you", language="en")
    chinese = GlossaryTermInput(canonical="CPU", reading="central processor", language="zh")
    service.update_terms(glossary["id"], GlossaryTermsUpdate(terms=(english, english, chinese)))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GlossaryTerm)) == 2
    original = service.glossary(glossary["id"])
    english_id = next(t["id"] for t in original["terms"] if t["language"] == "en")
    with pytest.raises(ClassScribeError, match="目标语言"):
        service.update_term(glossary["id"], english_id, chinese)
    assert service.glossary(glossary["id"])["terms"] == original["terms"]
    japanese = english.model_copy(update={"language": "ja", "reading": "shi pi yu"})
    changed = service.update_term(glossary["id"], english_id, japanese)
    assert next(t for t in changed["terms"] if t["id"] == english_id)["language"] == "ja"


@pytest.mark.parametrize("days", [True, 0, -1, 3651, "30"])
def test_retention_rejects_invalid_values(tmp_path: Path, days: Any) -> None:
    service, _ = service_fixture(tmp_path)
    with pytest.raises(ClassScribeError, match="derived_days"):
        service.update_settings({"retention": {"derived_days": days}})
    assert "retention" not in service.settings()


def test_retention_cleans_only_expired_disposable_terminal_artifacts(tmp_path: Path) -> None:
    service, sessions = service_fixture(tmp_path)
    identifiers = [make_job(sessions) for _ in range(3)]
    with sessions.begin() as session:
        for identifier, status in zip(
            identifiers,
            (JobStatus.COMPLETED, JobStatus.RUNNING, JobStatus.FAILED),
            strict=True,
        ):
            job = session.get_one(Job, identifier)
            job.status = status
            job.completed_at = datetime.now(UTC) - timedelta(days=90)
    for identifier in identifiers:
        for directory in ("derived", "exports", "source"):
            path = service.paths.data_path("jobs", identifier, directory, "keep.txt")
            path.parent.mkdir(parents=True)
            path.write_text("evidence")
    service.update_settings({"retention": {"derived_days": 30}})
    assert service.cleanup_retained_derived()["jobs_cleaned"] == 1
    for identifier in identifiers:
        assert service.paths.data_path("jobs", identifier, "exports", "keep.txt").is_file()
        assert service.paths.data_path("jobs", identifier, "source", "keep.txt").is_file()
    for identifier in identifiers[1:]:
        assert service.paths.data_path("jobs", identifier, "derived", "keep.txt").is_file()
    with pytest.raises(ClassScribeError, match="尚不支持"):
        service.update_settings({"ibus": {"save_audio": True}})


def test_broker_global_bound_and_monotonic_sequence_survive_eviction() -> None:
    broker = PipelineEventBroker(retained_jobs=2, retained_per_job=2)
    first = broker.publish("old", "job_completed")
    assert first is not None
    for identifier in ("second", "third", "fourth"):
        broker.publish(identifier, "job_completed")
    assert broker.after("old") == ()
    assert len(broker._events) <= 2
    last = broker.publish("old", "pipeline_recovered")
    assert last is not None and last.sequence > first.sequence
    broker.forget("old")
    assert broker.after("old") == () and broker.recent("old") == ()


def test_benchmark_without_inputs_is_rejected_without_phantom_record(tmp_path: Path) -> None:
    service, sessions = service_fixture(tmp_path)
    with pytest.raises(ClassScribeError, match="基准需要"):
        service.create_benchmark(BenchmarkCreate(manifest_version="v1"))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(BenchmarkRun)) == 0


def test_benchmark_background_failure_and_crash_recovery_finish_record(tmp_path: Path) -> None:
    service, sessions = service_fixture(tmp_path)
    for name in ("gold.jsonl", "predictions.jsonl"):
        service.paths.data_path("benchmarks", name).write_text("invalid")

    async def scenario() -> None:
        run = service.create_benchmark(
            BenchmarkCreate(
                manifest_version="v1",
                parameters={
                    "manifest_path": "gold.jsonl",
                    "predictions_path": "predictions.jsonl",
                },
            )
        )
        await service.close_background_jobs()
        assert service.benchmark(run["id"])["status"] == "failed"

    asyncio.run(scenario())
    with sessions.begin() as session:
        session.add(
            BenchmarkRun(
                manifest_version="orphan", status="running", hardware_json={}, parameters_json={}
            )
        )
    assert service.recover_background_jobs() == 1


def test_benchmark_background_success_persists_report(tmp_path: Path, monkeypatch: Any) -> None:
    service, _ = service_fixture(tmp_path)
    for name in ("gold.jsonl", "predictions.jsonl"):
        service.paths.data_path("benchmarks", name).write_text("{}")
    report = BenchmarkReport(
        schema_version=1,
        manifest_version="v1",
        manifest_sha256="a" * 64,
        status="completed",
        hardware={},
        parameters={"real_model_execution": False},
        coverage={},
        items=(),
        calibrations=(),
        rankings=(),
    )

    class Runner:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run(self, _predictions: Any) -> BenchmarkReport:
            return report

    monkeypatch.setattr("classscribe.api.service.BenchmarkRunner", Runner)
    monkeypatch.setattr("classscribe.api.service.load_gold_manifest", lambda _path: ())
    monkeypatch.setattr("classscribe.api.service.load_predictions", lambda _path: ())

    async def scenario() -> None:
        run = service.create_benchmark(
            BenchmarkCreate(
                manifest_version="v1",
                parameters={
                    "manifest_path": "gold.jsonl",
                    "predictions_path": "predictions.jsonl",
                },
            )
        )
        await service.close_background_jobs()
        saved = service.benchmark(run["id"])
        assert saved["status"] == "completed"
        assert saved["parameters"]["real_model_execution"] is False

    asyncio.run(scenario())


def test_clear_blocks_background_score_and_stops_future_maintenance(tmp_path: Path) -> None:
    from classscribe.deletion import DELETE_ALL_CONFIRMATION

    service, _ = service_fixture(tmp_path)

    async def scenario() -> None:
        waiting = asyncio.create_task(asyncio.sleep(60))
        service._benchmark_tasks.add(waiting)
        try:
            with pytest.raises(ClassScribeError, match="活动基准"):
                service.clear_local_data(DELETE_ALL_CONFIRMATION)
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            service._benchmark_tasks.discard(waiting)
        assert service.clear_local_data(DELETE_ALL_CONFIRMATION)["restart_required"] is True
        assert service.cleanup_retained_derived() == {"jobs_cleaned": 0, "errors": 0}
        with pytest.raises(ClassScribeError, match="重启"):
            service.create_benchmark(BenchmarkCreate(manifest_version="v1"))

    asyncio.run(scenario())


def test_cli_uses_user_config_for_diagnostics_and_prints_ipv6_url(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    from classscribe import cli, doctor
    from classscribe.config import ConfigError
    from classscribe.paths import AppPaths

    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    config = paths.config / "config.yaml"
    config.write_text("server:\n  port: 9000\n  host: '::1'\n")
    monkeypatch.setattr("classscribe.cli.AppPaths.from_environment", lambda: paths)
    assert cli.main(["--print-url"]) == 0
    assert capsys.readouterr().out.strip() == "http://[::1]:9000"
    config.write_text("server:\n  port: -1\n")
    with pytest.raises(ConfigError):
        doctor.main([])


def test_recovery_preserves_uninitialized_paused_queue_item(tmp_path: Path) -> None:
    _, sessions = service_fixture(tmp_path)
    identifier = make_job(sessions)
    pipeline = ClassroomPipeline(sessions, ComposableStageRunner({}))
    pipeline.pause(identifier)
    assert pipeline.recover() == ()
    assert pipeline.snapshot(identifier)["status"] == "paused"
