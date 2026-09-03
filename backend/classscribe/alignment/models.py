"""Canonical timing evidence and selection results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from classscribe.timeline import AudioSpan


class TimingSource(StrEnum):
    NATIVE_WORD = "reliable_native_word_timestamps"
    MOSS_STRUCTURE = "moss_native_structure_timing"
    QWEN_FORCED = "qwen_forced_aligner"
    VAD_COARSE = "vad_coarse_timing"


@dataclass(frozen=True, slots=True)
class AlignedToken:
    text: str
    span: AudioSpan

    def __post_init__(self) -> None:
        if not self.text or self.span.duration_samples <= 0:
            raise ValueError("aligned tokens require text and a positive duration")


@dataclass(frozen=True, slots=True)
class TimingEvidence:
    source: TimingSource
    canonical_span: AudioSpan
    final_text: str
    tokens: tuple[AlignedToken, ...]
    reliable: bool
    text_unchanged: bool
    alignment_cost: float | None = None
    vad_gap_error_ms: float | None = None

    def __post_init__(self) -> None:
        for value in (self.alignment_cost, self.vad_gap_error_ms):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("alignment costs/gaps must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class TimingValidation:
    valid: bool
    speech_coverage: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalTiming:
    source: TimingSource
    canonical_span: AudioSpan
    final_text: str
    tokens: tuple[AlignedToken, ...]
    coarse_timing: bool
    speech_coverage: float
    fallback_reasons: tuple[str, ...] = ()
    rule_version: str = "canonical-timing-priority-v1"
