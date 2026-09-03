"""Versioned messages and state shared by the thin IBus desktop processes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DictationState(StrEnum):
    IDLE = "idle"
    ARMING = "arming"
    LISTENING = "listening"
    INTERIM_UPDATE = "interim_update"
    FINALIZING = "finalizing"
    CANDIDATE_SELECT = "candidate_select"
    COMMITTING = "committing"
    ERROR = "error"


class DictationLanguage(StrEnum):
    CHINESE = "zh"
    JAPANESE = "ja"
    ENGLISH = "en"
    AUTO_MIXED = "auto"


class ConfirmationMode(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURACY = "accuracy"


class ActivationMode(StrEnum):
    HOLD = "hold"
    TOGGLE = "toggle"


class DictationPunctuation(StrEnum):
    AUTOMATIC = "automatic"
    SENTENCE_END = "sentence_end"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class DictationConfig:
    language: DictationLanguage = DictationLanguage.AUTO_MIXED
    confirmation: ConfirmationMode = ConfirmationMode.BALANCED
    activation: ActivationMode = ActivationMode.HOLD
    model_id: str = "auto_best"
    show_interim: bool = True
    punctuation: DictationPunctuation = DictationPunctuation.AUTOMATIC
    stream_chunk_ms: int = 560
    endpoint_silence_ms: int = 650
    hard_chunk_seconds: int = 25
    overlap_seconds: float = 2.0
    rolling_context_segments: int = 3
    stable_prefix_seconds: int = 45

    def __post_init__(self) -> None:
        if self.stream_chunk_ms not in {80, 160, 320, 560, 1120}:
            raise ValueError("stream chunk must match a supported model cache interval")
        if not 300 <= self.endpoint_silence_ms <= 2000:
            raise ValueError("semantic endpoint silence must be 300..2000 ms")
        if not 20 <= self.hard_chunk_seconds <= 30:
            raise ValueError("hard chunk must be 20..30 seconds")
        if not 1.5 <= self.overlap_seconds <= 2.5:
            raise ValueError("hard-chunk overlap must be 1.5..2.5 seconds")
        if not 1 <= self.rolling_context_segments <= 5:
            raise ValueError("rolling context must retain 1..5 segments")
        if not 1 <= self.stable_prefix_seconds <= 60:
            raise ValueError("stable prefix horizon must be 1..60 seconds")
        if not self.model_id:
            raise ValueError("dictation model identity must not be empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language.value,
            "confirmation": self.confirmation.value,
            "activation": self.activation.value,
            "model_id": self.model_id,
            "show_interim": self.show_interim,
            "punctuation": self.punctuation.value,
            "stream_chunk_ms": self.stream_chunk_ms,
            "endpoint_silence_ms": self.endpoint_silence_ms,
            "hard_chunk_seconds": self.hard_chunk_seconds,
            "overlap_seconds": self.overlap_seconds,
            "rolling_context_segments": self.rolling_context_segments,
            "stable_prefix_seconds": self.stable_prefix_seconds,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> DictationConfig:
        """Decode a complete, strict desktop-process configuration payload."""

        expected = {
            "language",
            "confirmation",
            "activation",
            "model_id",
            "show_interim",
            "punctuation",
            "stream_chunk_ms",
            "endpoint_silence_ms",
            "hard_chunk_seconds",
            "overlap_seconds",
            "rolling_context_segments",
            "stable_prefix_seconds",
        }
        if unknown := value.keys() - expected:
            raise ValueError(f"unknown dictation config fields: {sorted(unknown)}")
        show_interim = value.get("show_interim", True)
        if not isinstance(show_interim, bool):
            raise ValueError("show_interim must be boolean")
        return cls(
            language=DictationLanguage(str(value.get("language", "auto"))),
            confirmation=ConfirmationMode(str(value.get("confirmation", "balanced"))),
            activation=ActivationMode(str(value.get("activation", "hold"))),
            model_id=str(value.get("model_id", "auto_best")),
            show_interim=show_interim,
            punctuation=DictationPunctuation(str(value.get("punctuation", "automatic"))),
            stream_chunk_ms=_integer(value.get("stream_chunk_ms", 560), "stream_chunk_ms"),
            endpoint_silence_ms=_integer(
                value.get("endpoint_silence_ms", 650), "endpoint_silence_ms"
            ),
            hard_chunk_seconds=_integer(value.get("hard_chunk_seconds", 25), "hard_chunk_seconds"),
            overlap_seconds=_number(value.get("overlap_seconds", 2.0), "overlap_seconds"),
            rolling_context_segments=_integer(
                value.get("rolling_context_segments", 3), "rolling_context_segments"
            ),
            stable_prefix_seconds=_integer(
                value.get("stable_prefix_seconds", 45), "stable_prefix_seconds"
            ),
        )


@dataclass(frozen=True, slots=True)
class DictationStatus:
    session_id: str | None
    revision: int
    state: DictationState
    preedit: str = ""
    stable_characters: int = 0
    candidates: tuple[str, ...] = ()
    commit_text: str = ""
    message: str = ""
    latency_ms: float | None = None
    expected_accuracy_load_ms: float | None = None
    properties: dict[str, str | bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "revision": self.revision,
            "state": self.state.value,
            "preedit": self.preedit,
            "stable_characters": self.stable_characters,
            "candidates": list(self.candidates),
            "commit_text": self.commit_text,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "expected_accuracy_load_ms": self.expected_accuracy_load_ms,
            "properties": dict(self.properties),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> DictationStatus:
        candidates = value.get("candidates", ())
        properties = value.get("properties", {})
        if not isinstance(candidates, (list, tuple)) or not isinstance(properties, Mapping):
            raise ValueError("invalid dictation status collections")
        return cls(
            session_id=(str(value["session_id"]) if value.get("session_id") is not None else None),
            revision=_integer(value.get("revision"), "revision"),
            state=DictationState(str(value.get("state"))),
            preedit=str(value.get("preedit", "")),
            stable_characters=_integer(value.get("stable_characters", 0), "stable_characters"),
            candidates=tuple(str(item) for item in candidates),
            commit_text=str(value.get("commit_text", "")),
            message=str(value.get("message", "")),
            latency_ms=_optional_number(value.get("latency_ms"), "latency_ms"),
            expected_accuracy_load_ms=_optional_number(
                value.get("expected_accuracy_load_ms"), "expected_accuracy_load_ms"
            ),
            properties={str(key): _property_value(item) for key, item in properties.items()},
        )


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    return float(value)


def _optional_number(value: object, name: str) -> float | None:
    return None if value is None else _number(value, name)


def _property_value(value: object) -> str | bool:
    if not isinstance(value, (str, bool)):
        raise ValueError("dictation status properties must be string or boolean")
    return value
