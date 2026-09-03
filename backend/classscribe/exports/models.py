"""Typed, layer-aware export records with final absolute sample timing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from classscribe.timeline import AudioSpan


class ExportFormat(StrEnum):
    TXT = "txt"
    MARKDOWN = "md"
    JSON = "json"
    SRT = "srt"
    VTT = "vtt"
    CSV = "csv"


class ExportLayer(StrEnum):
    FAITHFUL = "faithful"
    SMART = "smart"
    USER = "user"


class ExportView(StrEnum):
    SENTENCES = "sentences"
    READABLE_PARAGRAPHS = "readable_paragraphs"


@dataclass(frozen=True, slots=True)
class ExportToken:
    text: str
    span: AudioSpan
    protected_group: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text or self.span.duration_samples <= 0:
            raise ValueError("export tokens require text and positive final time")


@dataclass(frozen=True, slots=True)
class ExportSegment:
    segment_id: str
    span: AudioSpan
    language: str
    raw_text: str
    faithful_text: str
    smart_corrected_text: str
    user_text: str | None
    faithful_tokens: tuple[ExportToken, ...]
    smart_tokens: tuple[ExportToken, ...] = ()
    user_tokens: tuple[ExportToken, ...] = ()
    speaker: str | None = None
    pause_before_ms: int = 0
    chapter_marker: str | None = None
    semantic_boundary_before: bool = False

    def __post_init__(self) -> None:
        if not self.segment_id or self.language not in {"zh", "ja", "en"}:
            raise ValueError("export segment identity/language is invalid")
        if self.span.duration_samples <= 0 or self.pause_before_ms < 0:
            raise ValueError("export segment timing is invalid")
        for tokens in (self.faithful_tokens, self.smart_tokens, self.user_tokens):
            previous_end = self.span.start_sample
            for token in tokens:
                if (
                    token.span.start_sample < previous_end
                    or token.span.start_sample < self.span.start_sample
                    or token.span.end_sample > self.span.end_sample
                ):
                    raise ValueError("export token times must be monotonic inside their segment")
                previous_end = token.span.end_sample

    def text_for(self, layer: ExportLayer) -> tuple[str, ExportLayer]:
        if layer is ExportLayer.USER:
            if self.user_text is not None and self.user_text.strip():
                return self.user_text, ExportLayer.USER
            if self.smart_corrected_text:
                return self.smart_corrected_text, ExportLayer.SMART
        if layer in {ExportLayer.USER, ExportLayer.SMART} and self.smart_corrected_text:
            return self.smart_corrected_text, ExportLayer.SMART
        return self.faithful_text, ExportLayer.FAITHFUL

    def tokens_for(self, layer: ExportLayer) -> tuple[ExportToken, ...]:
        _, resolved = self.text_for(layer)
        if resolved is ExportLayer.USER and self.user_tokens:
            return self.user_tokens
        if resolved is ExportLayer.SMART and self.smart_tokens:
            return self.smart_tokens
        return self.faithful_tokens


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    start_sample: int
    end_sample: int
    lines: tuple[str, ...]
    segment_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.start_sample < 0 or self.end_sample <= self.start_sample:
            raise ValueError("subtitle cue timing must be positive and ordered")
        if not 1 <= len(self.lines) <= 2 or any(not line for line in self.lines):
            raise ValueError("subtitle cues require one or two non-empty lines")
