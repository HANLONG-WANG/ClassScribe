"""Distinct structure-window and transcript-chunk policies on absolute samples."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from classscribe.timeline import SAMPLE_RATE, AudioSpan

DEFAULT_STRUCTURE_SAMPLES = 12 * 60 * SAMPLE_RATE
MIN_STRUCTURE_SAMPLES = 4 * 60 * SAMPLE_RATE
STRUCTURE_OVERLAP_SAMPLES = 4 * SAMPLE_RATE
ADAPTIVE_STRUCTURE_MINUTES = (20, 30, 60, 90)


@dataclass(frozen=True, slots=True)
class StructureWindow:
    ordinal: int
    span: AudioSpan
    overlap_before_samples: int
    overlap_after_samples: int
    policy: str


class BoundaryKind(StrEnum):
    SPEAKER_CHANGE = "speaker_change"
    NATURAL_PAUSE = "natural_pause_300_800ms"
    LONG_PAUSE = "long_pause"
    SENTENCE_END = "sentence_end_prosody_or_punctuation"
    LANGUAGE_SWITCH = "language_switch"


BOUNDARY_PRIORITY = {
    BoundaryKind.SPEAKER_CHANGE: 0,
    BoundaryKind.NATURAL_PAUSE: 1,
    BoundaryKind.LONG_PAUSE: 1,
    BoundaryKind.SENTENCE_END: 2,
    BoundaryKind.LANGUAGE_SWITCH: 3,
}


@dataclass(frozen=True, slots=True)
class BoundaryCue:
    sample: int
    kind: BoundaryKind
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.sample < 0 or not 0 <= self.confidence <= 1:
            raise ValueError("invalid boundary cue")


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    """ASR text unit; core span is unique, audio span may include hard-cut context."""

    ordinal: int
    core_span: AudioSpan
    audio_span: AudioSpan
    boundary_reason: str
    hard_split: bool
    overlap_before_samples: int
    overlap_after_samples: int


@dataclass(frozen=True, slots=True)
class TimedToken:
    text: str
    span: AudioSpan


@dataclass(frozen=True, slots=True)
class BoundaryCandidate:
    tokens: tuple[TimedToken, ...]
    quality_score: float


@dataclass(frozen=True, slots=True)
class BoundaryDedupPlan:
    matched_tokens: int
    drop_left_suffix: int
    drop_right_prefix: int
    preferred: str
    method: str = "token_text_and_absolute_time_alignment"


@dataclass(frozen=True, slots=True)
class SpeakerEmbedding:
    label: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if (
            not self.label
            or not self.vector
            or not all(math.isfinite(value) for value in self.vector)
        ):
            raise ValueError("speaker embedding must have a label and finite non-empty vector")


@dataclass(frozen=True, slots=True)
class SpeakerEmbeddingMatch:
    previous_global_label: str
    next_local_label: str
    cosine_similarity: float


def choose_structure_window_samples(measured_safe_samples: int | None = None) -> int:
    if measured_safe_samples is None:
        return DEFAULT_STRUCTURE_SAMPLES
    if measured_safe_samples < MIN_STRUCTURE_SAMPLES:
        raise ValueError("measured structure capacity is below the four-minute safety minimum")
    candidates = (4, 12, *ADAPTIVE_STRUCTURE_MINUTES)
    return max(
        minutes * 60 * SAMPLE_RATE
        for minutes in candidates
        if minutes * 60 * SAMPLE_RATE <= measured_safe_samples
    )


def make_structure_windows(
    total_samples: int,
    *,
    window_samples: int = DEFAULT_STRUCTURE_SAMPLES,
    overlap_samples: int = STRUCTURE_OVERLAP_SAMPLES,
    minimum_samples: int = MIN_STRUCTURE_SAMPLES,
) -> tuple[StructureWindow, ...]:
    if total_samples < 0:
        raise ValueError("total samples must be non-negative")
    if (
        window_samples < minimum_samples
        or overlap_samples <= 0
        or overlap_samples >= window_samples
    ):
        raise ValueError("invalid structure-window policy")
    if total_samples == 0:
        return ()
    spans: list[AudioSpan] = []
    start = 0
    while start < total_samples:
        end = min(total_samples, start + window_samples)
        if end == total_samples and end - start < minimum_samples and spans:
            start = max(spans[-1].start_sample + 1, total_samples - minimum_samples)
        span = AudioSpan(start, end)
        if spans and span.start_sample >= spans[-1].end_sample:
            raise ValueError("structure windows must overlap")
        spans.append(span)
        if end == total_samples:
            break
        start = end - overlap_samples
    return tuple(
        StructureWindow(
            ordinal=index,
            span=span,
            overlap_before_samples=(
                spans[index - 1].end_sample - span.start_sample if index else 0
            ),
            overlap_after_samples=(
                span.end_sample - spans[index + 1].start_sample if index + 1 < len(spans) else 0
            ),
            policy="adaptive_absolute_structure_window",
        )
        for index, span in enumerate(spans)
    )


def make_transcript_chunks(
    speech_extent: AudioSpan,
    cues: tuple[BoundaryCue, ...],
    *,
    target_samples: int = 18 * SAMPLE_RATE,
    minimum_samples: int = 8 * SAMPLE_RATE,
    hard_max_samples: int = 30 * SAMPLE_RATE,
    hard_overlap_samples: int = SAMPLE_RATE,
) -> tuple[TranscriptChunk, ...]:
    if not minimum_samples <= target_samples <= hard_max_samples:
        raise ValueError("text chunk target must lie between min and hard max")
    if not int(0.8 * SAMPLE_RATE) <= hard_overlap_samples <= int(1.5 * SAMPLE_RATE):
        raise ValueError("hard-cut context must be 0.8 to 1.5 seconds on each side")
    ordered_cues = sorted(
        (cue for cue in cues if speech_extent.start_sample < cue.sample < speech_extent.end_sample),
        key=lambda cue: cue.sample,
    )
    cores: list[tuple[AudioSpan, str, bool]] = []
    hard_boundaries: set[int] = set()
    start = speech_extent.start_sample
    while start < speech_extent.end_sample:
        remaining = speech_extent.end_sample - start
        if remaining <= hard_max_samples:
            cores.append((AudioSpan(start, speech_extent.end_sample), "end_of_speech", False))
            break
        lower = start + minimum_samples
        upper = min(start + hard_max_samples, speech_extent.end_sample)
        candidates = [cue for cue in ordered_cues if lower <= cue.sample <= upper]
        if candidates:
            cue = min(
                candidates,
                key=lambda item: (
                    BOUNDARY_PRIORITY[item.kind],
                    abs(item.sample - (start + target_samples)),
                    -item.confidence,
                ),
            )
            end = cue.sample
            reason = cue.kind.value
            hard = False
        else:
            end = upper
            reason = "hard_max_with_audio_context"
            hard = True
            hard_boundaries.add(end)
        cores.append((AudioSpan(start, end), reason, hard))
        start = end

    if len(cores) > 1 and cores[-1][0].duration_samples < minimum_samples:
        previous, final = cores[-2], cores[-1]
        merged = AudioSpan(previous[0].start_sample, final[0].end_sample)
        if merged.duration_samples <= hard_max_samples:
            hard_boundaries.discard(previous[0].end_sample)
            cores[-2:] = [(merged, final[1], previous[2] or final[2])]

    chunks: list[TranscriptChunk] = []
    for ordinal, (core, reason, _hard) in enumerate(cores):
        before = hard_overlap_samples if core.start_sample in hard_boundaries else 0
        after = hard_overlap_samples if core.end_sample in hard_boundaries else 0
        audio = AudioSpan(
            max(speech_extent.start_sample, core.start_sample - before),
            min(speech_extent.end_sample, core.end_sample + after),
        )
        chunks.append(
            TranscriptChunk(ordinal, core, audio, reason, bool(before or after), before, after)
        )
    return tuple(chunks)


def _normalize_token(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)


def plan_boundary_dedup(
    left: BoundaryCandidate,
    right: BoundaryCandidate,
    *,
    time_tolerance_samples: int = SAMPLE_RATE // 2,
) -> BoundaryDedupPlan:
    """Align suffix/prefix token text and absolute time; never delete fixed characters."""

    maximum = min(len(left.tokens), len(right.tokens))
    matched = 0
    for length in range(maximum, 0, -1):
        left_tokens = left.tokens[-length:]
        right_tokens = right.tokens[:length]
        if [_normalize_token(token.text) for token in left_tokens] != [
            _normalize_token(token.text) for token in right_tokens
        ]:
            continue
        times_align = all(
            first.span.overlaps(second.span)
            or abs(first.span.start_sample - second.span.start_sample) <= time_tolerance_samples
            for first, second in zip(left_tokens, right_tokens, strict=True)
        )
        if times_align:
            matched = length
            break
    if matched == 0:
        return BoundaryDedupPlan(0, 0, 0, "neither")
    if left.quality_score >= right.quality_score:
        return BoundaryDedupPlan(matched, 0, matched, "left")
    return BoundaryDedupPlan(matched, matched, 0, "right")


def match_speaker_embeddings(
    previous: tuple[SpeakerEmbedding, ...],
    following: tuple[SpeakerEmbedding, ...],
    *,
    threshold: float = 0.72,
) -> tuple[SpeakerEmbeddingMatch, ...]:
    """Greedy one-to-one overlap match used to continue global speaker labels."""

    if not -1 <= threshold <= 1:
        raise ValueError("cosine threshold must be within [-1, 1]")
    candidates: list[tuple[float, str, str]] = []
    for left in previous:
        for right in following:
            if len(left.vector) != len(right.vector):
                raise ValueError("speaker embedding dimensions must match")
            left_norm = math.sqrt(sum(value * value for value in left.vector))
            right_norm = math.sqrt(sum(value * value for value in right.vector))
            if left_norm == 0 or right_norm == 0:
                continue
            similarity = sum(
                first * second for first, second in zip(left.vector, right.vector, strict=True)
            ) / (left_norm * right_norm)
            if similarity >= threshold:
                candidates.append((similarity, left.label, right.label))
    used_previous: set[str] = set()
    used_following: set[str] = set()
    matches: list[SpeakerEmbeddingMatch] = []
    for similarity, previous_label, following_label in sorted(candidates, reverse=True):
        if previous_label in used_previous or following_label in used_following:
            continue
        used_previous.add(previous_label)
        used_following.add(following_label)
        matches.append(SpeakerEmbeddingMatch(previous_label, following_label, similarity))
    return tuple(sorted(matches, key=lambda item: item.next_local_label))
