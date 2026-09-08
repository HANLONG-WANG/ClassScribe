"""Sample-based long-dictation chunking, stable prefixes, and overlap deduplication."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from classscribe_protocol import DictationLanguage

SAMPLE_RATE = 16_000


@dataclass(frozen=True, slots=True)
class DictationChunk:
    sequence: int
    context_start_sample: int
    core_start_sample: int
    end_sample: int
    reason: str

    def __post_init__(self) -> None:
        if not 0 <= self.context_start_sample <= self.core_start_sample < self.end_sample:
            raise ValueError("dictation chunk samples are invalid")
        if self.reason not in {"semantic_endpoint", "hard_limit", "release"}:
            raise ValueError("dictation chunk reason is invalid")


@dataclass(frozen=True, slots=True)
class TimedToken:
    text: str
    start_sample: int
    end_sample: int
    stable: bool = True

    def __post_init__(self) -> None:
        if not self.text or not 0 <= self.start_sample < self.end_sample:
            raise ValueError("timed dictation token is invalid")


class ChunkPlanner:
    """Create contiguous cores plus context overlap; cores never duplicate or lose samples."""

    def __init__(
        self,
        *,
        endpoint_silence_ms: int = 650,
        hard_seconds: int = 25,
        overlap_seconds: float = 2.0,
        minimum_semantic_seconds: float = 3.0,
    ) -> None:
        if not 20 <= hard_seconds <= 30 or not 1.5 <= overlap_seconds <= 2.5:
            raise ValueError("unsafe long-dictation chunk settings")
        self.endpoint_samples = endpoint_silence_ms * SAMPLE_RATE // 1000
        self.hard_samples = hard_seconds * SAMPLE_RATE
        self.overlap_samples = round(overlap_seconds * SAMPLE_RATE)
        self.minimum_semantic_samples = round(minimum_semantic_seconds * SAMPLE_RATE)
        self.absolute_sample = 0
        self.core_start = 0
        self.last_voice_end = 0
        self.sequence = 0

    def push(self, frame_samples: int, *, voiced: bool) -> DictationChunk | None:
        if frame_samples < 320 or frame_samples > 640:
            raise ValueError("PipeWire frames must contain 20..40 ms at 16 kHz")
        self.absolute_sample += frame_samples
        if voiced:
            self.last_voice_end = self.absolute_sample
        core_duration = self.absolute_sample - self.core_start
        silence = self.absolute_sample - self.last_voice_end
        if core_duration >= self.hard_samples:
            return self._cut("hard_limit")
        if (
            core_duration >= self.minimum_semantic_samples
            and self.last_voice_end > self.core_start
            and silence >= self.endpoint_samples
        ):
            return self._cut("semantic_endpoint")
        return None

    def release(self) -> DictationChunk | None:
        if self.absolute_sample <= self.core_start:
            return None
        return self._cut("release")

    def _cut(self, reason: str) -> DictationChunk:
        chunk = DictationChunk(
            self.sequence,
            max(0, self.core_start - self.overlap_samples),
            self.core_start,
            self.absolute_sample,
            reason,
        )
        self.sequence += 1
        self.core_start = self.absolute_sample
        return chunk


def merge_timed_tokens(
    accepted: tuple[TimedToken, ...], incoming: tuple[TimedToken, ...]
) -> tuple[TimedToken, ...]:
    """Merge by absolute time and token identity; never delete outside an overlap."""

    _validate_order(accepted)
    _validate_order(incoming)
    if not accepted:
        return incoming
    result = list(accepted)
    accepted_end = accepted[-1].end_sample
    for token in incoming:
        overlapping = [
            item
            for item in result
            if item.start_sample < token.end_sample and token.start_sample < item.end_sample
        ]
        if any(item.text.casefold() == token.text.casefold() for item in overlapping):
            continue
        if token.end_sample <= accepted_end and overlapping:
            continue
        result.append(token)
    return tuple(sorted(result, key=lambda item: (item.start_sample, item.end_sample, item.text)))


class StablePrefix:
    def __init__(self, *, stable_after_seconds: int = 45) -> None:
        self.stable_after_samples = stable_after_seconds * SAMPLE_RATE
        self.committed: tuple[TimedToken, ...] = ()
        self.pending: tuple[TimedToken, ...] = ()

    def update(
        self, candidates: tuple[TimedToken, ...], *, absolute_sample: int
    ) -> tuple[TimedToken, ...]:
        combined = merge_timed_tokens(self.committed, _refresh_pending(self.pending, candidates))
        boundary = max(0, absolute_sample - self.stable_after_samples)
        prefix: list[TimedToken] = []
        for item in combined[len(self.committed) :]:
            if not item.stable or item.end_sample > boundary:
                break
            prefix.append(item)
        newly_stable = tuple(prefix)
        self.committed = merge_timed_tokens(self.committed, newly_stable)
        # An old token that the model still marks unstable must stay pending;
        # crossing the time horizon alone is never permission to discard it.
        self.pending = tuple(item for item in combined if item not in self.committed)
        return newly_stable

    def finalize(self, candidates: tuple[TimedToken, ...]) -> tuple[TimedToken, ...]:
        combined = merge_timed_tokens(self.committed, _refresh_pending(self.pending, candidates))
        final = tuple(item for item in combined if item not in self.committed)
        self.committed = combined
        self.pending = ()
        return final


class RollingContext:
    def __init__(self, maximum_segments: int = 3) -> None:
        if not 1 <= maximum_segments <= 5:
            raise ValueError("rolling context size must be 1..5")
        self._segments: deque[str] = deque(maxlen=maximum_segments)

    def add(self, text: str) -> None:
        if text.strip():
            self._segments.append(text.strip())

    def values(self) -> tuple[str, ...]:
        return tuple(self._segments)


class StableLanguageRouter:
    """Fuse streaming/final LID only at stable boundaries with two-window hysteresis."""

    def __init__(self, configured: DictationLanguage, *, threshold: float = 0.8) -> None:
        self.configured = configured
        self.threshold = threshold
        self.current: DictationLanguage | None = (
            configured if configured is not DictationLanguage.AUTO_MIXED else None
        )
        self._pending: DictationLanguage | None = None
        self._count = 0

    def observe(
        self,
        primary: Mapping[str, float],
        firered: Mapping[str, float] | None = None,
        *,
        stable_boundary: bool,
        english_terminology: bool = False,
    ) -> DictationLanguage | None:
        if self.configured is not DictationLanguage.AUTO_MIXED:
            return self.configured
        if not stable_boundary:
            return self.current
        combined: dict[str, float] = {}
        for language in ("zh", "ja", "en"):
            # A missing detector is absence of evidence, not a zero-confidence
            # vote. In particular, the primary ASR may omit LID while
            # FireRedLID has a strong result.
            values = []
            if language in primary:
                values.append(float(primary[language]))
            if firered is not None and language in firered:
                values.append(float(firered[language]))
            combined[language] = sum(values) / len(values) if values else 0.0
        language, confidence = max(combined.items(), key=lambda item: item[1])
        proposed = DictationLanguage(language)
        if confidence < self.threshold:
            self._pending = None
            self._count = 0
            return self.current
        if (
            proposed is DictationLanguage.ENGLISH
            and self.current is DictationLanguage.JAPANESE
            and english_terminology
        ):
            return self.current
        if proposed is self._pending:
            self._count += 1
        else:
            self._pending = proposed
            self._count = 1
        if self._count >= 2:
            self.current = proposed
            self._pending = None
            self._count = 0
        return self.current


def _refresh_pending(
    previous: tuple[TimedToken, ...], candidates: tuple[TimedToken, ...]
) -> tuple[TimedToken, ...]:
    """Let the newest hypothesis revise only the still-uncommitted overlap."""

    _validate_order(previous)
    _validate_order(candidates)
    if not candidates:
        return previous
    retained = tuple(
        old
        for old in previous
        if not any(
            old.start_sample < new.end_sample and new.start_sample < old.end_sample
            for new in candidates
        )
    )
    return tuple(
        sorted((*retained, *candidates), key=lambda item: (item.start_sample, item.end_sample))
    )


def _validate_order(tokens: tuple[TimedToken, ...]) -> None:
    if [item.start_sample for item in tokens] != sorted(item.start_sample for item in tokens):
        raise ValueError("timed tokens must be monotonic")
