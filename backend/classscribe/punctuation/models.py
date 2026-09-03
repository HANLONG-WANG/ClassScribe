"""Shared punctuation evidence and result values."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AcousticBoundary:
    after_character: int
    pause_ms: int = 0
    speaker_changed: bool = False
    rising_intonation: bool = False

    def __post_init__(self) -> None:
        if self.after_character < 0 or self.pause_ms < 0:
            raise ValueError("boundary character index and pause must be non-negative")


@dataclass(frozen=True, slots=True)
class BoundaryLabel:
    after_character: int
    punctuation: str
    confidence: float

    def __post_init__(self) -> None:
        if self.after_character < 0 or self.punctuation not in {
            "\u3001",
            "\u3002",
            "\uff1f",
            "\uff01",
        }:
            raise ValueError("Japanese boundary label is invalid")
        if not 0 <= self.confidence <= 1:
            raise ValueError("boundary-label confidence must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class PunctuationResult:
    text: str
    source: str
    character_sequence_preserved: bool
    rejected_sources: tuple[str, ...] = ()
    metrics: dict[str, int | float | str | bool | None] = field(default_factory=dict)
    rule_version: str = "strict-punctuation-v1"
