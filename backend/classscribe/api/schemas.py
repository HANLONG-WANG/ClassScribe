"""Strict version-one HTTP request schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classscribe.contracts import LanguageMode, ModelSelectionMode
from classscribe.exports import ExportFormat, ExportLayer, ExportView


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobCreate(APIModel):
    recording_id: str
    language: LanguageMode
    glossary_id: str | None = None
    speaker_count: Literal["auto", "1", "2", "3-4", "5+"] = "auto"
    model_selection: ModelSelectionMode = ModelSelectionMode.AUTO_BEST
    primary_model_id: str | None = None
    accuracy_mode: Literal["fast", "balanced", "highest", "strict_single"] = "balanced"
    outputs: tuple[ExportFormat, ...] = (
        ExportFormat.JSON,
        ExportFormat.MARKDOWN,
        ExportFormat.SRT,
        ExportFormat.VTT,
    )
    include_faithful: bool = True
    include_smart: bool = True
    include_speakers: bool = True
    include_subtitles: bool = True

    @model_validator(mode="after")
    def validate_model_choice(self) -> JobCreate:
        if self.model_selection is ModelSelectionMode.MANUAL_PRIMARY and not self.primary_model_id:
            raise ValueError("manual primary selection requires primary_model_id")
        if self.accuracy_mode == "strict_single" and not self.primary_model_id:
            raise ValueError("strict single mode requires primary_model_id")
        if not self.outputs:
            raise ValueError("at least one output format is required")
        return self


class SegmentPatch(APIModel):
    version: int = Field(ge=1)
    layer: Literal["faithful", "smart", "user"] = "user"
    text: str | None = None
    speaker_name: str | None = None

    @model_validator(mode="after")
    def has_change(self) -> SegmentPatch:
        if self.text is None and self.speaker_name is None:
            raise ValueError("segment patch contains no change")
        return self


class CandidateAdoption(APIModel):
    candidate_id: str
    version: int = Field(ge=1)


class HistoryAction(APIModel):
    version: int = Field(ge=1)


class SegmentSplit(APIModel):
    split_sample: int = Field(gt=0)
    left_text: str
    right_text: str


class SegmentMerge(APIModel):
    segment_ids: tuple[str, ...] = Field(min_length=2)


class ProfileUpdate(APIModel):
    primary_model_id: str
    fallback_model_ids: tuple[str, ...] = ()
    benchmark_run_id: str


class SettingsUpdate(APIModel):
    values: dict[str, Any]


class GlossaryCreate(APIModel):
    name: str = Field(min_length=1, max_length=256)
    course_id: str | None = Field(default=None, max_length=128)


class GlossaryTermInput(APIModel):
    canonical: str = Field(min_length=1)
    reading: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    language: Literal["zh", "ja", "en"]
    weight: float = Field(default=1.0, ge=0, le=1)
    source: Literal[
        "manual",
        "txt",
        "markdown",
        "csv",
        "pptx",
        "pdf",
        "handout",
        "textbook",
        "historical_confirmed",
        "automatic_suggestion",
    ] = "manual"
    confirmed: bool = True

    @model_validator(mode="after")
    def bound_suggestion_weight(self) -> GlossaryTermInput:
        if not self.confirmed and self.weight > 0.3:
            raise ValueError("unconfirmed term weight may not exceed 0.3")
        return self


class GlossaryTermsUpdate(APIModel):
    terms: tuple[GlossaryTermInput, ...]


class ExportCreate(APIModel):
    output_format: ExportFormat
    layer: ExportLayer
    view: ExportView = ExportView.SENTENCES
    traditional_chinese: bool = False


class BenchmarkCreate(APIModel):
    manifest_version: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ApplyRanking(APIModel):
    language: Literal["zh", "ja", "en"]
    scenario: Literal["classroom", "ibus"]


class ModelInstallRequest(APIModel):
    manifest: dict[str, Any]


class ModelInstallConfirmation(APIModel):
    confirmation_token: str = Field(min_length=32, max_length=256)
    health_recording_id: str
    health_language: Literal["zh", "ja", "en"]
    health_transcript: str | None = Field(default=None, max_length=500)
    terms_accepted: bool = False


class ModelRevisionRequest(APIModel):
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
