"""Backend-neutral streaming/non-streaming VAD with absolute sample output."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from classscribe.timeline import SAMPLE_RATE, AudioSpan


@dataclass(frozen=True, slots=True)
class VADFrame:
    start_sample: int
    end_sample: int
    speech_probability: float

    def __post_init__(self) -> None:
        AudioSpan(self.start_sample, self.end_sample)
        if self.end_sample == self.start_sample:
            raise ValueError("VAD frames must be non-empty")
        if not 0 <= self.speech_probability <= 1:
            raise ValueError("speech probability must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class SpeechRegionResult:
    span: AudioSpan
    vad_score: float
    acoustic_class: str
    source: str


class VADBackend(Protocol):
    def frames(self, audio_path: str, *, streaming: bool) -> Iterable[VADFrame]: ...


class StreamingVADSession:
    def __init__(
        self,
        *,
        source: str,
        threshold: float,
        min_speech_samples: int,
        min_silence_samples: int,
        padding_samples: int,
        total_samples: int,
    ) -> None:
        self.source = source
        self.threshold = threshold
        self.min_speech_samples = min_speech_samples
        self.min_silence_samples = min_silence_samples
        self.padding_samples = padding_samples
        self.total_samples = total_samples
        self._active_start: int | None = None
        self._last_speech_end: int | None = None
        self._score = 0.0
        self._last_frame_end = 0
        self._regions: list[SpeechRegionResult] = []

    def push(self, frame: VADFrame) -> tuple[SpeechRegionResult, ...]:
        if frame.start_sample < self._last_frame_end or frame.end_sample > self.total_samples:
            raise ValueError("VAD frames must be ordered, absolute, and within the audio range")
        self._last_frame_end = frame.end_sample
        before = len(self._regions)
        if (
            self._active_start is not None
            and self._last_speech_end is not None
            and frame.start_sample - self._last_speech_end >= self.min_silence_samples
        ):
            self._finish_active()
        if frame.speech_probability >= self.threshold:
            if self._active_start is None:
                self._active_start = frame.start_sample
            self._last_speech_end = frame.end_sample
            self._score = max(self._score, frame.speech_probability)
        elif (
            self._active_start is not None
            and self._last_speech_end is not None
            and frame.end_sample - self._last_speech_end >= self.min_silence_samples
        ):
            self._finish_active()
        return tuple(self._regions[before:])

    def flush(self) -> tuple[SpeechRegionResult, ...]:
        before = len(self._regions)
        self._finish_active()
        return tuple(self._regions[before:])

    @property
    def regions(self) -> tuple[SpeechRegionResult, ...]:
        return tuple(self._regions)

    def _finish_active(self) -> None:
        if self._active_start is None or self._last_speech_end is None:
            return
        if self._last_speech_end - self._active_start >= self.min_speech_samples:
            start = max(0, self._active_start - self.padding_samples)
            end = min(self.total_samples, self._last_speech_end + self.padding_samples)
            candidate = SpeechRegionResult(
                AudioSpan(start, end), self._score, "speech", self.source
            )
            if self._regions and candidate.span.start_sample <= self._regions[-1].span.end_sample:
                previous = self._regions.pop()
                candidate = SpeechRegionResult(
                    AudioSpan(previous.span.start_sample, max(previous.span.end_sample, end)),
                    max(previous.vad_score, candidate.vad_score),
                    "speech",
                    self.source,
                )
            self._regions.append(candidate)
        self._active_start = None
        self._last_speech_end = None
        self._score = 0.0


class FrameVADAdapter:
    """Adapter contract shared by FireRedVAD and fallback backends."""

    def __init__(
        self,
        backend: VADBackend,
        *,
        source: str,
        threshold: float = 0.5,
        min_speech_ms: int = 200,
        min_silence_ms: int = 300,
        padding_ms: int = 120,
    ) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("VAD threshold must be within (0, 1]")
        if min_speech_ms <= 0 or min_silence_ms <= 0 or padding_ms < 0:
            raise ValueError("VAD durations must be positive, with non-negative padding")
        if 2 * padding_ms > min_silence_ms:
            raise ValueError("VAD padding may not overlap separately emitted speech regions")
        self.backend = backend
        self.source = source
        self.threshold = threshold
        self.min_speech_samples = min_speech_ms * SAMPLE_RATE // 1000
        self.min_silence_samples = min_silence_ms * SAMPLE_RATE // 1000
        self.padding_samples = padding_ms * SAMPLE_RATE // 1000

    def stream(self, total_samples: int) -> StreamingVADSession:
        return StreamingVADSession(
            source=self.source,
            threshold=self.threshold,
            min_speech_samples=self.min_speech_samples,
            min_silence_samples=self.min_silence_samples,
            padding_samples=self.padding_samples,
            total_samples=total_samples,
        )

    def analyze(
        self, audio_path: str, *, total_samples: int, streaming: bool = False
    ) -> tuple[SpeechRegionResult, ...]:
        session = self.stream(total_samples)
        for frame in self.backend.frames(audio_path, streaming=streaming):
            session.push(frame)
        session.flush()
        return session.regions


class FireRedVADAdapter(FrameVADAdapter):
    def __init__(
        self,
        backend: VADBackend,
        *,
        threshold: float = 0.5,
        min_speech_ms: int = 200,
        min_silence_ms: int = 300,
        padding_ms: int = 120,
    ) -> None:
        super().__init__(
            backend,
            source="firered_vad",
            threshold=threshold,
            min_speech_ms=min_speech_ms,
            min_silence_ms=min_silence_ms,
            padding_ms=padding_ms,
        )


class SileroVADAdapter(FrameVADAdapter):
    def __init__(
        self,
        backend: VADBackend,
        *,
        threshold: float = 0.5,
        min_speech_ms: int = 200,
        min_silence_ms: int = 300,
        padding_ms: int = 120,
    ) -> None:
        super().__init__(
            backend,
            source="silero_vad_fallback",
            threshold=threshold,
            min_speech_ms=min_speech_ms,
            min_silence_ms=min_silence_ms,
            padding_ms=padding_ms,
        )


class WebRTCVADAdapter(FrameVADAdapter):
    def __init__(
        self,
        backend: VADBackend,
        *,
        threshold: float = 0.5,
        min_speech_ms: int = 200,
        min_silence_ms: int = 300,
        padding_ms: int = 120,
    ) -> None:
        super().__init__(
            backend,
            source="webrtc_vad_fallback",
            threshold=threshold,
            min_speech_ms=min_speech_ms,
            min_silence_ms=min_silence_ms,
            padding_ms=padding_ms,
        )


def callable_backend(
    function: Callable[[str, bool], Iterable[VADFrame]],
) -> VADBackend:
    class _CallableBackend:
        def frames(self, audio_path: str, *, streaming: bool) -> Iterable[VADFrame]:
            return function(audio_path, streaming)

    return _CallableBackend()
