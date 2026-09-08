"""Persistent application service behind the versioned local HTTP API."""

from __future__ import annotations

import hashlib
import wave
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.api.schemas import (
    ApplyRanking,
    BenchmarkCreate,
    CandidateAdoption,
    ExportCreate,
    GlossaryCreate,
    GlossaryTermsUpdate,
    JobCreate,
    ProfileUpdate,
    SegmentMerge,
    SegmentPatch,
    SegmentSplit,
)
from classscribe.classroom import ClassroomPipeline
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    AppSetting,
    ASRCandidate,
    BenchmarkItem,
    BenchmarkRun,
    BenchmarkStatus,
    DecisionEvent,
    ExportArtifact,
    Glossary,
    GlossaryMaterial,
    GlossaryTerm,
    Job,
    JobStatus,
    ModelHealth,
    ModelInstallation,
    ProfileSetting,
    Recording,
    SpeakerDisplayName,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.exports import ExportSegment, ExportToken, render_export
from classscribe.jobs.audit import ActorType, AuditService, EditableLayer
from classscribe.models import (
    DictationWorkerSupervisor,
    HealthCheckOutcome,
    LoadedManifestBundle,
    ManifestComponentSource,
    ModelEntry,
    ModelLicense,
    ModelManager,
    ModelManifest,
    ModelRegistry,
)
from classscribe.models.manager import ModelDownloader
from classscribe.models.support import model_installability, worker_implemented
from classscribe.paths import AppPaths
from classscribe.punctuation.guard import strip_punctuation_and_spacing
from classscribe.recovery import atomic_write_bytes, atomic_write_text
from classscribe.resources import resource_root
from classscribe.security import UploadLimits, parse_uuid
from classscribe.terminology import (
    ConfirmationStatus,
    CourseTerm,
    TerminologyRepository,
    TermSource,
    import_material,
)
from classscribe.timeline import AudioSpan

_CONTENT_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}
_SETTINGS_KEYS = frozenset(
    {
        "appearance",
        "locale",
        "auto_export",
        "retention",
        "diagnostics",
        "ibus",
        "subtitle",
    }
)
ModelHealthCheck = Callable[
    [ModelEntry, Path, Path, Mapping[str, str], str, str | None], HealthCheckOutcome
]


class ClassScribeService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        paths: AppPaths,
        registry: ModelRegistry,
        *,
        pipeline: ClassroomPipeline | None = None,
        model_manager: ModelManager | None = None,
        model_downloader: ModelDownloader | None = None,
        model_health_check: ModelHealthCheck | None = None,
        model_licenses: Mapping[str, ModelLicense] | None = None,
        manifest_bundle: LoadedManifestBundle | None = None,
        resident_workers: DictationWorkerSupervisor | None = None,
        allow_pending_jobs_without_pipeline: bool = True,
        upload_limits: UploadLimits | None = None,
    ) -> None:
        self.sessions = sessions
        self.paths = paths
        self.registry = registry
        self.pipeline = pipeline
        self.model_manager = model_manager
        self.model_downloader = model_downloader
        self.model_health_check = model_health_check
        self.model_licenses = dict(model_licenses or {})
        self.manifest_bundle = manifest_bundle
        self.resident_workers = resident_workers
        self.allow_pending_jobs_without_pipeline = allow_pending_jobs_without_pipeline
        self.upload_limits = upload_limits or UploadLimits()
        self.audit = AuditService()

    def create_recording(
        self,
        *,
        source_name: str,
        content: bytes,
        duration_samples: int,
        channels: int,
        sample_rate: int,
    ) -> dict[str, Any]:
        if Path(source_name).name != source_name or not source_name:
            raise ClassScribeError(ErrorCode.INVALID_FILE_ID, "source name must not contain a path")
        self.upload_limits.validate(
            source_name=source_name,
            size_bytes=len(content),
            duration_samples=duration_samples,
            channels=channels,
            sample_rate=sample_rate,
        )
        recording_id = str(uuid4())
        directory = self.paths.data_path("recordings", recording_id, "source")
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        target = directory / f"{uuid4()}{Path(source_name).suffix.lower()}"
        atomic_write_bytes(target, content, mode=0o400)
        digest = hashlib.sha256(content).hexdigest()
        with self.sessions.begin() as session:
            recording = Recording(
                id=recording_id,
                source_name=source_name,
                source_sha256=digest,
                source_path=str(target),
                duration_samples=duration_samples,
                sample_rate=sample_rate,
                channels=channels,
                audio_qc_json={
                    "upload_validated": True,
                    "source_bytes": len(content),
                    **_health_wav_qc(
                        content,
                        duration_samples=duration_samples,
                        channels=channels,
                        sample_rate=sample_rate,
                    ),
                },
            )
            session.add(recording)
        return self.recording(recording_id)

    def recording(self, recording_id: str) -> dict[str, Any]:
        identifier = _id(recording_id, "recording_id")
        with self.sessions() as session:
            item = session.get(Recording, identifier)
            if item is None:
                raise _not_found("recording")
            return _recording_payload(item)

    def recordings(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            items = session.scalars(
                select(Recording).order_by(Recording.created_at.desc(), Recording.id.desc())
            )
            return [_recording_payload(item) for item in items]

    def recording_file(self, recording_id: str) -> tuple[Path, str]:
        identifier = _id(recording_id, "recording_id")
        with self.sessions() as session:
            item = session.get(Recording, identifier)
            if item is None:
                raise _not_found("recording")
            path = Path(item.source_path)
            source_name = item.source_name
        root = self.paths.data_path("recordings", identifier).resolve(strict=False)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ClassScribeError(ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "recording file is invalid")
        return path, source_name

    def create_job(self, value: JobCreate) -> dict[str, Any]:
        if self.pipeline is None and not self.allow_pending_jobs_without_pipeline:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT,
                "the production classroom stage binding is unavailable; no job was created",
            )
        recording_id = _id(value.recording_id, "recording_id")
        glossary_id = _optional_id(value.glossary_id, "glossary_id")
        options = value.model_dump(mode="json")
        with self.sessions.begin() as session:
            if session.get(Recording, recording_id) is None:
                raise _not_found("recording")
            if glossary_id is not None and session.get(Glossary, glossary_id) is None:
                raise _not_found("glossary")
            if self.pipeline is not None:
                self.pipeline.preflight(options, session)
            job = Job(
                recording_id=recording_id,
                language_mode=value.language,
                profile_id=value.accuracy_mode,
                options_json=options,
            )
            session.add(job)
            session.flush()
            job_id = job.id
        if self.pipeline is not None:
            self.pipeline.initialize(job_id, options)
            self.pipeline.schedule(job_id)
        return self.job(job_id)

    def job(self, job_id: str) -> dict[str, Any]:
        identifier = _id(job_id, "job_id")
        if self.pipeline is not None:
            with self.sessions() as session:
                job = session.get(Job, identifier)
                if job is None:
                    raise _not_found("job")
                recording_id = job.recording_id
            return {
                **self.pipeline.snapshot(identifier),
                "recording_id": recording_id,
                "options": self._job_options(identifier),
            }
        with self.sessions() as session:
            job = session.get(Job, identifier)
            if job is None:
                raise _not_found("job")
            return _job_payload(job)

    def pause_job(self, job_id: str) -> dict[str, Any]:
        pipeline = self._pipeline()
        pipeline.pause(_id(job_id, "job_id"))
        return self.job(job_id)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        pipeline = self._pipeline()
        identifier = _id(job_id, "job_id")
        pipeline.resume(identifier)
        pipeline.schedule(identifier)
        return self.job(identifier)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        pipeline = self._pipeline()
        pipeline.cancel(_id(job_id, "job_id"))
        return self.job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        identifier = _id(job_id, "job_id")
        pipeline = self._pipeline()
        with self.sessions.begin() as session:
            job = session.get(Job, identifier)
            if job is None:
                raise _not_found("job")
            if job.status is JobStatus.FAILED:
                pipeline.state.retry(job)
            elif job.status is not JobStatus.PENDING:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "only failed/pending jobs can be retried"
                )
        pipeline.schedule(identifier)
        return self.job(identifier)

    def retry_segment(self, job_id: str, segment_id: str) -> dict[str, Any]:
        identifier = _id(job_id, "job_id")
        segment = _id(segment_id, "segment_id")
        pipeline = self._pipeline()
        count = pipeline.retry_segment(identifier, segment)
        pipeline.schedule(identifier)
        return {"job_id": identifier, "segment_id": segment, "checkpoints_queued": count}

    def transcript(self, job_id: str, *, low_confidence_only: bool = False) -> dict[str, Any]:
        identifier = _id(job_id, "job_id")
        with self.sessions() as session:
            if session.get(Job, identifier) is None:
                raise _not_found("job")
            query = (
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.job_id == identifier,
                    TranscriptSegment.is_active.is_(True),
                )
                .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
            )
            segments = tuple(session.scalars(query).all())
            if low_confidence_only:
                segments = tuple(
                    item
                    for item in segments
                    if item.quality_score is None or item.quality_score < 0.82
                )
            return {
                "job_id": identifier,
                "timeline": "absolute_samples_16000_hz",
                "segments": [self._segment_payload(session, item) for item in segments],
            }

    def segment(self, segment_id: str) -> dict[str, Any]:
        identifier = _id(segment_id, "segment_id")
        with self.sessions() as session:
            segment = session.get(TranscriptSegment, identifier)
            if segment is None:
                raise _not_found("segment")
            payload = self._segment_payload(session, segment)
            payload["audit"] = [
                _decision_payload(event)
                for event in session.scalars(
                    select(DecisionEvent)
                    .where(DecisionEvent.segment_id == identifier)
                    .order_by(DecisionEvent.created_at, DecisionEvent.id)
                )
            ]
            return payload

    def patch_segment(self, segment_id: str, value: SegmentPatch) -> dict[str, Any]:
        identifier = _id(segment_id, "segment_id")
        with self.sessions.begin() as session:
            segment = session.get(TranscriptSegment, identifier)
            if segment is None or not segment.is_active:
                raise _not_found("active segment")
            if value.text is not None:
                layer = {
                    "faithful": EditableLayer.FAITHFUL,
                    "smart": EditableLayer.CORRECTED,
                    "user": EditableLayer.USER,
                }[value.layer]
                self.audit.edit_segment(
                    session,
                    segment_id=identifier,
                    expected_version=value.version,
                    layer=layer,
                    text=value.text,
                    actor_type=ActorType.HUMAN,
                    actor_id="local-user",
                    rule_version="web-autosave-v1",
                )
            elif segment.version != value.version:
                raise ClassScribeError(ErrorCode.SEGMENT_VERSION_CONFLICT, "stale segment version")
            if value.speaker_name is not None:
                if not segment.speaker_id or not value.speaker_name.strip():
                    raise ClassScribeError(
                        ErrorCode.JOB_STATE_CONFLICT,
                        "speaker rename requires a speaker ID and non-empty name",
                    )
                mapping = session.scalar(
                    select(SpeakerDisplayName).where(
                        SpeakerDisplayName.job_id == segment.job_id,
                        SpeakerDisplayName.speaker_global_id == segment.speaker_id,
                    )
                )
                if mapping is None:
                    mapping = SpeakerDisplayName(
                        job_id=segment.job_id,
                        speaker_global_id=segment.speaker_id,
                        display_name=value.speaker_name.strip(),
                    )
                    session.add(mapping)
                else:
                    mapping.display_name = value.speaker_name.strip()
                session.add(
                    DecisionEvent(
                        segment_id=identifier,
                        event_type="speaker_display_name_changed",
                        actor_type="human",
                        actor_id="local-user",
                        input_json={"speaker_id": segment.speaker_id},
                        output_json={"display_name": value.speaker_name.strip()},
                        rule_version="job-local-speaker-name-v1",
                    )
                )
        return self.segment(identifier)

    def undo_segment(self, segment_id: str, version: int) -> dict[str, Any]:
        return self._history_action(_id(segment_id, "segment_id"), version, redo=False)

    def redo_segment(self, segment_id: str, version: int) -> dict[str, Any]:
        return self._history_action(_id(segment_id, "segment_id"), version, redo=True)

    def candidates(self, segment_id: str) -> list[dict[str, Any]]:
        identifier = _id(segment_id, "segment_id")
        with self.sessions() as session:
            if session.get(TranscriptSegment, identifier) is None:
                raise _not_found("segment")
            return [
                {
                    "id": candidate.id,
                    "model_id": candidate.model_id,
                    "model_revision": candidate.model_revision,
                    "raw_text": candidate.raw_text,
                    "normalized_text": candidate.normalized_text,
                    "confidence_raw": candidate.confidence_raw,
                    "confidence_calibrated": candidate.confidence_calibrated,
                    "quality": candidate.quality_features_json,
                    "warnings": candidate.warnings_json,
                    "valid": candidate.is_valid,
                    "adopted": candidate.is_adopted,
                }
                for candidate in session.scalars(
                    select(ASRCandidate)
                    .where(ASRCandidate.segment_id == identifier, ASRCandidate.deleted_at.is_(None))
                    .order_by(ASRCandidate.created_at, ASRCandidate.id)
                )
            ]

    def adopt_candidate(self, segment_id: str, value: CandidateAdoption) -> dict[str, Any]:
        identifier = _id(segment_id, "segment_id")
        candidate_id = _id(value.candidate_id, "candidate_id")
        with self.sessions.begin() as session:
            candidate = session.get(ASRCandidate, candidate_id)
            if candidate is None or candidate.segment_id != identifier:
                raise _not_found("candidate")
            self.audit.adopt_candidate(
                session,
                candidate_id=candidate_id,
                actor_type=ActorType.HUMAN,
                actor_id="local-user",
                rule_version="human-candidate-adoption-v1",
            )
            self.audit.edit_segment(
                session,
                segment_id=identifier,
                expected_version=value.version,
                layer=EditableLayer.USER,
                text=candidate.normalized_text,
                actor_type=ActorType.HUMAN,
                actor_id="local-user",
                rule_version="human-candidate-adoption-v1",
            )
        return self.segment(identifier)

    def split_segment(self, segment_id: str, value: SegmentSplit) -> dict[str, Any]:
        identifier = _id(segment_id, "segment_id")
        with self.sessions.begin() as session:
            original = session.get(TranscriptSegment, identifier)
            if original is None or not original.is_active:
                raise _not_found("active segment")
            if not original.start_sample < value.split_sample < original.end_sample:
                raise ClassScribeError(
                    ErrorCode.CANDIDATE_TIMELINE_INVALID, "split is out of range"
                )
            current = original.user_text or original.smart_corrected_text or original.faithful_text
            if strip_punctuation_and_spacing(value.left_text + value.right_text) != (
                strip_punctuation_and_spacing(current)
            ):
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "split text must preserve the current text"
                )
            left = self._derived_segment(
                original, AudioSpan(original.start_sample, value.split_sample), value.left_text
            )
            right = self._derived_segment(
                original, AudioSpan(value.split_sample, original.end_sample), value.right_text
            )
            original.is_active = False
            session.add_all((left, right))
            session.flush()
            session.add(
                DecisionEvent(
                    segment_id=identifier,
                    event_type="segment_split",
                    actor_type="human",
                    actor_id="local-user",
                    input_json={"segment_id": identifier, "split_sample": value.split_sample},
                    output_json={"segment_ids": [left.id, right.id], "text_preserved": True},
                    rule_version="timeline-edit-v1",
                )
            )
            job_id = original.job_id
            result = {"superseded_segment_id": identifier, "segment_ids": [left.id, right.id]}
        if self.pipeline is not None:
            self.pipeline.refresh_segments(job_id)
        return result

    def merge_segments(self, anchor_segment_id: str, value: SegmentMerge) -> dict[str, Any]:
        anchor = _id(anchor_segment_id, "segment_id")
        identifiers = tuple(_id(item, "segment_id") for item in value.segment_ids)
        if anchor not in identifiers or len(set(identifiers)) != len(identifiers):
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "merge IDs are invalid")
        with self.sessions.begin() as session:
            segments = tuple(
                session.scalars(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.id.in_(identifiers))
                    .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
                ).all()
            )
            if len(segments) != len(identifiers) or any(not item.is_active for item in segments):
                raise _not_found("active segments")
            if len({item.job_id for item in segments}) != 1:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "segments belong to different jobs"
                )
            if any(left.end_sample != right.start_sample for left, right in pairwise(segments)):
                raise ClassScribeError(
                    ErrorCode.CANDIDATE_TIMELINE_INVALID, "merge segments must be contiguous"
                )
            merged_text = " ".join(
                (item.user_text or item.smart_corrected_text or item.faithful_text).strip()
                for item in segments
            ).strip()
            merged = self._derived_segment(
                segments[0],
                AudioSpan(segments[0].start_sample, segments[-1].end_sample),
                merged_text,
                supersedes=identifiers,
            )
            for item in segments:
                item.is_active = False
            session.add(merged)
            session.flush()
            session.add(
                DecisionEvent(
                    segment_id=merged.id,
                    event_type="segments_merged",
                    actor_type="human",
                    actor_id="local-user",
                    input_json={"segment_ids": list(identifiers)},
                    output_json={"segment_id": merged.id},
                    rule_version="timeline-edit-v1",
                )
            )
            job_id = merged.job_id
            merged_id = merged.id
        if self.pipeline is not None:
            self.pipeline.refresh_segments(job_id)
        return {"segment_id": merged_id, "superseded_segment_ids": list(identifiers)}

    def models(self) -> list[dict[str, Any]]:
        immutable_resources = resource_root()
        with self.sessions() as session:
            installations = {
                (item.model_id, item.revision): item
                for item in session.scalars(select(ModelInstallation))
            }
            result: list[dict[str, Any]] = []
            for entry in self.registry.models:
                manifest_metadata = self._manifest_metadata(entry.id)
                manifest = manifest_metadata[0] if manifest_metadata is not None else None
                worker_is_implemented = worker_implemented(entry, immutable_resources)
                installable, install_block_reason = model_installability(
                    entry,
                    immutable_resources,
                    manifest_available=manifest_metadata is not None,
                )
                result.append(
                    {
                        "id": entry.id,
                        "name": entry.display_name,
                        "revision": entry.revision,
                        "repository": entry.repository,
                        "license": (
                            self.model_licenses[entry.repository].model_dump(mode="json")
                            if entry.repository in self.model_licenses
                            else None
                        ),
                        "languages": list(entry.languages),
                        "tasks": list(entry.tasks),
                        "enabled": entry.enabled,
                        "experimental": entry.experimental,
                        "manifest_available": manifest_metadata is not None,
                        "manifest_sha256": (
                            manifest_metadata[1] if manifest_metadata is not None else None
                        ),
                        "worker_implemented": worker_is_implemented,
                        "installable": installable,
                        "install_block_reason": install_block_reason,
                        "estimated_download_bytes": (
                            manifest.estimated_download_bytes if manifest is not None else None
                        ),
                        "installed_size_bytes": (
                            manifest.installed_size_bytes if manifest is not None else None
                        ),
                        "remote_code_file_count": (
                            sum(item.kind == "remote_code" for item in manifest.files)
                            if manifest is not None
                            else None
                        ),
                        "component_source_count": (
                            len(manifest.component_sources) if manifest is not None else None
                        ),
                        "component_sources": (
                            _component_source_payload(manifest.component_sources)
                            if manifest is not None
                            else None
                        ),
                        "estimated_vram_mb": entry.resources.estimated_vram_mb,
                        "installation": _installation_payload(
                            installations.get((entry.id, entry.revision))
                        ),
                        "benchmark": entry.benchmark.model_dump(mode="json"),
                    }
                )
            return result

    def _manifest_metadata(self, model_id: str) -> tuple[ModelManifest, str] | None:
        bundle = self.manifest_bundle
        if bundle is None:
            return None
        try:
            return bundle.manifest(model_id), bundle.manifest_sha256(model_id)
        except KeyError:
            return None

    def request_bundled_model_install(self, model_id: str) -> dict[str, Any]:
        entry = self._registry_model(model_id)
        manifest_metadata = self._manifest_metadata(model_id)
        installable, install_block_reason = model_installability(
            entry,
            resource_root(),
            manifest_available=manifest_metadata is not None,
        )
        if not installable:
            raise ClassScribeError(
                ErrorCode.MODEL_INSTALL_BLOCKED,
                f"model is not ordinarily installable: {install_block_reason}",
            )
        if manifest_metadata is None:  # pragma: no cover - guarded by installability
            raise AssertionError("installable model must have manifest metadata")
        model_manifest, manifest_sha256 = manifest_metadata
        result = self._request_model_install(entry, model_manifest)
        result["manifest_sha256"] = manifest_sha256
        return result

    def model_manifest(self, model_id: str) -> dict[str, Any]:
        _entry, model_manifest, manifest_sha256 = self._bundled_manifest(model_id)
        return {
            "model_id": model_manifest.model_id,
            "sha256": manifest_sha256,
            "manifest": model_manifest.as_dict(),
        }

    def _bundled_manifest(self, model_id: str) -> tuple[ModelEntry, ModelManifest, str]:
        entry = self._registry_model(model_id)
        bundle = self.manifest_bundle
        if bundle is None:
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED,
                "the built-in model manifest bundle is unavailable",
            )
        try:
            model_manifest = bundle.manifest(model_id)
            manifest_sha256 = bundle.manifest_sha256(model_id)
        except KeyError as error:
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED,
                f"the built-in manifest is unavailable for model: {model_id}",
            ) from error
        return entry, model_manifest, manifest_sha256

    def _request_model_install(
        self, entry: ModelEntry, model_manifest: ModelManifest
    ) -> dict[str, Any]:
        manager = self._model_manager()
        license_record = self.model_licenses.get(entry.repository)
        if (
            model_manifest.model_id != entry.id
            or model_manifest.repository != entry.repository
            or model_manifest.revision != entry.revision
            or model_manifest.worker != entry.worker
            or model_manifest.trust_remote_code != entry.trust_remote_code
        ):
            raise ClassScribeError(
                ErrorCode.MODEL_REVISION_NOT_PINNED,
                "manifest identity, worker, repository, or code policy differs from the registry",
            )
        if license_record is not None and (
            model_manifest.license_id != license_record.license_id
            or model_manifest.license_url != license_record.license_url
            or model_manifest.requires_terms_acceptance
            is not license_record.requires_terms_acceptance
        ):
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED,
                "manifest license disclosure differs from the frozen upstream inventory",
            )
        for source in model_manifest.component_sources:
            source_license = self.model_licenses.get(source.repository)
            if source_license is None or (
                source.license_id != source_license.license_id
                or source.license_url != source_license.license_url
                or source.requires_terms_acceptance is not source_license.requires_terms_acceptance
            ):
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED,
                    "manifest component license differs from the frozen upstream inventory",
                )
        plan = manager.request_user_install(model_manifest)
        remote_code_files = [
            item.path for item in model_manifest.files if item.kind == "remote_code"
        ]
        return {
            "confirmation_token": plan.confirmation_token,
            "model_id": plan.model_id,
            "revision": plan.revision,
            "license_id": plan.license_id,
            "license_url": plan.license_url,
            "requires_terms_acceptance": plan.requires_terms_acceptance,
            "component_sources": _component_source_payload(plan.component_sources),
            "estimated_download_bytes": plan.estimated_download_bytes,
            "installed_size_bytes": plan.installed_size_bytes,
            "required_free_bytes": plan.required_free_bytes,
            "available_bytes": plan.available_bytes,
            "environment": plan.environment,
            "remote_code_file_count": len(remote_code_files),
            "remote_code_files": remote_code_files,
            "expires_at": plan.expires_at.isoformat(),
        }

    def confirm_model_install(
        self,
        model_id: str,
        *,
        confirmation_token: str,
        health_recording_id: str,
        health_language: str,
        health_transcript: str | None,
        terms_accepted: bool,
    ) -> dict[str, Any]:
        entry = self._registry_model(model_id)
        manager = self._model_manager()
        if "alignment" in entry.tasks and not (health_transcript and health_transcript.strip()):
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                "an exact transcript is required to confirm an aligner installation",
            )
        if self.model_downloader is None or self.model_health_check is None:
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                "the production downloader and inference health checker are unavailable",
            )
        health_recording = self.recording(health_recording_id)
        audio_qc = health_recording.get("audio_qc")
        if not isinstance(audio_qc, dict) or audio_qc.get("health_wav_eligible") is not True:
            raise ClassScribeError(
                ErrorCode.MODEL_HEALTH_CHECK_FAILED,
                "health recording must be a non-empty 16 kHz mono 16-bit PCM WAV "
                "of at most 15 seconds",
            )
        health_audio, _source_name = self.recording_file(health_recording_id)

        def run_health(
            model_path: Path, audio_path: Path, environment: Mapping[str, str]
        ) -> HealthCheckOutcome:
            assert self.model_health_check is not None
            return self.model_health_check(
                entry,
                model_path,
                audio_path,
                environment,
                health_language,
                health_transcript,
            )

        result = manager.install_confirmed(
            confirmation_token,
            downloader=self.model_downloader,
            health_audio=health_audio,
            health_check=run_health,
            terms_accepted=terms_accepted,
            expected_model_id=entry.id,
        )
        if self.resident_workers is not None:
            self.resident_workers.request_refresh()
        return {
            "model_id": result.model_id,
            "revision": result.revision,
            "sha256": result.aggregate_sha256,
            "health": {
                "healthy": result.health.healthy,
                "measured_vram_mb": result.health.measured_vram_mb,
                "detail": result.health.detail,
                "environment": dict(result.health.environment),
            },
            "installed": True,
        }

    def verify_model(self, model_id: str) -> dict[str, Any]:
        entry = self._registry_model(model_id)
        self._model_manager().resolve_for_runtime(model_id)
        with self.sessions.begin() as session:
            installation = session.scalar(
                select(ModelInstallation).where(
                    ModelInstallation.model_id == model_id,
                    ModelInstallation.revision == entry.revision,
                )
            )
            if installation is not None:
                installation.health_status = ModelHealth.HEALTHY
                installation.checked_at = datetime.now(UTC)
        return {"model_id": model_id, "revision": entry.revision, "verified": True}

    def delete_model(self, model_id: str, revision: str) -> dict[str, Any]:
        self._model_manager().delete_revision(model_id, revision)
        return {"model_id": model_id, "revision": revision, "deleted": True}

    def rollback_model(self, model_id: str, revision: str) -> dict[str, Any]:
        self._model_manager().rollback(model_id, revision)
        return {"model_id": model_id, "revision": revision, "active": True}

    def profiles(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            overrides = {
                (item.language, item.scenario): item
                for item in session.scalars(select(ProfileSetting))
            }
            result: list[dict[str, Any]] = []
            for name, ranking in sorted(self.registry.rankings.items()):
                parts = name.split(".")
                if len(parts) == 2 and parts[0] == "classroom" and parts[1] in {"zh", "ja", "en"}:
                    scenario, language = "classroom", parts[1]
                elif len(parts) == 3 and parts[0] == "ibus" and parts[1] in {"zh", "ja", "en"}:
                    scenario, language = f"ibus.{parts[2]}", parts[1]
                else:
                    continue
                override = overrides.get((language, scenario))
                result.append(
                    {
                        "language": language,
                        "scenario": scenario,
                        "models": (
                            override.config_json.get("models", [])
                            if override is not None
                            else list(ranking)
                        ),
                        "source": (
                            "local_benchmark"
                            if override is not None and override.benchmark_run_id is not None
                            else "bootstrap"
                        ),
                        "benchmark_run_id": override.benchmark_run_id if override else None,
                    }
                )
            return result

    def update_profile(self, language: str, scenario: str, value: ProfileUpdate) -> dict[str, Any]:
        if language not in {"zh", "ja", "en"} or scenario not in {
            "classroom",
            "ibus.fast",
            "ibus.balanced",
            "ibus.accuracy",
        }:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "invalid profile identity")
        ordered = (value.primary_model_id, *value.fallback_model_ids)
        entries = tuple(self._registry_model(model_id) for model_id in ordered)
        with self.sessions.begin() as session:
            benchmark = session.get(BenchmarkRun, _id(value.benchmark_run_id, "benchmark_run_id"))
            if benchmark is None or not _benchmark_is_release_eligible(benchmark):
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "automatic-best profile requires a completed real production-gold benchmark",
                )
            if len(set(ordered)) != len(ordered):
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "profile models repeat")
            report_scenario = "ibus" if scenario.startswith("ibus.") else scenario
            report_ranking = next(
                (
                    item
                    for item in benchmark.ranking_json
                    if item.get("language") == language
                    and item.get("scenario") == report_scenario
                    and item.get("passed") is True
                ),
                None,
            )
            if report_ranking is None or report_ranking.get("models") != list(ordered):
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "profile models must exactly match a passed local benchmark ranking",
                )
            candidates = report_ranking.get("candidates")
            calibrations = benchmark.parameters_json.get("calibrations")
            manifest_sha256 = benchmark.parameters_json.get("manifest_sha256")
            if not isinstance(candidates, list) or not isinstance(calibrations, list):
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "profile benchmark lacks ranked candidates or calibration artifacts",
                )
            for entry in entries:
                ranked = next(
                    (
                        item
                        for item in candidates
                        if isinstance(item, dict)
                        and item.get("model_id") == entry.id
                        and item.get("eligible") is True
                    ),
                    None,
                )
                calibration = next(
                    (
                        item
                        for item in calibrations
                        if isinstance(item, dict)
                        and item.get("model_id") == entry.id
                        and item.get("language") == language
                        and item.get("scenario") == report_scenario
                    ),
                    None,
                )
                if (
                    ranked is None
                    or calibration is None
                    or ranked.get("model_revision") != entry.revision
                    or calibration.get("model_revision") != entry.revision
                    or calibration.get("manifest_sha256") != manifest_sha256
                ):
                    raise ClassScribeError(
                        ErrorCode.JOB_STATE_CONFLICT,
                        f"profile calibration is stale or missing for {entry.id}",
                    )
            setting = session.scalar(
                select(ProfileSetting).where(
                    ProfileSetting.language == language, ProfileSetting.scenario == scenario
                )
            )
            if setting is None:
                registry_key = (
                    f"classroom.{language}"
                    if scenario == "classroom"
                    else f"ibus.{language}.{scenario.removeprefix('ibus.')}"
                )
                history: list[dict[str, Any]] = [
                    {
                        "models": list(self.registry.rankings[registry_key]),
                        "benchmark_run_id": None,
                    }
                ]
                setting = ProfileSetting(language=language, scenario=scenario, version=1)
                session.add(setting)
            else:
                raw_history = setting.config_json.get("history", [])
                history = list(raw_history) if isinstance(raw_history, list) else []
                history.append(
                    {
                        "models": list(setting.config_json.get("models", [])),
                        "benchmark_run_id": setting.benchmark_run_id,
                    }
                )
                setting.version += 1
            setting.config_json = {"models": list(ordered), "history": history}
            setting.benchmark_run_id = benchmark.id
        if self.resident_workers is not None and scenario.startswith("ibus."):
            self.resident_workers.request_refresh()
        return next(
            item
            for item in self.profiles()
            if item["language"] == language and item["scenario"] == scenario
        )

    def rollback_profile(self, language: str, scenario: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            setting = session.scalar(
                select(ProfileSetting).where(
                    ProfileSetting.language == language, ProfileSetting.scenario == scenario
                )
            )
            if setting is None:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "profile has no rollback history"
                )
            raw_history = setting.config_json.get("history", [])
            if not isinstance(raw_history, list) or not raw_history:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "profile has no rollback history"
                )
            previous = raw_history[-1]
            if not isinstance(previous, dict) or not isinstance(previous.get("models"), list):
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "profile history is invalid")
            setting.config_json = {
                "models": list(previous["models"]),
                "history": raw_history[:-1],
            }
            benchmark_run_id = previous.get("benchmark_run_id")
            setting.benchmark_run_id = (
                str(benchmark_run_id) if benchmark_run_id is not None else None
            )
            setting.version += 1
        if self.resident_workers is not None and scenario.startswith("ibus."):
            self.resident_workers.request_refresh()
        return next(
            item
            for item in self.profiles()
            if item["language"] == language and item["scenario"] == scenario
        )

    def settings(self) -> dict[str, Any]:
        with self.sessions() as session:
            return {item.key: item.value_json for item in session.scalars(select(AppSetting))}

    def update_settings(self, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = values.keys() - _SETTINGS_KEYS
        if unknown:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT, f"unsupported settings: {sorted(unknown)}"
            )
        with self.sessions.begin() as session:
            for key, value in values.items():
                if not isinstance(value, dict):
                    raise ClassScribeError(
                        ErrorCode.JOB_STATE_CONFLICT, "each setting group must be an object"
                    )
                setting = session.get(AppSetting, key) or AppSetting(key=key)
                setting.value_json = dict(value)
                session.add(setting)
        return self.settings()

    def glossaries(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [
                self._glossary_payload(session, item)
                for item in session.scalars(select(Glossary).order_by(Glossary.name, Glossary.id))
            ]

    def create_glossary(self, value: GlossaryCreate) -> dict[str, Any]:
        with self.sessions.begin() as session:
            glossary = Glossary(name=value.name.strip(), course_id=value.course_id)
            session.add(glossary)
            session.flush()
            identifier = glossary.id
        return self.glossary(identifier)

    def glossary(self, glossary_id: str) -> dict[str, Any]:
        identifier = _id(glossary_id, "glossary_id")
        with self.sessions() as session:
            glossary = session.get(Glossary, identifier)
            if glossary is None:
                raise _not_found("glossary")
            return self._glossary_payload(session, glossary)

    def update_terms(self, glossary_id: str, value: GlossaryTermsUpdate) -> dict[str, Any]:
        identifier = _id(glossary_id, "glossary_id")
        terms = tuple(
            CourseTerm(
                canonical=item.canonical,
                reading=item.reading,
                aliases=item.aliases,
                weight=item.weight,
                source=TermSource(item.source),
                confirmation=(
                    ConfirmationStatus.CONFIRMED if item.confirmed else ConfirmationStatus.SUGGESTED
                ),
                language=item.language,
            )
            for item in value.terms
        )
        with self.sessions() as session:
            if session.get(Glossary, identifier) is None:
                raise _not_found("glossary")
        confirmed = tuple(item for item in terms if item.user_confirmed)
        suggested = tuple(item for item in terms if not item.user_confirmed)
        self._upsert_glossary_terms(identifier, confirmed)
        if suggested:
            TerminologyRepository(self.sessions).record_suggestions(
                identifier, suggested, default_language=suggested[0].language or "en"
            )
        return self.glossary(identifier)

    def delete_glossary(self, glossary_id: str) -> dict[str, Any]:
        identifier = _id(glossary_id, "glossary_id")
        with self.sessions.begin() as session:
            glossary = session.get(Glossary, identifier)
            if glossary is None:
                raise _not_found("glossary")
            materials = list(
                session.scalars(
                    select(GlossaryMaterial).where(GlossaryMaterial.glossary_id == identifier)
                )
            )
            paths = [self.paths.data_path(*Path(item.relative_path).parts) for item in materials]
            for path in paths:
                path.unlink(missing_ok=True)
            session.delete(glossary)
        return {"glossary_id": identifier, "deleted": True}

    def delete_term(self, glossary_id: str, term_id: str) -> dict[str, Any]:
        glossary = _id(glossary_id, "glossary_id")
        term = _id(term_id, "term_id")
        with self.sessions.begin() as session:
            record = session.get(GlossaryTerm, term)
            if record is None or record.glossary_id != glossary:
                raise _not_found("glossary term")
            session.delete(record)
        return {"term_id": term, "deleted": True}

    def add_glossary_document(
        self,
        glossary_id: str,
        *,
        source_name: str,
        source_kind: TermSource,
        language: str,
        content: bytes,
    ) -> dict[str, Any]:
        identifier = _id(glossary_id, "glossary_id")
        if Path(source_name).name != source_name or not source_name:
            raise ClassScribeError(ErrorCode.INVALID_FILE_ID, "material name must not be a path")
        with self.sessions() as session:
            if session.get(Glossary, identifier) is None:
                raise _not_found("glossary")
        file_id = str(uuid4())
        relative = Path(
            "glossaries", identifier, "materials", f"{file_id}{Path(source_name).suffix}"
        )
        target = self.paths.data_path(*relative.parts)
        atomic_write_bytes(target, content)
        imported = import_material(target, language=language, source=source_kind)
        digest = hashlib.sha256(content).hexdigest()
        with self.sessions.begin() as session:
            session.add(
                GlossaryMaterial(
                    glossary_id=identifier,
                    source_name=source_name,
                    source_kind=source_kind.value,
                    file_id=file_id,
                    relative_path=relative.as_posix(),
                    sha256=digest,
                    suggestion_count=len(imported.suggestions),
                )
            )
        if imported.suggestions:
            TerminologyRepository(self.sessions).record_suggestions(
                identifier, imported.suggestions, default_language=language
            )
        return {
            "file_id": file_id,
            "source_name": source_name,
            "source_kind": source_kind.value,
            "sha256": digest,
            "suggestion_count": len(imported.suggestions),
        }

    def create_export(self, job_id: str, value: ExportCreate) -> dict[str, Any]:
        identifier = _id(job_id, "job_id")
        segments = self._export_segments(identifier)
        content = render_export(
            segments,
            output_format=value.output_format,
            layer=value.layer,
            view=value.view,
            traditional_chinese=value.traditional_chinese,
        )
        artifact_id = str(uuid4())
        suffix = "md" if value.output_format.value == "md" else value.output_format.value
        file_name = f"classscribe-{identifier}-{value.layer.value}.{suffix}"
        relative = Path("jobs", identifier, "exports", f"{artifact_id}.{suffix}")
        target = self.paths.data_path(*relative.parts)
        atomic_write_text(target, content)
        digest = hashlib.sha256(content.encode()).hexdigest()
        with self.sessions.begin() as session:
            if session.get(Job, identifier) is None:
                raise _not_found("job")
            session.add(
                ExportArtifact(
                    id=artifact_id,
                    job_id=identifier,
                    output_format=value.output_format.value,
                    text_layer=value.layer.value,
                    view=value.view.value,
                    file_name=file_name,
                    relative_path=relative.as_posix(),
                    content_type=_CONTENT_TYPES[value.output_format.value],
                    sha256=digest,
                    size_bytes=len(content.encode()),
                )
            )
        return self.export_metadata(artifact_id)

    def export_metadata(self, export_id: str) -> dict[str, Any]:
        identifier = _id(export_id, "export_id")
        with self.sessions() as session:
            artifact = session.get(ExportArtifact, identifier)
            if artifact is None:
                raise _not_found("export")
            return _export_payload(artifact)

    def export_file(self, export_id: str) -> tuple[Path, dict[str, Any]]:
        metadata = self.export_metadata(export_id)
        identifier = _id(export_id, "export_id")
        with self.sessions() as session:
            artifact = session.get_one(ExportArtifact, identifier)
            path = self.paths.data_path(*Path(artifact.relative_path).parts)
        if path.is_symlink() or not path.is_file():
            raise _not_found("export file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "export integrity check failed")
        return path, metadata

    def create_benchmark(self, value: BenchmarkCreate) -> dict[str, Any]:
        with self.sessions.begin() as session:
            run = BenchmarkRun(
                manifest_version=value.manifest_version,
                status=BenchmarkStatus.RUNNING,
                hardware_json={},
                parameters_json=value.parameters,
                metrics_json={},
                ranking_json=[],
            )
            session.add(run)
            session.flush()
            identifier = run.id
        return self.benchmark(identifier)

    def benchmark(self, benchmark_id: str) -> dict[str, Any]:
        identifier = _id(benchmark_id, "benchmark_id")
        with self.sessions() as session:
            run = session.get(BenchmarkRun, identifier)
            if run is None:
                raise _not_found("benchmark")
            return _benchmark_payload(run)

    def benchmark_results(self, benchmark_id: str) -> dict[str, Any]:
        identifier = _id(benchmark_id, "benchmark_id")
        with self.sessions() as session:
            run = session.get(BenchmarkRun, identifier)
            if run is None:
                raise _not_found("benchmark")
            items = tuple(
                session.scalars(
                    select(BenchmarkItem)
                    .where(BenchmarkItem.run_id == identifier)
                    .order_by(BenchmarkItem.item_key, BenchmarkItem.model_id)
                ).all()
            )
            return {
                "id": run.id,
                "status": run.status.value,
                "metrics": run.metrics_json,
                "ranking": run.ranking_json,
                "calibrations": run.parameters_json.get("calibrations", []),
                "items": [
                    {
                        "item_key": item.item_key,
                        "model_id": item.model_id,
                        "model_revision": item.model_revision,
                        "metrics": item.metrics_json,
                    }
                    for item in items
                ],
            }

    def apply_ranking(self, benchmark_id: str, value: ApplyRanking) -> dict[str, Any]:
        identifier = _id(benchmark_id, "benchmark_id")
        with self.sessions() as session:
            run = session.get(BenchmarkRun, identifier)
            if run is None or run.status is not BenchmarkStatus.COMPLETED:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT, "only a completed benchmark can be applied"
                )
            ranking = next(
                (
                    item
                    for item in run.ranking_json
                    if item.get("language") == value.language
                    and item.get("scenario") == value.scenario
                ),
                None,
            )
        if not ranking or not isinstance(ranking.get("models"), list) or not ranking["models"]:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT, "benchmark has no matching ranking"
            )
        models = tuple(str(item) for item in ranking["models"])
        return self.update_profile(
            value.language,
            "ibus.balanced" if value.scenario == "ibus" else value.scenario,
            ProfileUpdate(
                primary_model_id=models[0],
                fallback_model_ids=models[1:],
                benchmark_run_id=identifier,
            ),
        )

    def _segment_payload(self, session: Session, segment: TranscriptSegment) -> dict[str, Any]:
        tokens = tuple(
            session.scalars(
                select(TokenSpan)
                .where(TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None))
                .order_by(TokenSpan.start_sample, TokenSpan.id)
            ).all()
        )
        speaker_name = None
        if segment.speaker_id:
            mapping = session.scalar(
                select(SpeakerDisplayName).where(
                    SpeakerDisplayName.job_id == segment.job_id,
                    SpeakerDisplayName.speaker_global_id == segment.speaker_id,
                )
            )
            speaker_name = mapping.display_name if mapping else None
        return {
            "id": segment.id,
            "job_id": segment.job_id,
            "start_sample": segment.start_sample,
            "end_sample": segment.end_sample,
            "speaker_id": segment.speaker_id,
            "speaker_name": speaker_name,
            "language": segment.language.value,
            "raw_text": segment.raw_text,
            "faithful_text": segment.faithful_text,
            "smart_corrected_text": segment.smart_corrected_text,
            "user_text": segment.user_text,
            "auto_final_text": segment.smart_corrected_text or segment.faithful_text,
            "quality_score": segment.quality_score,
            "low_confidence": segment.quality_score is None or segment.quality_score < 0.82,
            "review_status": segment.review_status.value,
            "timing_quality": segment.timing_quality.value,
            "version": segment.version,
            "active": segment.is_active,
            "supersedes_segment_ids": segment.supersedes_segment_ids_json,
            "tokens": [
                {
                    "id": token.id,
                    "start_sample": token.start_sample,
                    "end_sample": token.end_sample,
                    "text": token.token,
                    "confidence": token.confidence,
                    "provenance": token.provenance_json,
                }
                for token in tokens
            ],
        }

    def _history_action(
        self, segment_id: str, expected_version: int, *, redo: bool
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            segment = session.get(TranscriptSegment, segment_id)
            if segment is None or segment.version != expected_version:
                raise ClassScribeError(
                    ErrorCode.SEGMENT_VERSION_CONFLICT, "segment version is stale"
                )
            events = tuple(
                session.scalars(
                    select(DecisionEvent)
                    .where(DecisionEvent.segment_id == segment_id)
                    .order_by(DecisionEvent.created_at.desc(), DecisionEvent.id.desc())
                ).all()
            )
            undo_stack: list[DecisionEvent] = []
            redo_stack: list[tuple[DecisionEvent, DecisionEvent]] = []
            for event in reversed(events):
                if event.event_type == "segment_text_edited":
                    undo_stack.append(event)
                    redo_stack.clear()
                elif event.event_type == "segment_edit_undone":
                    original = next(
                        (
                            item
                            for item in undo_stack
                            if item.id == event.input_json.get("edit_event_id")
                        ),
                        None,
                    )
                    if original is not None:
                        undo_stack.remove(original)
                        redo_stack.append((original, event))
                elif event.event_type == "segment_edit_redone":
                    pair = next(
                        (
                            item
                            for item in redo_stack
                            if item[1].id == event.input_json.get("undo_event_id")
                        ),
                        None,
                    )
                    if pair is not None:
                        redo_stack.remove(pair)
                        undo_stack.append(pair[0])
            action_type = "segment_edit_redone" if redo else "segment_edit_undone"
            if redo:
                if not redo_stack:
                    raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "nothing to redo")
                source, undone = redo_stack[-1]
                layer_name = str(source.input_json["layer"])
                text = undone.input_json["redo_text"]
                reference = undone.id
            else:
                if not undo_stack:
                    raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "nothing to undo")
                source = undo_stack[-1]
                layer_name = str(source.input_json["layer"])
                text = source.input_json["text"]
                reference = source.id
            previous = getattr(segment, layer_name)
            setattr(segment, layer_name, text)
            session.add(
                DecisionEvent(
                    segment_id=segment_id,
                    event_type=action_type,
                    actor_type="human",
                    actor_id="local-user",
                    input_json=(
                        {"undo_event_id": reference, "layer": layer_name, "text": previous}
                        if redo
                        else {
                            "edit_event_id": reference,
                            "layer": layer_name,
                            "text": previous,
                            "redo_text": previous,
                        }
                    ),
                    output_json={"layer": layer_name, "text": text},
                    rule_version="edit-history-v1",
                )
            )
        return self.segment(segment_id)

    @staticmethod
    def _derived_segment(
        original: TranscriptSegment,
        span: AudioSpan,
        text: str,
        *,
        supersedes: tuple[str, ...] | None = None,
    ) -> TranscriptSegment:
        return TranscriptSegment(
            job_id=original.job_id,
            start_sample=span.start_sample,
            end_sample=span.end_sample,
            speaker_id=original.speaker_id,
            language=original.language,
            raw_text=text,
            faithful_text=text,
            smart_corrected_text=text,
            user_text=text,
            review_status=original.review_status,
            timing_quality=original.timing_quality,
            quality_score=original.quality_score,
            supersedes_segment_ids_json=list(supersedes or (original.id,)),
        )

    def _export_segments(self, job_id: str) -> tuple[ExportSegment, ...]:
        with self.sessions() as session:
            if session.get(Job, job_id) is None:
                raise _not_found("job")
            segments = tuple(
                session.scalars(
                    select(TranscriptSegment)
                    .where(
                        TranscriptSegment.job_id == job_id,
                        TranscriptSegment.is_active.is_(True),
                    )
                    .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
                ).all()
            )
            names = {
                item.speaker_global_id: item.display_name
                for item in session.scalars(
                    select(SpeakerDisplayName).where(SpeakerDisplayName.job_id == job_id)
                )
            }
            result: list[ExportSegment] = []
            for segment in segments:
                tokens = tuple(
                    ExportToken(
                        token.token,
                        AudioSpan(token.start_sample, token.end_sample),
                        protected_group=(
                            str(token.provenance_json["protected_group"])
                            if token.provenance_json.get("protected_group")
                            else None
                        ),
                        provenance=dict(token.provenance_json),
                    )
                    for token in session.scalars(
                        select(TokenSpan)
                        .where(
                            TokenSpan.segment_id == segment.id,
                            TokenSpan.candidate_id.is_(None),
                        )
                        .order_by(TokenSpan.start_sample, TokenSpan.id)
                    )
                )
                result.append(
                    ExportSegment(
                        segment.id,
                        AudioSpan(segment.start_sample, segment.end_sample),
                        segment.language.value,
                        segment.raw_text,
                        segment.faithful_text,
                        segment.smart_corrected_text,
                        segment.user_text,
                        tokens,
                        smart_tokens=tokens,
                        user_tokens=tokens if segment.user_text else (),
                        speaker=names.get(segment.speaker_id, segment.speaker_id)
                        if segment.speaker_id
                        else None,
                    )
                )
            return tuple(result)

    def _glossary_payload(self, session: Session, glossary: Glossary) -> dict[str, Any]:
        terms = tuple(
            session.scalars(
                select(GlossaryTerm)
                .where(GlossaryTerm.glossary_id == glossary.id)
                .order_by(GlossaryTerm.language, GlossaryTerm.canonical, GlossaryTerm.id)
            ).all()
        )
        materials = tuple(
            session.scalars(
                select(GlossaryMaterial)
                .where(GlossaryMaterial.glossary_id == glossary.id)
                .order_by(GlossaryMaterial.created_at, GlossaryMaterial.id)
            ).all()
        )
        return {
            "id": glossary.id,
            "name": glossary.name,
            "course_id": glossary.course_id,
            "version": glossary.version,
            "terms": [
                {
                    "id": term.id,
                    "canonical": term.canonical,
                    "reading": term.reading,
                    "aliases": term.aliases,
                    "language": term.language.value,
                    "weight": term.weight,
                    "source": term.source,
                    "confirmed": term.user_confirmed,
                }
                for term in terms
            ],
            "materials": [
                {
                    "file_id": item.file_id,
                    "source_name": item.source_name,
                    "source_kind": item.source_kind,
                    "sha256": item.sha256,
                    "suggestion_count": item.suggestion_count,
                }
                for item in materials
            ],
        }

    def _upsert_glossary_terms(self, glossary_id: str, terms: tuple[CourseTerm, ...]) -> None:
        with self.sessions.begin() as session:
            for term in terms:
                language = LanguageMode(term.language or "en")
                record = session.scalar(
                    select(GlossaryTerm).where(
                        GlossaryTerm.glossary_id == glossary_id,
                        GlossaryTerm.language == language,
                        GlossaryTerm.canonical == term.canonical,
                    )
                )
                if record is None:
                    record = GlossaryTerm(
                        glossary_id=glossary_id,
                        canonical=term.canonical,
                        language=language,
                    )
                    session.add(record)
                record.reading = term.reading
                record.aliases = list(term.aliases)
                record.weight = term.weight
                record.source = term.source.value
                record.user_confirmed = term.user_confirmed

    def _job_options(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            return dict(session.get_one(Job, job_id).options_json)

    def _pipeline(self) -> ClassroomPipeline:
        if self.pipeline is None:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "pipeline runtime is unavailable")
        return self.pipeline

    def _model_manager(self) -> ModelManager:
        if self.model_manager is None:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT, "model lifecycle runtime is unavailable"
            )
        return self.model_manager

    def _registry_model(self, model_id: str) -> ModelEntry:
        try:
            return self.registry.model(model_id)
        except KeyError as exc:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT, f"unknown model: {model_id}"
            ) from exc


def _id(value: str, field: str) -> str:
    return str(parse_uuid(value, field=field))


def _optional_id(value: str | None, field: str) -> str | None:
    return None if value is None else _id(value, field)


def _recording_payload(item: Recording) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_name": item.source_name,
        "sha256": item.source_sha256,
        "duration_samples": item.duration_samples,
        "sample_rate": item.sample_rate,
        "channels": item.channels,
        "audio_qc": item.audio_qc_json,
        "created_at": item.created_at.isoformat(),
        "media_url": f"/api/v1/recordings/{item.id}/media",
    }


def _health_wav_qc(
    content: bytes,
    *,
    duration_samples: int,
    channels: int,
    sample_rate: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {"health_wav_eligible": False}
    try:
        with wave.open(BytesIO(content), "rb") as recording:
            frames = recording.getnframes()
            actual_rate = recording.getframerate()
            actual_channels = recording.getnchannels()
            sample_width = recording.getsampwidth()
            compression = recording.getcomptype()
    except (EOFError, wave.Error):
        return result
    result.update(
        {
            "container": "wav",
            "compression": compression,
            "duration_samples": frames,
            "sample_rate": actual_rate,
            "channels": actual_channels,
            "sample_width_bytes": sample_width,
        }
    )
    result["health_wav_eligible"] = (
        frames > 0
        and frames <= 15 * 16_000
        and actual_rate == 16_000
        and actual_channels == 1
        and sample_width == 2
        and compression == "NONE"
        and duration_samples == frames
        and sample_rate == actual_rate
        and channels == actual_channels
    )
    return result


def _not_found(label: str) -> ClassScribeError:
    return ClassScribeError(ErrorCode.INVALID_FILE_ID, f"{label} not found")


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "recording_id": job.recording_id,
        "status": job.status.value,
        "stage": job.stage.value,
        "progress": job.progress,
        "error_code": job.error_code,
        "error_detail": job.error_detail,
        "options": job.options_json,
    }


def _decision_payload(event: DecisionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.event_type,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "input": event.input_json,
        "output": event.output_json,
        "rule_version": event.rule_version,
        "created_at": event.created_at.isoformat(),
    }


def _component_source_payload(
    sources: tuple[ManifestComponentSource, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "repository": source.repository,
            "revision": source.revision,
            "relationship": source.relationship,
            "license_id": source.license_id,
            "license_url": source.license_url,
            "requires_terms_acceptance": source.requires_terms_acceptance,
            "files": [
                {
                    "installed_path": item.installed_path,
                    "source_path": item.source_path,
                    "source_sha256": item.source_sha256,
                    "source_size_bytes": item.source_size_bytes,
                }
                for item in source.files
            ],
        }
        for source in sources
    ]


def _installation_payload(item: ModelInstallation | None) -> dict[str, Any]:
    if item is None:
        return {"state": "not_installed"}
    return {
        "state": item.health_status.value,
        "sha256": item.sha256,
        "measured_vram_mb": item.measured_vram_mb,
        "installed_at": item.installed_at.isoformat(),
        "checked_at": item.checked_at.isoformat() if item.checked_at else None,
    }


def _export_payload(item: ExportArtifact) -> dict[str, Any]:
    return {
        "id": item.id,
        "job_id": item.job_id,
        "format": item.output_format,
        "layer": item.text_layer,
        "view": item.view,
        "file_name": item.file_name,
        "content_type": item.content_type,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
        "created_at": item.created_at.isoformat(),
        "download_url": f"/api/v1/exports/{item.id}",
    }


def _benchmark_payload(item: BenchmarkRun) -> dict[str, Any]:
    return {
        "id": item.id,
        "manifest_version": item.manifest_version,
        "status": item.status.value,
        "hardware": item.hardware_json,
        "parameters": item.parameters_json,
        "metrics": item.metrics_json,
        "ranking": item.ranking_json,
        "started_at": item.started_at.isoformat(),
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


def _benchmark_is_release_eligible(item: BenchmarkRun) -> bool:
    parameters = item.parameters_json
    manifest_sha256 = parameters.get("manifest_sha256")
    return (
        item.status is BenchmarkStatus.COMPLETED
        and parameters.get("production_gold") is True
        and parameters.get("real_model_execution") is True
        and parameters.get("synthetic_gold") is False
        and isinstance(manifest_sha256, str)
        and len(manifest_sha256) == 64
        and all(character in "0123456789abcdef" for character in manifest_sha256)
    )
