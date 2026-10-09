from __future__ import annotations

import asyncio
import json
import threading
import wave
from datetime import UTC, datetime, timedelta
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
    Glossary,
    GlossaryTerm,
    Job,
    JobCheckpoint,
    JobStatus,
    OnlineRequestAttempt,
    Recording,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError, ErrorCode
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
                if path.is_file():
                    return  # Production reuses the immutable master for this recording.
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


def test_course_glossary_and_import_options_reach_transport(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    options = {
        "transcribe_style": "clean",
        "timestamps": "none",
        "diarization": False,
        "locale": "en",
        "profanity_filter_mode": "Removed",
        "phrases": ["GPU", "manual term"],
        "phrase_biasing_weight": 1.5,
    }
    with sessions.begin() as session:
        glossary = Glossary(name="course vocabulary")
        glossary.terms = [
            GlossaryTerm(
                canonical="GPU",
                reading="reading-marker",
                aliases=["alias-marker"],
                language=LanguageMode.ENGLISH,
                weight=0.8,
                source="manual",
                user_confirmed=True,
            )
        ]
        session.add(glossary)
        session.flush()
        job = session.get_one(Job, identifier)
        job.options_json = {
            **job.options_json,
            "glossary_id": glossary.id,
            "mai_options": options,
        }
        runner.preflight(job.options_json, session)
    captured: list[dict[str, Any]] = []

    def transcribe(
        endpoint: str, key: str, audio: Path, definition: dict[str, Any], *args: Any, **kwargs: Any
    ) -> tuple[bytes, str]:
        captured.append(definition)
        return b'{"phrases":[{"text":"Clean transcript","locale":"en"}]}', "request-id"

    monkeypatch.setattr(runner.client, "transcribe", transcribe)
    pipeline.run_until_blocked(identifier)
    assert len(captured) == 1
    definition = captured[0]
    assert definition["phraseList"] == {
        "phrases": ["GPU", "manual term"],
        "biasingWeight": 1.5,
    }
    assert "reading-marker" not in json.dumps(definition)
    assert "alias-marker" not in json.dumps(definition)
    assert definition["enhancedMode"]["modelOptions"] == {
        "transcribeStyle": "clean",
        "timestamps": "none",
    }
    assert definition["diarization"] == {"enabled": False}
    assert definition["profanityFilterMode"] == "Removed"
    assert definition["locales"] == ["en"]
    with sessions() as session:
        job = session.get_one(Job, identifier)
        assert job.status == JobStatus.COMPLETED, job.error_detail
        assert job.options_json["mai_options"] == options
        segment = session.scalar(select(TranscriptSegment))
        assert segment.raw_text == "Clean transcript"
        assert segment.speaker_id is None
        assert segment.start_sample == segment.end_sample == 0


def test_mai_queue_overlaps_requests_caps_parallelism_and_resumes_after_pause(
    database: Any, tmp_path: Path
) -> None:
    from classscribe.classroom import queue
    from classscribe.classroom.mai import MaiHttpError

    sessions, runner, pipeline, first = setup_online(database, tmp_path)
    released = threading.Event()
    entered = threading.Event()
    capacity = threading.Event()
    guard = threading.Lock()
    calls: list[str] = []
    active, peak = 0, 0

    def transcribe(endpoint: str, key: str, audio: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal active, peak
        identifier = audio.parent.parent.name
        with guard:
            calls.append(identifier)
            active += 1
            peak = max(peak, active)
            entered.set()
            if active == 10:
                capacity.set()
        try:
            assert released.wait(10)
            if identifier == first:
                raise MaiHttpError(503, "service-failure-id", "30")
            return b'{"phrases":[{"text":"parallel result"}]}', "request-id"
        finally:
            with guard:
                active -= 1

    runner.client.transcribe = transcribe  # type: ignore[method-assign]

    async def scenario() -> None:
        pipeline.schedule(first)
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            # A new submission must wake the dispatcher while its first request is waiting.
            with sessions.begin() as session:
                original = session.get_one(Job, first)
                others = [
                    Job(
                        recording_id=original.recording_id,
                        language_mode=original.language_mode,
                        profile_id=original.profile_id,
                        options_json=dict(original.options_json),
                        queue_order=order,
                    )
                    for order in range(1, 12)
                ]
                session.add_all(others)
                session.flush()
                identifiers = [first, *(job.id for job in others)]
            pipeline.schedule(identifiers[-1])
            assert await asyncio.to_thread(capacity.wait, 5)
            assert calls[0] == first
            assert set(calls) == set(identifiers[:10])
            assert all(
                pipeline.snapshot(identifier)["status"] == "pending"
                for identifier in identifiers[10:]
            )
            with sessions.begin() as session:
                queue.set_paused(session, True)
            released.set()
            await asyncio.wait_for(asyncio.gather(*tuple(pipeline._tasks)), 5)
            assert len(calls) == 10
            with sessions() as session:
                attempt = session.scalar(
                    select(OnlineRequestAttempt).where(OnlineRequestAttempt.job_id == first)
                )
                assert attempt.status == "failed"
                assert attempt.error_code == "MAI_HTTP_503"
                assert attempt.service_request_id == "service-failure-id"
                assert "30" in session.get_one(Job, first).error_detail
            with sessions.begin() as session:
                queue.set_paused(session, False)
            pipeline.schedule(identifiers[-1])
            await asyncio.wait_for(asyncio.gather(*tuple(pipeline._tasks)), 5)
            assert len(calls) == len(set(calls)) == 12
            assert peak == 10
            assert pipeline.snapshot(first)["status"] == "failed"
            assert all(
                pipeline.snapshot(identifier)["status"] == "completed"
                for identifier in identifiers[1:]
            )
            with pytest.raises(ClassScribeError, match="不会自动重发"):
                runner._request(sessions, first)
            assert len(calls) == 12
        finally:
            released.set()
            await pipeline.close()

    asyncio.run(scenario())


def _retry_service(
    runner: ProviderStageRunner, pipeline: ClassroomPipeline, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, list[str]]:
    from classscribe.api.service import ClassScribeService

    scheduled: list[str] = []
    monkeypatch.setattr(pipeline, "schedule", scheduled.append)
    service = ClassScribeService(
        pipeline.sessions,
        runner.paths,
        load_registry(Path("config/model-registry.v1.yaml")),
        pipeline=pipeline,
    )
    return service, scheduled


def test_manual_mai_retry_preserves_steps_and_request_history(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.classroom.mai import MaiHttpError
    from classscribe.classroom.online_attempts import latest_attempt

    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    calls: list[int] = []

    def transcribe(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        calls.append(1)
        if len(calls) == 1:
            raise MaiHttpError(
                503, "first-request", "0", body='{"error":{"code":"diarization_unavailable"}}'
            )
        return b'{"phrases":[{"text":"retried successfully"}]}', "second-request"

    monkeypatch.setattr(runner.client, "transcribe", transcribe)
    service, scheduled = _retry_service(runner, pipeline, monkeypatch)
    pipeline.run_until_blocked(identifier)
    failed = service.job(identifier)
    assert failed["error_code"] == ErrorCode.MAI_HTTP_ERROR.value
    assert len(calls) == 1
    original_id = failed["online_retry"]["attempt_id"]
    with sessions() as session:
        completed = {
            cp.id: (cp.attempt_count, cp.completed_at)
            for cp in session.get_one(Job, identifier).checkpoints
            if cp.status.value == "completed"
        }
    with pytest.raises(ClassScribeError, match="确认"):
        service.retry_job(identifier)
    assert not scheduled
    pending = service.retry_job(identifier, confirm_resend=True, expected_attempt_id=original_id)
    assert pending["status"] == "pending"
    assert [a["status"] for a in pending["online_attempts"]] == ["failed", "prepared"]
    # Duplicate clicks while queued grant no additional request.
    service.retry_job(identifier, confirm_resend=True, expected_attempt_id=original_id)
    pipeline.run_until_blocked(identifier)
    result = service.job(identifier)
    assert result["status"] == "completed"
    assert "online_error" not in result
    assert len(calls) == 2
    assert [a["request_id"] for a in result["online_attempts"]] == [
        "first-request",
        "second-request",
    ]
    assert (
        result["online_attempts"][0]["diagnostics"]["service_error_code"]
        == "diarization_unavailable"
    )
    with sessions() as session:
        job = session.get_one(Job, identifier)
        assert {
            cp.id: (cp.attempt_count, cp.completed_at)
            for cp in job.checkpoints
            if cp.id in completed
        } == completed
        checkpoint = next(cp for cp in job.checkpoints if cp.checkpoint_key == "mai_request")
        assert checkpoint.attempt_count == checkpoint.max_attempts == 2
        attempt = latest_attempt(session, identifier)
        assert attempt is not None and attempt.response_path is not None
        assert Path(attempt.response_path).is_file()
        assert "/attempts/2/" in attempt.response_path
        assert len(list(session.scalars(select(TranscriptSegment)))) == 1
    runner._request(sessions, identifier)
    assert len(calls) == 2


def test_uncertain_manual_retry_requires_confirmation_for_current_attempt(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    calls: list[int] = []

    def transcribe(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise OSError("connection lost")

    monkeypatch.setattr(runner.client, "transcribe", transcribe)
    service, _ = _retry_service(runner, pipeline, monkeypatch)
    pipeline.run_until_blocked(identifier)
    first = service.job(identifier)["online_retry"]
    assert first["result_uncertain"]
    with pytest.raises(ClassScribeError, match="确认"):
        service.retry_job(identifier, expected_attempt_id=first["attempt_id"])
    with pytest.raises(ClassScribeError, match="刷新"):
        service.retry_job(identifier, confirm_resend=True, expected_attempt_id=str(uuid4()))
    assert len(calls) == 1
    service.retry_job(identifier, confirm_resend=True, expected_attempt_id=first["attempt_id"])
    pipeline.run_until_blocked(identifier)
    assert len(calls) == 2
    # An old confirmation cannot authorize a third request after a second failure.
    with pytest.raises(ClassScribeError, match="刷新"):
        service.retry_job(identifier, confirm_resend=True, expected_attempt_id=first["attempt_id"])
    with sessions() as session:
        assert [
            a.status
            for a in session.scalars(
                select(OnlineRequestAttempt).order_by(OnlineRequestAttempt.attempt_number)
            )
        ] == ["uncertain", "uncertain"]


def test_authorized_retry_survives_restart_and_is_sent_once(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.classroom.mai import MaiHttpError
    from classscribe.classroom.online_attempts import latest_attempt

    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    calls: list[int] = []

    def first_send(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise MaiHttpError(503, "failed-before-restart", "0")

    monkeypatch.setattr(runner.client, "transcribe", first_send)
    service, _ = _retry_service(runner, pipeline, monkeypatch)
    pipeline.run_until_blocked(identifier)
    policy = service.job(identifier)["online_retry"]
    service.retry_job(identifier, confirm_resend=True, expected_attempt_id=policy["attempt_id"])
    restarted = ProviderStageRunner(runner.local, runner.paths, runner.config)
    recovered_pipeline = ClassroomPipeline(sessions, restarted)

    def second_send(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        calls.append(2)
        return b'{"phrases":[{"text":"recovered retry"}]}', "after-restart"

    monkeypatch.setattr(restarted.client, "transcribe", second_send)
    assert recovered_pipeline.recover() == (identifier,)
    recovered_pipeline.run_until_blocked(identifier)
    assert recovered_pipeline.snapshot(identifier)["status"] == "completed"
    restarted._request(sessions, identifier)
    assert calls == [1, 2]
    with sessions() as session:
        latest = latest_attempt(session, identifier)
        assert latest is not None and latest.attempt_number == 2 and latest.status == "imported"


@pytest.mark.parametrize("retry_after, expected_wait", [("30", 30), (None, 5)])
def test_manual_retry_obeys_upstream_cooldown(
    database: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
    expected_wait: int,
) -> None:
    from classscribe.classroom.mai import MaiHttpError

    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)

    def transcribe(*args: Any, **kwargs: Any) -> Any:
        raise MaiHttpError(503, "retry-later", retry_after)

    monkeypatch.setattr(runner.client, "transcribe", transcribe)
    service, scheduled = _retry_service(runner, pipeline, monkeypatch)
    pipeline.run_until_blocked(identifier)
    policy = service.job(identifier)["online_retry"]
    assert 0 < policy["retry_after_seconds"] <= expected_wait
    with pytest.raises(ClassScribeError, match="等待"):
        service.retry_job(identifier, confirm_resend=True, expected_attempt_id=policy["attempt_id"])
    assert not scheduled
    with sessions.begin() as session:
        attempt = session.get_one(OnlineRequestAttempt, policy["attempt_id"])
        attempt.updated_at = datetime.now(UTC) - timedelta(seconds=expected_wait + 1)
    assert (
        service.retry_job(
            identifier, confirm_resend=True, expected_attempt_id=policy["attempt_id"]
        )["status"]
        == "pending"
    )


def test_manual_retry_recovers_published_response_without_resending(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.recovery import atomic_write_bytes

    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)

    def transcribe(*args: Any, **kwargs: Any) -> Any:
        raise OSError("lost connection")

    monkeypatch.setattr(runner.client, "transcribe", transcribe)
    service, _ = _retry_service(runner, pipeline, monkeypatch)
    pipeline.run_until_blocked(identifier)
    response = runner.paths.data_path("jobs", identifier, "online", "response.raw.json")
    atomic_write_bytes(response, b'{"phrases":[{"text":"already saved"}]}')
    runner.credentials.clear()
    policy = service.job(identifier)["online_retry"]
    assert policy["recovers_saved_response"] and not policy["requires_confirmation"]
    service.retry_job(identifier)
    pipeline.run_until_blocked(identifier)
    assert service.job(identifier)["status"] == "completed"
    with sessions() as session:
        assert len(list(session.scalars(select(OnlineRequestAttempt)))) == 1
        assert session.scalar(select(TranscriptSegment)).faithful_text == "already saved"


def test_retry_route_validates_explicit_confirmation(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.api.routes import create_api_router
    from classscribe.classroom.mai import MaiHttpError
    from fastapi import FastAPI

    _, runner, pipeline, identifier = setup_online(database, tmp_path)

    def transcribe(*args: Any, **kwargs: Any) -> Any:
        raise MaiHttpError(503, "retry-route", "0")

    monkeypatch.setattr(runner.client, "transcribe", transcribe)
    service, _ = _retry_service(runner, pipeline, monkeypatch)
    pipeline.run_until_blocked(identifier)
    attempt_id = service.job(identifier)["online_retry"]["attempt_id"]
    app = FastAPI()
    app.include_router(create_api_router(service))

    async def post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        messages: list[Any] = []

        async def receive() -> Any:
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

        async def send(message: Any) -> None:
            messages.append(message)

        await app(
            {
                "type": "http",
                "method": "POST",
                "scheme": "http",
                "path": f"/api/v1/jobs/{identifier}/retry",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "server": ("127.0.0.1", 8765),
                "client": ("127.0.0.1", 50000),
            },
            receive,
            send,
        )
        return messages[0]["status"], json.loads(messages[1]["body"])

    assert asyncio.run(post({"confirm_resend": "true"}))[0] == 422
    status, payload = asyncio.run(post({"confirm_resend": True, "expected_attempt_id": attempt_id}))
    assert status == 200 and payload["status"] == "pending"


def test_failed_response_diagnostics_are_durable_and_available_after_restart(
    database: Any, tmp_path: Path
) -> None:
    from classscribe.api.service import ClassScribeService
    from classscribe.classroom.mai import MaiHttpError

    sessions, runner, pipeline, identifier = setup_online(database, tmp_path)
    calls: list[int] = []

    def transcribe(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise MaiHttpError(
            503,
            "trace-id",
            "45",
            body=json.dumps(
                {
                    "error": {
                        "code": "diarization_unavailable",
                        "message": "Diarization service returned error code 400 secret-marker",
                    }
                }
            ),
            secrets=("secret-marker",),
        )

    runner.client.transcribe = transcribe  # type: ignore[method-assign]
    pipeline.run_until_blocked(identifier)
    path = runner.paths.data_path("jobs", identifier, "online", "error.json")
    diagnostics = json.loads(path.read_text())
    assert path.stat().st_mode & 0o777 == 0o600
    assert diagnostics["service_error_code"] == "diarization_unavailable"
    assert diagnostics["http_status"] == 503
    assert "error code 400" in diagnostics["service_error_message"]
    assert "secret-marker" not in path.read_text()
    service = ClassScribeService(
        sessions, runner.paths, load_registry(Path("config/model-registry.v1.yaml"))
    )
    payload = service.job(identifier)
    assert payload["online_error"] == diagnostics
    assert "diarization_unavailable" in payload["error_detail"]
    assert "secret-marker" not in json.dumps(payload)
    with pytest.raises(ClassScribeError, match="不会自动重发"):
        runner._request(sessions, identifier)
    assert len(calls) == 1


def test_mai_can_clip_and_normalize_more_than_ninety_minutes(database: Any, tmp_path: Path) -> None:
    from classscribe.db.models import ExportArtifact

    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path)
    paths.ensure()
    recording_id = str(uuid4())
    source = paths.data_path("recordings", recording_id, "source", "long.wav")
    source.parent.mkdir(parents=True)
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 1, 1000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x80" * (100 * 60 * 1000))
    with sessions.begin() as session:
        session.add(
            Recording(
                id=recording_id,
                source_name="long.wav",
                source_path=str(source),
                source_sha256=file_sha256(source),
                duration_samples=100 * 60 * 16000,
                sample_rate=1000,
                channels=1,
            )
        )
    clip_id = create_recording_clip(
        sessions, paths, recording_id, str(uuid4()), 0, 91 * 60 * 16000, provider="azure_mai"
    )
    config = load_config(environment={})
    local = ProductionStageRunner(
        paths,
        config,
        load_registry(Path("config/model-registry.v1.yaml")),
        ModelManager(paths.cache / "models"),
        cast(SandboxedModelInvoker, object()),
    )
    runner = ProviderStageRunner(local, paths, config)
    runner.credentials.configure("https://eastus.api.cognitive.microsoft.com", "key")
    options = {"provider": "azure_mai", "language": "en", "outputs": ["json"]}
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
        identifier = job.id

    def transcribe(*args: Any, **kwargs: Any) -> Any:
        audio = args[2]
        assert audio.stat().st_size <= 240_000_000
        return b'{"phrases":[{"text":"Long online transcript."}]}', "request-id"

    runner.client.transcribe = transcribe  # type: ignore[method-assign]
    pipeline.initialize(identifier, options)
    pipeline.run_until_blocked(identifier)
    with sessions() as session:
        job = session.get_one(Job, identifier)
        assert job.status == JobStatus.COMPLETED, job.error_detail
        assert session.get_one(Recording, clip_id).duration_samples == 91 * 60 * 16000
        assert session.scalar(select(ExportArtifact).where(ExportArtifact.job_id == identifier))


def test_mai_queue_close_waits_for_requests_and_preserves_response(
    database: Any, tmp_path: Path
) -> None:
    sessions, runner, pipeline, first = setup_online(database, tmp_path)
    entered, released = threading.Event(), threading.Event()

    def transcribe(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert released.wait(10)
        return b'{"phrases":[{"text":"saved during shutdown"}]}', "request-id"

    runner.client.transcribe = transcribe  # type: ignore[method-assign]

    async def scenario() -> None:
        pipeline.schedule(first)
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            closing = asyncio.create_task(pipeline.close())
            await asyncio.sleep(0.05)
            assert not closing.done()
            released.set()
            await asyncio.wait_for(closing, 5)
            assert pipeline.snapshot(first)["status"] == "pending"
            with sessions() as session:
                attempt = session.scalar(select(OnlineRequestAttempt))
                assert attempt.status == "responded"
                assert Path(attempt.response_path).is_file()
        finally:
            released.set()
            await pipeline.close()

    asyncio.run(scenario())
