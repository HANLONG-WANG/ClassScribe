"""Manual-language bypass and hysteretic FireRedLID routing on absolute samples."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from classscribe.contracts import LanguageMode
from classscribe.timeline import SAMPLE_RATE, AudioSpan

ROUTABLE_LANGUAGES = (LanguageMode.CHINESE, LanguageMode.JAPANESE, LanguageMode.ENGLISH)
LANGUAGE_PROMPTS = {
    LanguageMode.CHINESE: "zh",
    LanguageMode.JAPANESE: "Japanese",
    LanguageMode.ENGLISH: "English",
}


@dataclass(frozen=True, slots=True)
class LIDObservation:
    span: AudioSpan
    probabilities: Mapping[LanguageMode, float]

    def __post_init__(self) -> None:
        if set(self.probabilities) != set(ROUTABLE_LANGUAGES):
            raise ValueError("LID must report raw zh/ja/en probabilities")
        if any(not 0 <= value <= 1 for value in self.probabilities.values()):
            raise ValueError("LID probabilities must be within [0, 1]")
        if sum(self.probabilities.values()) > 1.000001:
            raise ValueError("LID probabilities may not sum above one")


@dataclass(frozen=True, slots=True)
class LanguageRoutingSpan:
    span: AudioSpan
    language: LanguageMode
    confidence_raw: float
    decision: Mapping[str, Any]


class LIDBackend(Protocol):
    def probabilities(self, audio_path: str, span: AudioSpan) -> Mapping[LanguageMode, float]: ...


def sliding_lid_windows(
    total_samples: int,
    *,
    window_samples: int = 5 * SAMPLE_RATE,
    step_samples: int = 5 * SAMPLE_RATE // 2,
) -> tuple[AudioSpan, ...]:
    if total_samples < 0 or not 3 * SAMPLE_RATE <= window_samples <= 8 * SAMPLE_RATE:
        raise ValueError("LID windows must be 3 to 8 seconds")
    if step_samples <= 0 or step_samples > window_samples:
        raise ValueError("invalid LID step")
    if total_samples == 0:
        return ()
    if total_samples <= window_samples:
        return (AudioSpan(0, total_samples),)
    windows: list[AudioSpan] = []
    start = 0
    while start + window_samples <= total_samples:
        windows.append(AudioSpan(start, start + window_samples))
        start += step_samples
    if windows[-1].end_sample < total_samples:
        final = AudioSpan(total_samples - window_samples, total_samples)
        if final != windows[-1]:
            windows.append(final)
    return tuple(windows)


class FireRedLIDAdapter:
    def __init__(self, backend: LIDBackend) -> None:
        self.backend = backend

    def observe(self, audio_path: str, total_samples: int) -> tuple[LIDObservation, ...]:
        return tuple(
            LIDObservation(span, self.backend.probabilities(audio_path, span))
            for span in sliding_lid_windows(total_samples)
        )


class LanguageRouter:
    def __init__(self, *, threshold: float = 0.80, consecutive_windows: int = 2) -> None:
        if not 0 < threshold <= 1 or consecutive_windows < 2:
            raise ValueError("language routing requires threshold and multi-window hysteresis")
        self.threshold = threshold
        self.consecutive_windows = consecutive_windows

    def route(
        self,
        mode: LanguageMode,
        total_samples: int,
        *,
        observations: Sequence[LIDObservation] = (),
        silence_points: Sequence[int] = (),
    ) -> tuple[LanguageRoutingSpan, ...]:
        if total_samples < 0:
            raise ValueError("total samples must be non-negative")
        if total_samples == 0:
            return ()
        if mode in LANGUAGE_PROMPTS:
            return (
                LanguageRoutingSpan(
                    AudioSpan(0, total_samples),
                    mode,
                    1.0,
                    {
                        "mode": "manual",
                        "global_lid_bypassed": True,
                        "model_language_prompt": LANGUAGE_PROMPTS[mode],
                        "observations": [],
                    },
                ),
            )
        if mode is not LanguageMode.AUTO_MIXED:
            raise ValueError(f"unsupported language mode: {mode}")
        ordered = tuple(sorted(observations, key=lambda item: item.span.start_sample))
        self._validate_observations(ordered, total_samples)
        if not ordered:
            raise ValueError("automatic/mixed mode requires LID observations")
        current, stable_index = self._initial_language(ordered)
        switches: list[tuple[int, LanguageMode, LanguageMode, int]] = []
        pending: LanguageMode | None = None
        pending_count = 0
        pending_start = 0
        current_start = 0
        for index, observation in enumerate(ordered[stable_index + 1 :], stable_index + 1):
            candidate = max(ROUTABLE_LANGUAGES, key=observation.probabilities.__getitem__)
            probability = observation.probabilities[candidate]
            if candidate is current or probability < self.threshold:
                pending = None
                pending_count = 0
                continue
            if candidate is pending:
                pending_count += 1
            else:
                pending = candidate
                pending_count = 1
                pending_start = observation.span.start_sample
            if pending_count < self.consecutive_windows:
                continue
            boundary = self._recent_silence(
                silence_points,
                after=current_start,
                before=observation.span.start_sample,
                fallback=pending_start,
            )
            if boundary > current_start:
                switches.append((boundary, current, candidate, index))
                current_start = boundary
                current = candidate
            pending = None
            pending_count = 0
        initial = self._initial_language(ordered)[0]
        return self._build_spans(total_samples, ordered, switches, initial)

    def _initial_language(self, observations: Sequence[LIDObservation]) -> tuple[LanguageMode, int]:
        for index in range(self.consecutive_windows - 1, len(observations)):
            group = observations[index - self.consecutive_windows + 1 : index + 1]
            candidates = [
                max(ROUTABLE_LANGUAGES, key=item.probabilities.__getitem__) for item in group
            ]
            candidate = candidates[0]
            if all(
                item is candidate and observation.probabilities[candidate] >= self.threshold
                for item, observation in zip(candidates, group, strict=True)
            ):
                return candidate, index
        totals: defaultdict[LanguageMode, float] = defaultdict(float)
        for observation in observations:
            for language, probability in observation.probabilities.items():
                totals[language] += probability
        return max(ROUTABLE_LANGUAGES, key=totals.__getitem__), len(observations) - 1

    @staticmethod
    def _recent_silence(
        silence_points: Sequence[int], *, after: int, before: int, fallback: int
    ) -> int:
        eligible = [point for point in silence_points if after < point <= before]
        return max(eligible) if eligible else fallback

    def _build_spans(
        self,
        total_samples: int,
        observations: Sequence[LIDObservation],
        switches: Sequence[tuple[int, LanguageMode, LanguageMode, int]],
        initial: LanguageMode,
    ) -> tuple[LanguageRoutingSpan, ...]:
        boundaries = [0, *(item[0] for item in switches), total_samples]
        languages = [initial, *(item[2] for item in switches)]
        results: list[LanguageRoutingSpan] = []
        for index, language in enumerate(languages):
            start, end = boundaries[index], boundaries[index + 1]
            if end <= start:
                continue
            relevant = [
                item
                for item in observations
                if item.span.start_sample < end and item.span.end_sample > start
            ]
            confidence = (
                sum(item.probabilities[language] for item in relevant) / len(relevant)
                if relevant
                else 0.0
            )
            transition = switches[index - 1] if index else None
            results.append(
                LanguageRoutingSpan(
                    AudioSpan(start, end),
                    language,
                    confidence,
                    {
                        "mode": "auto_mixed",
                        "source": "firered_lid",
                        "window_samples": 5 * SAMPLE_RATE,
                        "step_samples": 5 * SAMPLE_RATE // 2,
                        "threshold": self.threshold,
                        "consecutive_windows": self.consecutive_windows,
                        "transition_from": transition[1].value if transition else None,
                        "routing_hint": self._routing_hint(transition),
                        "observations": [
                            {
                                "start_sample": item.span.start_sample,
                                "end_sample": item.span.end_sample,
                                "probabilities": {
                                    key.value: value for key, value in item.probabilities.items()
                                },
                            }
                            for item in relevant
                        ],
                    },
                )
            )
        return tuple(results)

    @staticmethod
    def _routing_hint(
        transition: tuple[int, LanguageMode, LanguageMode, int] | None,
    ) -> str:
        if transition is None:
            return "language_specialized"
        pair = {transition[1], transition[2]}
        if pair == {LanguageMode.CHINESE, LanguageMode.ENGLISH}:
            return "prefer_unified_zh_en_model"
        if pair == {LanguageMode.JAPANESE, LanguageMode.ENGLISH}:
            return "prefer_unified_ja_en_model"
        return "language_specialized"

    @staticmethod
    def _validate_observations(observations: Sequence[LIDObservation], total_samples: int) -> None:
        previous_start = -1
        for item in observations:
            if item.span.start_sample < previous_start or item.span.end_sample > total_samples:
                raise ValueError("LID observations must be ordered and within the audio range")
            previous_start = item.span.start_sample
