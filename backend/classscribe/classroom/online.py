"""Provider routing and durable MAI send intent outside database transactions."""
# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.activity import report_activity
from classscribe.audio.limits import validate_transcription_duration
from classscribe.audio.media import FFmpegMediaPipeline, file_sha256
from classscribe.audio.online_upload import prepare_mai_upload
from classscribe.classroom.mai import (
    API_VERSION,
    MODEL,
    MaiClient,
    MaiCredentials,
    MaiHttpError,
    mai_error,
    parse_mai_response,
    request_definition,
)
from classscribe.classroom.online_attempts import latest_attempt, response_file
from classscribe.config import AppConfig
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    ASRCandidate,
    GlossaryTerm,
    Job,
    JobCheckpoint,
    JobStatus,
    OnlineRequestAttempt,
    Recording,
    TimingQuality,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.jobs.state_machine import JobStateMachine
from classscribe.paths import AppPaths
from classscribe.recovery import atomic_write_bytes, atomic_write_text


class ProviderStageRunner:
    """Keep existing local execution intact while routing explicit online jobs."""

    def __init__(self, local: Any, paths: AppPaths, config: AppConfig) -> None:
        self.local = local
        self.paths = paths
        self.config = config
        self.credentials = MaiCredentials(paths.config / "azure-mai.env")
        self.client = MaiClient()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.local, name)

    @property
    def boundary(self) -> Any:
        return self.local.boundary

    @boundary.setter
    def boundary(self, value: Any) -> None:
        self.local.boundary = value

    def preflight(self, parameters: Mapping[str, Any], session: Session | None = None) -> None:
        if parameters.get("provider", "local") == "local":
            self.local.preflight(parameters, session)
            return
        self.credentials.get()
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise mai_error("MAI 音频处理需要 ffmpeg 和 ffprobe。")
        if session is not None:
            request_definition(parameters, self._terms(session, parameters))

    @staticmethod
    def _terms(session: Session, options: Mapping[str, Any]) -> list[str]:
        glossary = options.get("glossary_id")
        return (
            list(
                session.scalars(
                    select(GlossaryTerm.canonical).where(GlossaryTerm.glossary_id == glossary)
                )
            )
            if glossary
            else []
        )

    def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        if job.options_json.get("provider", "local") != "azure_mai":
            self.local.run(session, job, checkpoint)
            return
        if checkpoint.checkpoint_key == "mai_import":
            self._import(session, job)
        else:
            self.local.run(session, job, checkpoint)

    def run_external(
        self, sessions: sessionmaker[Session], job_id: str, checkpoint_id: str
    ) -> None:
        state = JobStateMachine()
        with sessions.begin() as session:
            job = session.get_one(Job, job_id)
            checkpoint = session.get_one(JobCheckpoint, checkpoint_id)
            # Each dispatch gets one send; additional dispatches require an explicit retry.
            checkpoint.max_attempts = checkpoint.attempt_count + 1
            state.begin_checkpoint(job, checkpoint)
        try:
            self._request(sessions, job_id)
            with sessions.begin() as session:
                state.complete_checkpoint(
                    session.get_one(Job, job_id), session.get_one(JobCheckpoint, checkpoint_id)
                )
        except Exception as exc:
            with sessions.begin() as session:
                state.fail_checkpoint(
                    session.get_one(Job, job_id),
                    session.get_one(JobCheckpoint, checkpoint_id),
                    code=exc.code if isinstance(exc, ClassScribeError) else ErrorCode.WORKER_CRASH,
                    detail=exc.detail
                    if isinstance(exc, ClassScribeError)
                    else "MAI 请求失败；不会自动重发。",
                )
            raise

    def _request(self, sessions: sessionmaker[Session], job_id: str) -> None:
        with sessions() as session:
            current = latest_attempt(session, job_id)
            number = current.attempt_number if current is not None else 1
        response_path = response_file(self.paths, job_id, number)
        # Atomic response publication can precede the database commit during a crash.
        # Recover local evidence before consulting credentials or sending another request.
        with sessions.begin() as session:
            attempt = latest_attempt(session, job_id)
            if (
                attempt is not None
                and attempt.status in {"sending", "uncertain", "responded", "imported"}
                and response_path.is_file()
            ):
                if attempt.status != "imported":
                    attempt.status = "responded"
                attempt.response_path = str(response_path)
                return
        endpoint, key = self.credentials.get()
        with sessions() as session:
            job = session.get_one(Job, job_id)
            recording = session.get_one(Recording, job.recording_id)
            audio = self.paths.data_path("recordings", recording.id, "derived", "audio_master.wav")
            definition = request_definition(
                job.options_json, self._terms(session, job.options_json)
            )
        validate_transcription_duration(
            FFmpegMediaPipeline._inspect_master(audio).duration_samples, "azure_mai"
        )
        digest = file_sha256(audio)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"audio": digest, "endpoint": endpoint, "definition": definition}, sort_keys=True
            ).encode()
        ).hexdigest()

        def cancelled() -> bool:
            with sessions() as session:
                status = session.scalar(select(Job.status).where(Job.id == job_id))
                return status in {None, JobStatus.CANCELLING, JobStatus.CANCELLED}

        with sessions.begin() as session:
            attempt = latest_attempt(session, job_id)
            if attempt is not None:
                if attempt.request_fingerprint != fingerprint:
                    raise mai_error("MAI 请求参数或音频已变化；请创建新任务。")
                if attempt.status in {"responded", "imported"} and response_path.is_file():
                    return
                if attempt.status != "prepared":
                    raise mai_error(
                        "此前 MAI 请求结果未知或失败，不会自动重发。"
                        "请在任务页面确认可能再次计费后，手动重试。"
                    )
            else:
                attempt = OnlineRequestAttempt(
                    job_id=job_id,
                    request_fingerprint=fingerprint,
                    audio_sha256=digest,
                    status="prepared",
                )
                session.add(attempt)
        upload_path = response_path.with_name("upload.flac")
        report_activity("mai_prepare", force=True)
        prepare_mai_upload(audio, upload_path, cancelled)
        if file_sha256(audio) != digest:
            raise mai_error("音频在准备期间发生变化，未发送请求。")
        upload_digest = file_sha256(upload_path)
        # Persist send intent before any socket can upload data.
        with sessions.begin() as session:
            attempt = latest_attempt(session, job_id)
            assert attempt is not None
            attempt.status = "sending"
            attempt.audio_sha256 = upload_digest
        atomic_write_text(
            response_path.with_name("request.json"),
            json.dumps(
                {
                    "definition": definition,
                    "audio_sha256": upload_digest,
                    "canonical_audio_sha256": digest,
                    "request_fingerprint": fingerprint,
                },
                ensure_ascii=False,
            ),
        )

        try:
            payload, request_id = self.client.transcribe(
                endpoint,
                key,
                upload_path,
                definition,
                cancelled=cancelled,
                progress=lambda stage, count, total: report_activity(
                    stage,
                    completed=count,
                    total=total or None,
                    unit="bytes" if total else None,
                    force=True,
                ),
            )
            atomic_write_bytes(response_path, payload)
            with sessions.begin() as session:
                attempt = latest_attempt(session, job_id)
                assert attempt is not None
                attempt.status = "responded"
                attempt.response_path = str(response_path)
                attempt.service_request_id = request_id
        except Exception as exc:
            if isinstance(exc, MaiHttpError):
                diagnostics = {**exc.diagnostics, "captured_at": datetime.now(UTC).isoformat()}
                try:
                    atomic_write_text(
                        response_path.with_name("error.json"),
                        json.dumps(diagnostics, ensure_ascii=False),
                    )
                except OSError as write_error:
                    exc.detail += f" 诊断文件保存失败: {type(write_error).__name__}。"
            with sessions.begin() as session:
                attempt = latest_attempt(session, job_id)
                assert attempt is not None
                if isinstance(exc, MaiHttpError):
                    attempt.diagnostics_json = diagnostics
                    attempt.status = "failed"
                    attempt.error_code = f"MAI_HTTP_{exc.status}"
                    attempt.service_request_id = exc.request_id or None
                else:
                    attempt.status = "uncertain"
                    attempt.error_code = "MAI_REQUEST_UNCERTAIN"
            raise

    def _import(self, session: Session, job: Job) -> None:
        attempt = latest_attempt(session, job.id)
        if (
            attempt is None
            or attempt.status not in {"responded", "imported"}
            or not attempt.response_path
        ):
            raise mai_error("MAI 原始响应尚未保存。")
        if attempt.status == "imported":
            return
        recording = session.get_one(Recording, job.recording_id)
        phrases = parse_mai_response(
            Path(attempt.response_path).read_bytes(),
            recording.duration_samples,
            job.language_mode.value,
        )
        for phrase in phrases:
            segment = TranscriptSegment(
                job_id=job.id,
                start_sample=phrase.start,
                end_sample=phrase.end,
                speaker_id=phrase.speaker,
                language=LanguageMode(phrase.language),
                raw_text=phrase.text,
                faithful_text=phrase.text,
                smart_corrected_text=phrase.text,
                auto_final_source="azure_mai",
                timing_quality=TimingQuality.NATIVE if phrase.timed else TimingQuality.INVALID,
            )
            session.add(segment)
            session.flush()
            session.add(
                ASRCandidate(
                    segment_id=segment.id,
                    model_id=MODEL,
                    model_revision="service-unreported",
                    raw_text=phrase.text,
                    normalized_text=phrase.text,
                    is_adopted=True,
                    decode_config_json={
                        "provider": "azure_mai",
                        "api_version": API_VERSION,
                        "requested_model": MODEL,
                    },
                    inference_metrics_json={"service_request_id": attempt.service_request_id},
                    warnings_json=[] if phrase.timed else [{"code": "MISSING_TIMING"}],
                )
            )
            for token, start, end in phrase.words:
                session.add(
                    TokenSpan(
                        segment_id=segment.id,
                        token=token,
                        normalized_token=token,
                        start_sample=start,
                        end_sample=end,
                        provenance_json={
                            "provider": "azure_mai",
                            "request_id": attempt.service_request_id,
                        },
                    )
                )
        attempt.status = "imported"
