from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from classscribe.audio.clips import create_recording_clip
from classscribe.audio.media import file_sha256
from classscribe.classroom.online import ProviderStageRunner
from classscribe.classroom.pipeline import ClassroomPipeline
from classscribe.classroom.production import ProductionStageRunner
from classscribe.config import load_config
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    AppSetting,
    Job,
    JobCheckpoint,
    JobStatus,
    OnlineRequestAttempt,
    Recording,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError
from classscribe.models import ModelManager, SandboxedModelInvoker, load_registry
from classscribe.paths import AppPaths
from sqlalchemy import select


def setup_online(
    database: Any, tmp_path: Path
) -> tuple[Any, ProviderStageRunner, ClassroomPipeline, str]:
    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path)

    class Local:
        boundary = None

        def preflight(self, *args: Any) -> None:
            raise AssertionError("online preflight must not load local models")

        def run(self, session: Any, job: Job, checkpoint: JobCheckpoint) -> None:
            assert checkpoint.checkpoint_key in {
                "upload_validate",
                "normalize_audio_master",
                "automatic_exports",
            }
            if checkpoint.checkpoint_key == "normalize_audio_master":
                path = paths.data_path(
                    "recordings", job.recording_id, "derived", "audio_master.wav"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(path), "wb") as output:
                    output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    output.writeframes(b"\0\0" * 160000)

    config = load_config(environment={})
    runner = ProviderStageRunner(Local(), paths, config)
    runner.credentials.configure("https://eastus.api.cognitive.microsoft.com", "secret-marker")
    pipeline = ClassroomPipeline(sessions, runner)
    with sessions.begin() as session:
        recording = Recording(
            source_name="input.wav",
            source_path="input.wav",
            source_sha256="a" * 64,
            duration_samples=160000,
            sample_rate=16000,
            channels=1,
        )
        job = Job(
            recording=recording,
            language_mode=LanguageMode.JAPANESE,
            profile_id="balanced",
            options_json={"provider": "azure_mai", "language": "ja"},
        )
        session.add(job)
        session.flush()
        identifier = job.id
    pipeline.initialize(identifier, {"provider": "azure_mai", "language": "ja"})
    return sessions, runner, pipeline, identifier


def test_online_network_has_no_write_transaction_and_import_is_idempotent(
    database: Any, tmp_path: Path
) -> None:
    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    calls: list[int] = []

    def transcribe(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        # A second connection can write while network work is running.
        with sessions.begin() as session:
            session.add(AppSetting(key="during-network", value_json={"ok": True}))
        calls.append(1)
        return json.dumps(
            {
                "phrases": [
                    {
                        "text": "今日は晴れです。",
                        "offsetMilliseconds": 100,
                        "durationMilliseconds": 1000,
                        "speaker": 0,
                    }
                ]
            }
        ).encode(), "request-id"

    runner.client.transcribe = transcribe  # type: ignore[method-assign]
    runner.preflight({"provider": "azure_mai"})
    pipeline.run_until_blocked(identifier)
    with sessions.begin() as session:
        job = session.get_one(Job, identifier)
        assert job.status == JobStatus.COMPLETED, job.error_detail
        assert {checkpoint.checkpoint_key for checkpoint in job.checkpoints} == {
            "upload_validate",
            "normalize_audio_master",
            "mai_request",
            "mai_import",
            "automatic_exports",
        }
        attempt = session.scalar(select(OnlineRequestAttempt))
        assert attempt.status == "imported"
        assert Path(attempt.response_path).is_file()
        runner._import(session, job)
        segments = list(session.scalars(select(TranscriptSegment)))
        assert len(segments) == 1
        assert segments[0].start_sample == 1600
        assert segments[0].end_sample == 17600
        assert segments[0].faithful_text == "今日は晴れです。"
        assert "secret-marker" not in json.dumps(job.options_json)
    runner._request(sessions, identifier)
    assert len(calls) == 1


def test_uncertain_send_is_never_repeated(database: Any, tmp_path: Path) -> None:
    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    calls: list[int] = []

    def transcribe(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        calls.append(1)
        raise OSError("secret-marker")

    runner.client.transcribe = transcribe  # type: ignore[method-assign]
    pipeline.run_until_blocked(identifier)
    with sessions() as session:
        job = session.get_one(Job, identifier)
        assert job.status == JobStatus.FAILED
        assert "secret-marker" not in job.error_detail
        assert session.scalar(select(OnlineRequestAttempt)).status == "uncertain"
    with pytest.raises(ClassScribeError, match="不会自动重发"):
        runner._request(sessions, identifier)
    assert len(calls) == 1


def test_selected_provider_controls_preflight(database: Any, tmp_path: Path) -> None:
    _, runner, _, _ = setup_online(database, tmp_path)
    runner.preflight({"provider": "azure_mai"})
    with pytest.raises(AssertionError, match="online preflight must not load local models"):
        runner.preflight({"provider": "local"})


def test_recovers_published_response_without_credentials_or_network(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.setattr(
        runner.client,
        "transcribe",
        lambda *args, **kwargs: (
            b'{"phrases":[{"text":"saved text"}]}',
            "request",
        ),
    )
    assert pipeline.run_next(identifier)
    assert pipeline.run_next(identifier)
    assert pipeline.run_next(identifier)
    with sessions.begin() as session:
        attempt = session.scalar(select(OnlineRequestAttempt))
        attempt.status = "sending"
        attempt.response_path = None
    runner.credentials.clear()
    runner.config = load_config(environment={})
    runner._request(sessions, identifier)
    pipeline.run_until_blocked(identifier)
    with sessions() as session:
        assert session.get_one(Job, identifier).status == JobStatus.COMPLETED
        assert session.scalar(select(TranscriptSegment)).faithful_text == "saved text"
        assert session.scalar(select(OnlineRequestAttempt)).status == "imported"


def test_real_clip_normalization_online_import_and_exports(database: Any, tmp_path: Path) -> None:
    from classscribe.db.models import ExportArtifact, TokenSpan

    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path)
    paths.ensure()
    recording_id = str(uuid4())
    source = paths.data_path("recordings", recording_id, "source", "input.wav")
    source.parent.mkdir(parents=True)
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 48000)
    with sessions.begin() as session:
        session.add(
            Recording(
                id=recording_id,
                source_name="input.wav",
                source_path=str(source),
                source_sha256=file_sha256(source),
                duration_samples=48000,
                sample_rate=16000,
                channels=1,
            )
        )
    clip_id = create_recording_clip(sessions, paths, recording_id, str(uuid4()), 1234, 30000)
    config = load_config(environment={})
    local = ProductionStageRunner(
        paths,
        config,
        load_registry(Path("config/model-registry.v1.yaml")),
        ModelManager(paths.cache / "models"),
        cast(SandboxedModelInvoker, object()),
    )
    runner = ProviderStageRunner(local, paths, config)
    runner.credentials.configure("https://eastus.api.cognitive.microsoft.com", "secret-marker")
    options = {"provider": "azure_mai", "language": "en", "outputs": ["json", "md", "srt", "vtt"]}
    runner.preflight(options)
    pipeline = ClassroomPipeline(sessions, runner)
    with sessions.begin() as session:
        job = Job(
            recording_id=clip_id,
            language_mode=LanguageMode.ENGLISH,
            profile_id="balanced",
            options_json=options,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    def transcribe(
        endpoint: str, key: str, audio: Path, *args: Any, **kwargs: Any
    ) -> tuple[bytes, str]:
        assert audio.suffix == ".flac"
        assert audio.read_bytes().startswith(b"fLaC")
        return json.dumps(
            {
                "phrases": [
                    {
                        "text": "Hello world.",
                        "offsetMilliseconds": 100,
                        "durationMilliseconds": 800,
                        "words": [
                            {
                                "text": "Hello",
                                "offsetMilliseconds": 100,
                                "durationMilliseconds": 200,
                            },
                            {
                                "text": "world.",
                                "offsetMilliseconds": 400,
                                "durationMilliseconds": 300,
                            },
                        ],
                    },
                    {"text": "Untimed text."},
                ]
            }
        ).encode(), "test-request"

    runner.client.transcribe = transcribe  # type: ignore[method-assign]
    pipeline.initialize(job_id, options)
    pipeline.run_until_blocked(job_id)
    pipeline.refresh_segments(job_id)
    with sessions() as session:
        job = session.get_one(Job, job_id)
        assert job.status == JobStatus.COMPLETED, job.error_detail
        assert not any(checkpoint.segment_id for checkpoint in job.checkpoints)
        assert len(list(session.scalars(select(TokenSpan)))) == 2
        artifacts = list(session.scalars(select(ExportArtifact)))
        assert len(artifacts) == 8
        for artifact in artifacts:
            content = paths.data_path(artifact.relative_path).read_text()
            assert "secret-marker" not in content
            if artifact.output_format == "json":
                records = json.loads(content)["records"]
                timed = next(item for item in records if item["text"] == "Hello world.")
                assert timed["start_sample"] == 1600
                assert timed["source"]["start_sample"] == 1234 + 1600
                assert any(item["text"] == "Untimed text." for item in records)
            if artifact.output_format in {"srt", "vtt"}:
                assert "00:00:00.100" in content or "00:00:00,100" in content
                assert "Untimed text." not in content
        segment_id = session.scalar(
            select(TranscriptSegment.id).where(TranscriptSegment.job_id == job_id)
        )
    with pytest.raises(ClassScribeError, match="cannot be rerun locally"):
        pipeline.retry_segment(job_id, segment_id)
