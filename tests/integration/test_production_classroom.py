from __future__ import annotations

import wave
from array import array
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from classscribe.api.schemas import JobCreate
from classscribe.api.service import ClassScribeService
from classscribe.audio.media import file_sha256
from classscribe.classroom import ClassroomPipeline, ProductionStageRunner
from classscribe.config import load_config
from classscribe.contracts import LanguageMode, ModelSelectionMode
from classscribe.db.models import (
    ASRCandidate,
    BenchmarkRun,
    BenchmarkStatus,
    ExportArtifact,
    Job,
    JobStatus,
    ProfileSetting,
    Recording,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import ModelEntry, ModelManager, SandboxedModelInvoker, load_registry
from classscribe.paths import AppPaths
from classscribe.timeline import SAMPLE_RATE
from classscribe_protocol import RPCRequest, RPCResponse
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker


class InstalledModels:
    def __init__(self, root: Path, identifiers: set[str]) -> None:
        self.root = root
        self.identifiers = identifiers

    def resolve_for_runtime(self, model_id: str) -> Path:
        if model_id not in self.identifiers:
            raise FileNotFoundError(model_id)
        return self.root / model_id


class FixtureInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, entry: ModelEntry, request: RPCRequest) -> RPCResponse:
        model_id = entry.id
        revision = entry.revision
        self.calls.append((model_id, request.method))
        start = int(request.params.get("start_sample", 0))
        end = int(request.params.get("end_sample", SAMPLE_RATE))
        if model_id == "firered_vad":
            return RPCResponse(
                request.request_id,
                request.job_id,
                True,
                model_id,
                revision,
                segments=(
                    {
                        "start_sample": start,
                        "end_sample": end,
                        "vad_score": 0.98,
                        "acoustic_class": "speech",
                        "source": "firered_vad",
                    },
                ),
            )
        if model_id == "moss_td_0_9b":
            return RPCResponse(
                request.request_id,
                request.job_id,
                True,
                model_id,
                revision,
                raw_text="これは生産経路の試験です。",
                normalized_text="これは生産経路の試験です。",
                language="ja",
                segments=(
                    {
                        "start_sample": start,
                        "end_sample": end,
                        "speaker_local": "M0",
                        "text": "これは生産経路の試験です。",
                        "confidence_raw": 0.95,
                        "structure_score": 0.95,
                        "acoustic_events": [],
                    },
                ),
            )
        if model_id == "pyannote_community_1":
            raise FileNotFoundError("optional diarization fallback is not installed")
        if model_id == "granite_speech_4_1_2b":
            text = "これは生産経路の試験です。"
            return RPCResponse(
                request.request_id,
                request.job_id,
                True,
                model_id,
                revision,
                raw_text=text,
                normalized_text=text,
                language="ja",
                segments=(
                    {
                        "start_sample": start,
                        "end_sample": end,
                        "text": text,
                        "confidence_raw": 0.96,
                        "words": [
                            {
                                "start_sample": start,
                                "end_sample": end,
                                "text": text,
                                "confidence_raw": 0.96,
                            }
                        ],
                    },
                ),
                metrics={"generated_tokens": 12, "inference_ms": 20.0},
            )
        raise AssertionError(f"unexpected model invocation: {model_id}")


def _write_wav(path: Path, duration_samples: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True)
    samples = array("h", (1200 if index % 32 < 16 else -1200 for index in range(duration_samples)))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    path.chmod(0o400)


def test_production_runner_completes_real_media_to_automatic_export(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path
) -> None:
    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    recording_id = str(uuid4())
    duration = 12 * SAMPLE_RATE
    source = paths.data_path("recordings", recording_id, "source", "lecture.wav")
    _write_wav(source, duration)
    options = {
        "language": "ja",
        "model_selection": "auto_best",
        "accuracy_mode": "balanced",
        "outputs": ["json"],
    }
    granite = registry.model("granite_speech_4_1_2b")
    manifest_sha256 = "f" * 64
    calibration = {
        "schema_version": 1,
        "model_id": granite.id,
        "model_revision": granite.revision,
        "language": "ja",
        "scenario": "classroom",
        "manifest_sha256": manifest_sha256,
        "method": "isotonic",
        "error_threshold": 0.2,
        "parameters": {"thresholds": [1.0], "values": [0.91]},
        "validation_brier": 0.01,
        "test_brier": 0.02,
        "training_sha256": "e" * 64,
        "created_at": "2026-09-04T00:00:00+00:00",
    }
    with sessions.begin() as session:
        recording = Recording(
            id=recording_id,
            source_name="lecture.wav",
            source_sha256=file_sha256(source),
            source_path=str(source),
            duration_samples=duration,
            sample_rate=SAMPLE_RATE,
            channels=1,
        )
        job = Job(
            recording=recording,
            language_mode=LanguageMode.JAPANESE,
            profile_id="balanced",
            options_json=options,
        )
        session.add(job)
        benchmark = BenchmarkRun(
            manifest_version="private-v1",
            status=BenchmarkStatus.COMPLETED,
            hardware_json={"gpu": "fixture"},
            parameters_json={
                "production_gold": True,
                "real_model_execution": True,
                "synthetic_gold": False,
                "manifest_sha256": manifest_sha256,
                "calibrations": [calibration],
            },
            metrics_json={},
            ranking_json=[
                {
                    "language": "ja",
                    "scenario": "classroom",
                    "models": [granite.id],
                    "passed": True,
                    "candidates": [
                        {
                            "model_id": granite.id,
                            "model_revision": granite.revision,
                            "eligible": True,
                            "metrics": {"normalized_cer": 0.1},
                        }
                    ],
                }
            ],
        )
        session.add(benchmark)
        session.flush()
        session.add(
            ProfileSetting(
                language="ja",
                scenario="classroom",
                config_json={"models": [granite.id], "history": []},
                benchmark_run_id=benchmark.id,
            )
        )
        job_id = job.id

    installed = {"firered_vad", "moss_td_0_9b", "granite_speech_4_1_2b"}
    invoker = FixtureInvoker()
    stage_runner = ProductionStageRunner(
        paths,
        load_config(environment={}),
        registry,
        cast(ModelManager, InstalledModels(tmp_path, installed)),
        cast(SandboxedModelInvoker, invoker),
    )
    pipeline = ClassroomPipeline(sessions, stage_runner)
    pipeline.initialize(job_id, options)
    pipeline.run_until_blocked(job_id)

    with sessions() as session:
        job = session.get_one(Job, job_id)
        segments = tuple(
            session.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.job_id == job_id)
                .order_by(TranscriptSegment.start_sample)
            )
        )
        artifact = session.scalar(select(ExportArtifact).where(ExportArtifact.job_id == job_id))
        candidate = session.scalar(
            select(ASRCandidate).where(ASRCandidate.segment_id == segments[0].id)
        )
        assert job.status is JobStatus.COMPLETED, (job.error_code, job.error_detail)
        assert len(segments) == 1
        assert segments[0].faithful_text == "これは生産経路の試験です。"
        assert segments[0].smart_corrected_text == "これは生産経路の試験です。"
        assert segments[0].token_spans
        assert candidate is not None and candidate.confidence_calibrated == 0.91
        assert any(
            token.candidate_id is None
            and token.provenance_json.get("reliability_locally_calibrated") is True
            for token in segments[0].token_spans
        )
        assert artifact is not None
        export_path = paths.data_path(*Path(artifact.relative_path).parts)
        assert export_path.is_file() and file_sha256(export_path) == artifact.sha256
    assert invoker.calls.count(("granite_speech_4_1_2b", "transcribe_batch")) == 1
    assert ("firered_vad", "vad") in invoker.calls
    assert ("moss_td_0_9b", "transcribe_batch") in invoker.calls


def test_service_preflight_rejects_before_persisting_a_job(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    recording_id = str(uuid4())
    source = paths.data_path("recordings", recording_id, "source", "lecture.wav")
    _write_wav(source, SAMPLE_RATE)
    with sessions.begin() as session:
        session.add(
            Recording(
                id=recording_id,
                source_name="lecture.wav",
                source_sha256=file_sha256(source),
                source_path=str(source),
                duration_samples=SAMPLE_RATE,
                sample_rate=SAMPLE_RATE,
                channels=1,
            )
        )
    manager = cast(ModelManager, InstalledModels(tmp_path, {"firered_vad", "moss_td_0_9b"}))
    invoker = cast(SandboxedModelInvoker, FixtureInvoker())
    pipeline = ClassroomPipeline(
        sessions,
        ProductionStageRunner(
            paths,
            load_config(environment={}),
            registry,
            manager,
            invoker,
        ),
    )
    service = ClassScribeService(sessions, paths, registry, pipeline=pipeline)
    monkeypatch.setattr(
        "classscribe.classroom.production.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    with pytest.raises(ClassScribeError) as failure:
        service.create_job(
            JobCreate(
                recording_id=recording_id,
                language=LanguageMode.JAPANESE,
                model_selection=ModelSelectionMode.MANUAL_PRIMARY,
                primary_model_id="granite_speech_4_1_2b",
            )
        )
    assert failure.value.code is ErrorCode.MODEL_NOT_FULLY_INSTALLED
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
