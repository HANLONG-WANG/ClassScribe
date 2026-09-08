"""Complete stage-2 persistence model for local ClassScribe state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from classscribe.contracts import LanguageMode
from classscribe.db.base import Base


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


def enum_type(enum: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


class JobStage(StrEnum):
    CREATED = "created"
    AUDIO_IMPORT = "audio_import"
    AUDIO_QC = "audio_qc"
    VAD = "vad"
    LID = "lid"
    STRUCTURE = "structure"
    TRANSCRIPTION = "transcription"
    QUALITY = "quality"
    POSTPROCESS = "postprocess"
    ALIGNMENT = "alignment"
    EXPORT = "export"
    COMPLETED = "completed"


class CheckpointStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    RETRYABLE = "retryable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus(StrEnum):
    AUTO = "auto"
    NEEDS_REVIEW = "needs_review"
    REVIEWED = "reviewed"


class TimingQuality(StrEnum):
    STRUCTURE = "structure"
    NATIVE = "native"
    ALIGNED = "aligned"
    INVALID = "invalid"


class ModelHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    MISSING = "missing"


class BenchmarkStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DictationStatus(StrEnum):
    ACTIVE = "active"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Recording(Base):
    __tablename__ = "recordings"
    __table_args__ = (
        CheckConstraint("duration_samples >= 0", name="duration_nonnegative"),
        CheckConstraint("sample_rate > 0", name="sample_rate_positive"),
        CheckConstraint("channels > 0", name="channels_positive"),
        Index("ix_recordings_sha256", "source_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_name: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_path: Mapped[str] = mapped_column(Text, unique=True)
    duration_samples: Mapped[int] = mapped_column(Integer)
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    audio_qc_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    jobs: Mapped[list[Job]] = relationship(back_populates="recording", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 1", name="progress_range"),
        Index("ix_jobs_recovery", "status", "stage", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id", ondelete="RESTRICT"), index=True
    )
    language_mode: Mapped[LanguageMode] = mapped_column(enum_type(LanguageMode, "language_mode"))
    profile_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, "job_status"), default=JobStatus.PENDING, index=True
    )
    stage: Mapped[JobStage] = mapped_column(
        enum_type(JobStage, "job_stage"), default=JobStage.CREATED
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    options_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    queue_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", index=True)
    submission_key: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)

    recording: Mapped[Recording] = relationship(back_populates="jobs")
    checkpoints: Mapped[list[JobCheckpoint]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    speech_regions: Mapped[list[SpeechRegion]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    language_spans: Mapped[list[LanguageSpan]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    speaker_spans: Mapped[list[SpeakerSpan]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    structure_segments: Mapped[list[StructureSegmentRecord]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    speaker_display_names: Mapped[list[SpeakerDisplayName]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    transcript_segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobCheckpoint(Base):
    __tablename__ = "job_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "stage",
            "checkpoint_key",
            "segment_id",
            name="uq_job_checkpoint_scope",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_job_checkpoints_resume", "job_id", "stage", "status", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    stage: Mapped[JobStage] = mapped_column(enum_type(JobStage, "checkpoint_job_stage"))
    checkpoint_key: Mapped[str] = mapped_column(String(128))
    segment_id: Mapped[str | None] = mapped_column(String(36))
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[CheckpointStatus] = mapped_column(
        enum_type(CheckpointStatus, "checkpoint_status"), default=CheckpointStatus.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    parameter_hash: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    job: Mapped[Job] = relationship(back_populates="checkpoints")


class SpeechRegion(Base):
    __tablename__ = "speech_regions"
    __table_args__ = (
        CheckConstraint("start_sample >= 0", name="start_nonnegative"),
        CheckConstraint("end_sample >= start_sample", name="ordered_samples"),
        Index("ix_speech_regions_timeline", "job_id", "start_sample", "end_sample"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    start_sample: Mapped[int] = mapped_column(Integer)
    end_sample: Mapped[int] = mapped_column(Integer)
    vad_score: Mapped[float] = mapped_column(Float)
    acoustic_class: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(128))

    job: Mapped[Job] = relationship(back_populates="speech_regions")


class LanguageSpan(Base):
    __tablename__ = "language_spans"
    __table_args__ = (
        CheckConstraint("start_sample >= 0", name="start_nonnegative"),
        CheckConstraint("end_sample >= start_sample", name="ordered_samples"),
        Index("ix_language_spans_timeline", "job_id", "start_sample", "end_sample"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    start_sample: Mapped[int] = mapped_column(Integer)
    end_sample: Mapped[int] = mapped_column(Integer)
    language: Mapped[LanguageMode] = mapped_column(enum_type(LanguageMode, "span_language"))
    confidence_raw: Mapped[float] = mapped_column(Float)
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped[Job] = relationship(back_populates="language_spans")


class SpeakerSpan(Base):
    __tablename__ = "speaker_spans"
    __table_args__ = (
        CheckConstraint("window_ordinal >= 0", name="window_ordinal_nonnegative"),
        CheckConstraint("start_sample >= 0", name="start_nonnegative"),
        CheckConstraint("end_sample >= start_sample", name="ordered_samples"),
        Index("ix_speaker_spans_timeline", "job_id", "start_sample", "end_sample"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    window_ordinal: Mapped[int] = mapped_column(Integer, default=0)
    start_sample: Mapped[int] = mapped_column(Integer)
    end_sample: Mapped[int] = mapped_column(Integer)
    speaker_global_id: Mapped[str] = mapped_column(String(128), index=True)
    speaker_local_id: Mapped[str] = mapped_column(String(128))
    overlap: Mapped[bool] = mapped_column(Boolean, default=False)
    source_model: Mapped[str] = mapped_column(String(128))
    source_revision: Mapped[str] = mapped_column(String(256))
    confidence: Mapped[float] = mapped_column(Float)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped[Job] = relationship(back_populates="speaker_spans")


class StructureSegmentRecord(Base):
    """Coarse structural evidence, deliberately separate from adopted transcript text."""

    __tablename__ = "structure_segments"
    __table_args__ = (
        CheckConstraint("window_ordinal >= 0", name="window_ordinal_nonnegative"),
        CheckConstraint("start_sample >= 0", name="start_nonnegative"),
        CheckConstraint("end_sample > start_sample", name="ordered_samples"),
        CheckConstraint(
            "selection_score >= 0 AND selection_score <= 1", name="selection_score_range"
        ),
        CheckConstraint("adopted_as_final = 0", name="coarse_text_never_final"),
        UniqueConstraint(
            "job_id",
            "window_ordinal",
            "start_sample",
            "end_sample",
            "source_model",
            "source_revision",
            name="uq_structure_segment_source",
        ),
        Index(
            "ix_structure_segments_timeline",
            "job_id",
            "start_sample",
            "end_sample",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    window_ordinal: Mapped[int] = mapped_column(Integer)
    start_sample: Mapped[int] = mapped_column(Integer)
    end_sample: Mapped[int] = mapped_column(Integer)
    speaker_global_id: Mapped[str | None] = mapped_column(String(128), index=True)
    speaker_local_id: Mapped[str | None] = mapped_column(String(128))
    coarse_text: Mapped[str] = mapped_column(Text, default="")
    acoustic_events_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    overlap: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    source_model: Mapped[str] = mapped_column(String(128))
    source_revision: Mapped[str] = mapped_column(String(256))
    confidence_raw: Mapped[float | None] = mapped_column(Float)
    selection_score: Mapped[float] = mapped_column(Float)
    text_role: Mapped[str] = mapped_column(String(128))
    adopted_as_final: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    job: Mapped[Job] = relationship(back_populates="structure_segments")


class SpeakerDisplayName(Base):
    """Job-local presentation mapping; raw diarization labels stay immutable."""

    __tablename__ = "speaker_display_names"
    __table_args__ = (
        UniqueConstraint("job_id", "speaker_global_id", name="uq_speaker_display_name"),
        CheckConstraint("identity_scope = 'job'", name="speaker_identity_job_scoped"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    speaker_global_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(256))
    identity_scope: Mapped[str] = mapped_column(String(32), default="job")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    job: Mapped[Job] = relationship(back_populates="speaker_display_names")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        CheckConstraint("start_sample >= 0", name="start_nonnegative"),
        CheckConstraint("end_sample >= start_sample", name="ordered_samples"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_transcript_segments_timeline", "job_id", "start_sample", "end_sample"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    start_sample: Mapped[int] = mapped_column(Integer)
    end_sample: Mapped[int] = mapped_column(Integer)
    speaker_id: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[LanguageMode] = mapped_column(enum_type(LanguageMode, "segment_language"))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    faithful_text: Mapped[str] = mapped_column(Text, default="")
    smart_corrected_text: Mapped[str] = mapped_column(Text, default="")
    user_text: Mapped[str | None] = mapped_column(Text)
    auto_final_source: Mapped[str | None] = mapped_column(String(128))
    quality_score: Mapped[float | None] = mapped_column(Float)
    timing_quality: Mapped[TimingQuality] = mapped_column(
        enum_type(TimingQuality, "timing_quality"), default=TimingQuality.STRUCTURE
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_type(ReviewStatus, "review_status"), default=ReviewStatus.AUTO
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    supersedes_segment_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    __mapper_args__: dict[str, Any] = {"version_id_col": version}  # noqa: RUF012
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    job: Mapped[Job] = relationship(back_populates="transcript_segments")
    candidates: Mapped[list[ASRCandidate]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )
    token_spans: Mapped[list[TokenSpan]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[DecisionEvent]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )


class ASRCandidate(Base):
    __tablename__ = "asr_candidates"
    __table_args__ = (
        Index("ix_asr_candidates_model", "segment_id", "model_id", "model_revision"),
        Index("ix_asr_candidates_active", "segment_id", "deleted_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE")
    )
    model_id: Mapped[str] = mapped_column(String(128))
    model_revision: Mapped[str] = mapped_column(String(256))
    raw_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    confidence_raw: Mapped[float | None] = mapped_column(Float)
    confidence_calibrated: Mapped[float | None] = mapped_column(Float)
    quality_features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decode_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    inference_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    is_adopted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    supersedes_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("asr_candidates.id", ondelete="RESTRICT")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    segment: Mapped[TranscriptSegment] = relationship(back_populates="candidates")
    token_spans: Mapped[list[TokenSpan]] = relationship(back_populates="candidate")


class TokenSpan(Base):
    __tablename__ = "token_spans"
    __table_args__ = (
        CheckConstraint("start_sample >= 0", name="start_nonnegative"),
        CheckConstraint("end_sample >= start_sample", name="ordered_samples"),
        Index("ix_token_spans_timeline", "segment_id", "start_sample", "end_sample"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("asr_candidates.id", ondelete="SET NULL"), index=True
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE")
    )
    start_sample: Mapped[int] = mapped_column(Integer)
    end_sample: Mapped[int] = mapped_column(Integer)
    token: Mapped[str] = mapped_column(Text)
    normalized_token: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON)

    candidate: Mapped[ASRCandidate | None] = relationship(back_populates="token_spans")
    segment: Mapped[TranscriptSegment] = relationship(back_populates="token_spans")


class DecisionEvent(Base):
    __tablename__ = "decision_events"
    __table_args__ = (Index("ix_decision_events_segment_created", "segment_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(128))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(String(128))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    rule_version: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    segment: Mapped[TranscriptSegment] = relationship(back_populates="decisions")


class Glossary(Base):
    __tablename__ = "glossaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(256))
    course_id: Mapped[str | None] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    terms: Mapped[list[GlossaryTerm]] = relationship(
        back_populates="glossary", cascade="all, delete-orphan"
    )


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"
    __table_args__ = (Index("ix_glossary_terms_lookup", "glossary_id", "language", "canonical"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    glossary_id: Mapped[str] = mapped_column(ForeignKey("glossaries.id", ondelete="CASCADE"))
    canonical: Mapped[str] = mapped_column(Text)
    reading: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    language: Mapped[LanguageMode] = mapped_column(enum_type(LanguageMode, "glossary_language"))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(128))
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    glossary: Mapped[Glossary] = relationship(back_populates="terms")


class GlossaryMaterial(Base):
    __tablename__ = "glossary_materials"
    __table_args__ = (Index("ix_glossary_materials_glossary", "glossary_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    glossary_id: Mapped[str] = mapped_column(ForeignKey("glossaries.id", ondelete="CASCADE"))
    source_name: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(32))
    file_id: Mapped[str] = mapped_column(String(36), unique=True)
    relative_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    suggestion_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"
    __table_args__ = (Index("ix_export_artifacts_job", "job_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    output_format: Mapped[str] = mapped_column(String(16))
    text_layer: Mapped[str] = mapped_column(String(16))
    view: Mapped[str] = mapped_column(String(32))
    file_name: Mapped[str] = mapped_column(Text)
    relative_path: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProfileSetting(Base):
    __tablename__ = "profile_settings"
    __table_args__ = (
        UniqueConstraint("language", "scenario", name="uq_profile_language_scenario"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    language: Mapped[str] = mapped_column(String(16))
    scenario: Mapped[str] = mapped_column(String(64))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    benchmark_run_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ModelInstallation(Base):
    __tablename__ = "model_installations"
    __table_args__ = (UniqueConstraint("model_id", "revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    model_id: Mapped[str] = mapped_column(String(128), index=True)
    repository: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(String(256))
    local_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    environment_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    measured_vram_mb: Mapped[int | None] = mapped_column(Integer)
    health_status: Mapped[ModelHealth] = mapped_column(
        enum_type(ModelHealth, "model_health"), default=ModelHealth.UNKNOWN
    )
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    manifest_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[BenchmarkStatus] = mapped_column(
        enum_type(BenchmarkStatus, "benchmark_status"), default=BenchmarkStatus.RUNNING
    )
    hardware_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ranking_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[BenchmarkItem]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BenchmarkItem(Base):
    __tablename__ = "benchmark_items"
    __table_args__ = (Index("ix_benchmark_items_model", "run_id", "model_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("benchmark_runs.id", ondelete="CASCADE"))
    item_key: Mapped[str] = mapped_column(String(256))
    model_id: Mapped[str] = mapped_column(String(128))
    model_revision: Mapped[str] = mapped_column(String(256))
    gold_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    prediction_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON)

    run: Mapped[BenchmarkRun] = relationship(back_populates="items")


class DictationSession(Base):
    __tablename__ = "dictation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    language_mode: Mapped[LanguageMode] = mapped_column(
        enum_type(LanguageMode, "dictation_language")
    )
    profile_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[DictationStatus] = mapped_column(
        enum_type(DictationStatus, "dictation_status"), default=DictationStatus.ACTIVE
    )
    retain_history: Mapped[bool] = mapped_column(Boolean, default=False)
    committed_text: Mapped[str | None] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "retain_history = 1 OR audio_path IS NULL", name="audio_requires_history_opt_in"
        ),
    )
