"""Reliable cross-window speaker matching and overlap de-duplication."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import StrEnum

from classscribe.structure.models import StructureSegment
from classscribe.timeline import SAMPLE_RATE, AudioSpan


@dataclass(frozen=True, slots=True)
class EmbeddingObservation:
    window_ordinal: int
    speaker_local: str
    span: AudioSpan
    vector: tuple[float, ...]
    signal_quality: float
    overlap: bool = False
    support_count: int = 1

    def __post_init__(self) -> None:
        if self.window_ordinal < 0 or not self.speaker_local or not self.vector:
            raise ValueError("embedding observation requires window, speaker, and vector")
        if not all(math.isfinite(value) for value in self.vector):
            raise ValueError("embedding values must be finite")
        if not math.isfinite(self.signal_quality) or not 0 <= self.signal_quality <= 1:
            raise ValueError("signal quality must be within [0, 1]")
        if self.span.duration_samples <= 0:
            raise ValueError("embedding observation must have positive duration")
        if self.support_count < 1:
            raise ValueError("embedding support_count must be positive")


@dataclass(frozen=True, slots=True)
class OverlapSpeakerConstraint:
    window_ordinal: int
    speaker_local: str
    speaker_global: str
    evidence_score: float
    source: str = "overlap_text_and_time"

    def __post_init__(self) -> None:
        if not self.speaker_local or not self.speaker_global:
            raise ValueError("overlap constraint requires local and global speakers")
        if not 0 <= self.evidence_score <= 1:
            raise ValueError("overlap constraint evidence must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class SpeakerCandidateScore:
    speaker_global: str
    cosine_similarity: float | None
    adjacency_score: float
    overlap_constraint_score: float
    composite_score: float


class SpeakerDecision(StrEnum):
    INITIAL = "new_initial_speaker"
    EMBEDDING_MATCH = "embedding_centroid_match"
    OVERLAP_CONSTRAINT = "overlap_same_person_constraint"
    LOW_RELIABILITY_NEW = "new_speaker_low_reliability"
    ONE_TO_ONE_CONFLICT_NEW = "new_speaker_one_to_one_conflict"


@dataclass(frozen=True, slots=True)
class SpeakerMatchDiagnostic:
    window_ordinal: int
    speaker_local: str
    speaker_global: str
    decision: SpeakerDecision
    observation_count: int
    selected_cosine: float | None
    selected_score: float | None
    runner_up_margin: float | None
    candidates: tuple[SpeakerCandidateScore, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "window_ordinal": self.window_ordinal,
            "speaker_local": self.speaker_local,
            "speaker_global": self.speaker_global,
            "decision": self.decision.value,
            "observation_count": self.observation_count,
            "selected_cosine": self.selected_cosine,
            "selected_score": self.selected_score,
            "runner_up_margin": self.runner_up_margin,
            "candidates": [
                {
                    "speaker_global": item.speaker_global,
                    "cosine_similarity": item.cosine_similarity,
                    "adjacency_score": item.adjacency_score,
                    "overlap_constraint_score": item.overlap_constraint_score,
                    "composite_score": item.composite_score,
                }
                for item in self.candidates
            ],
        }


@dataclass(frozen=True, slots=True)
class StitchWindowResult:
    segments: tuple[StructureSegment, ...]
    mapping: dict[str, str]
    diagnostics: tuple[SpeakerMatchDiagnostic, ...]
    overlap_links: int
    speaker_id_switches: int

    @property
    def speaker_id_switch_rate(self) -> float:
        return self.speaker_id_switches / self.overlap_links if self.overlap_links else 0.0


@dataclass(frozen=True, slots=True)
class DedupDiagnostic:
    kept_window: int
    dropped_window: int
    kept_span: AudioSpan
    dropped_span: AudioSpan
    text_similarity: float
    overlap_samples: int
    reason: str = "higher_structural_selection_score"

    def as_dict(self) -> dict[str, object]:
        return {
            "kept_window": self.kept_window,
            "dropped_window": self.dropped_window,
            "kept_start_sample": self.kept_span.start_sample,
            "kept_end_sample": self.kept_span.end_sample,
            "dropped_start_sample": self.dropped_span.start_sample,
            "dropped_end_sample": self.dropped_span.end_sample,
            "text_similarity": self.text_similarity,
            "overlap_samples": self.overlap_samples,
            "reason": self.reason,
        }


@dataclass(slots=True)
class _GlobalSpeaker:
    speaker_global: str
    centroid: tuple[float, ...]
    embedding_weight: float
    observation_count: int
    last_end_sample: int


@dataclass(frozen=True, slots=True)
class _LocalCentroid:
    vector: tuple[float, ...] | None
    observation_count: int
    first_start_sample: int
    last_end_sample: int


class GlobalSpeakerTracker:
    """Maintain job-local anonymous identities; no cross-job voice identity is retained."""

    def __init__(
        self,
        *,
        cosine_threshold: float = 0.72,
        ambiguity_margin: float = 0.05,
        minimum_observations: int = 2,
        minimum_signal_quality: float = 0.7,
    ) -> None:
        if not -1 <= cosine_threshold <= 1 or not 0 <= ambiguity_margin <= 2:
            raise ValueError("invalid stitching thresholds")
        if minimum_observations < 2:
            raise ValueError("speaker centroids require multiple observations")
        if not 0 <= minimum_signal_quality <= 1:
            raise ValueError("minimum signal quality must be within [0, 1]")
        self.cosine_threshold = cosine_threshold
        self.ambiguity_margin = ambiguity_margin
        self.minimum_observations = minimum_observations
        self.minimum_signal_quality = minimum_signal_quality
        self._speakers: dict[str, _GlobalSpeaker] = {}
        self._next_id = 1

    @property
    def centroids(self) -> dict[str, tuple[float, ...]]:
        return {key: value.centroid for key, value in self._speakers.items()}

    def stitch_window(
        self,
        window_ordinal: int,
        segments: tuple[StructureSegment, ...],
        observations: tuple[EmbeddingObservation, ...],
        constraints: tuple[OverlapSpeakerConstraint, ...] = (),
        additional_local_labels: tuple[str, ...] = (),
    ) -> StitchWindowResult:
        if any(item.window_ordinal != window_ordinal for item in segments):
            raise ValueError("all segments must belong to the stitched window")
        if any(item.window_ordinal != window_ordinal for item in observations):
            raise ValueError("all embeddings must belong to the stitched window")
        local_labels = sorted(
            {item.speaker_local for item in segments if item.speaker_local is not None}
            | {item.speaker_local for item in observations}
            | set(additional_local_labels)
        )
        constraint_map = _strongest_constraints(window_ordinal, constraints)
        local_centroids = {
            label: _aggregate_local(
                label,
                segments,
                observations,
                minimum_quality=self.minimum_signal_quality,
            )
            for label in local_labels
        }
        scored = {
            label: self._candidate_scores(local_centroids[label], constraint_map.get(label))
            for label in local_labels
        }
        processing_order = sorted(
            local_labels,
            key=lambda label: (
                scored[label][0].composite_score if scored[label] else -1.0,
                local_centroids[label].observation_count,
                label,
            ),
            reverse=True,
        )
        used_global: set[str] = set()
        mapping: dict[str, str] = {}
        diagnostics: list[SpeakerMatchDiagnostic] = []
        for label in processing_order:
            local = local_centroids[label]
            candidates = scored[label]
            selected, margin = self._select_reliable(local, candidates, used_global)
            if selected is None:
                initial = not self._speakers
                had_reliable_conflict = any(
                    self._candidate_is_reliable(local, candidate, _candidate_margin(candidates, i))
                    and candidate.speaker_global in used_global
                    for i, candidate in enumerate(candidates)
                )
                global_id = self._new_speaker(local)
                decision = (
                    SpeakerDecision.INITIAL
                    if initial or window_ordinal == 0
                    else SpeakerDecision.ONE_TO_ONE_CONFLICT_NEW
                    if had_reliable_conflict
                    else SpeakerDecision.LOW_RELIABILITY_NEW
                )
                selected_cosine = None
                selected_score = None
                selected_margin = None
            else:
                global_id = selected.speaker_global
                constraint = constraint_map.get(label)
                decision = (
                    SpeakerDecision.OVERLAP_CONSTRAINT
                    if constraint is not None
                    and constraint.speaker_global == global_id
                    and constraint.evidence_score >= 0.9
                    else SpeakerDecision.EMBEDDING_MATCH
                )
                selected_cosine = selected.cosine_similarity
                selected_score = selected.composite_score
                selected_margin = margin
                self._update_speaker(global_id, local)
            mapping[label] = global_id
            used_global.add(global_id)
            diagnostics.append(
                SpeakerMatchDiagnostic(
                    window_ordinal=window_ordinal,
                    speaker_local=label,
                    speaker_global=global_id,
                    decision=decision,
                    observation_count=local.observation_count,
                    selected_cosine=selected_cosine,
                    selected_score=selected_score,
                    runner_up_margin=selected_margin,
                    candidates=candidates,
                )
            )
        mapped_segments = tuple(
            replace(
                item,
                speaker_global=(
                    None
                    if item.overlap or item.speaker_local is None
                    else mapping[item.speaker_local]
                ),
                provenance={
                    **item.provenance,
                    "speaker_stitching": (
                        "true_overlap_no_unique_speaker" if item.overlap else "global_centroid_v1"
                    ),
                },
            )
            for item in segments
        )
        switches = sum(
            mapping.get(item.speaker_local) != item.speaker_global
            for item in constraint_map.values()
        )
        return StitchWindowResult(
            segments=mapped_segments,
            mapping=mapping,
            diagnostics=tuple(sorted(diagnostics, key=lambda item: item.speaker_local)),
            overlap_links=len(constraint_map),
            speaker_id_switches=switches,
        )

    def _candidate_scores(
        self,
        local: _LocalCentroid,
        constraint: OverlapSpeakerConstraint | None,
    ) -> tuple[SpeakerCandidateScore, ...]:
        candidates: list[SpeakerCandidateScore] = []
        for global_id, speaker in self._speakers.items():
            cosine = (
                _cosine(local.vector, speaker.centroid)
                if local.vector is not None and speaker.centroid
                else None
            )
            gap = max(0, local.first_start_sample - speaker.last_end_sample)
            adjacency = max(0.0, 1.0 - gap / (5 * 60 * SAMPLE_RATE))
            constraint_score = (
                constraint.evidence_score
                if constraint is not None and constraint.speaker_global == global_id
                else 0.0
            )
            cosine_component = max(0.0, cosine if cosine is not None else 0.0)
            base_score = 0.82 * cosine_component + 0.10 * adjacency + 0.08 * constraint_score
            composite = max(base_score, constraint_score)
            candidates.append(
                SpeakerCandidateScore(
                    speaker_global=global_id,
                    cosine_similarity=cosine,
                    adjacency_score=round(adjacency, 6),
                    overlap_constraint_score=constraint_score,
                    composite_score=round(composite, 6),
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (item.composite_score, item.speaker_global),
                reverse=True,
            )
        )

    def _select_reliable(
        self,
        local: _LocalCentroid,
        candidates: tuple[SpeakerCandidateScore, ...],
        used_global: set[str],
    ) -> tuple[SpeakerCandidateScore | None, float | None]:
        for index, candidate in enumerate(candidates):
            margin = _candidate_margin(candidates, index)
            if candidate.speaker_global in used_global:
                continue
            if self._candidate_is_reliable(local, candidate, margin):
                return candidate, margin
        return None, None

    def _candidate_is_reliable(
        self,
        local: _LocalCentroid,
        candidate: SpeakerCandidateScore,
        margin: float,
    ) -> bool:
        if candidate.overlap_constraint_score >= 0.9:
            return True
        return (
            local.observation_count >= self.minimum_observations
            and candidate.cosine_similarity is not None
            and candidate.cosine_similarity >= self.cosine_threshold
            and margin >= self.ambiguity_margin
        )

    def _new_speaker(self, local: _LocalCentroid) -> str:
        speaker_global = f"SPEAKER_{self._next_id:02d}"
        self._next_id += 1
        self._speakers[speaker_global] = _GlobalSpeaker(
            speaker_global=speaker_global,
            centroid=local.vector or (),
            embedding_weight=float(local.observation_count),
            observation_count=local.observation_count,
            last_end_sample=local.last_end_sample,
        )
        return speaker_global

    def _update_speaker(self, global_id: str, local: _LocalCentroid) -> None:
        speaker = self._speakers[global_id]
        speaker.last_end_sample = max(speaker.last_end_sample, local.last_end_sample)
        if local.vector is None or local.observation_count == 0:
            return
        if not speaker.centroid:
            speaker.centroid = local.vector
            speaker.embedding_weight = float(local.observation_count)
            speaker.observation_count = local.observation_count
            return
        if len(speaker.centroid) != len(local.vector):
            raise ValueError("speaker embedding dimensions changed across windows")
        old_weight = speaker.embedding_weight
        new_weight = float(local.observation_count)
        speaker.centroid = _normalize(
            tuple(
                (old * old_weight + new * new_weight) / (old_weight + new_weight)
                for old, new in zip(speaker.centroid, local.vector, strict=True)
            )
        )
        speaker.embedding_weight += new_weight
        speaker.observation_count += local.observation_count


def infer_overlap_constraints(
    previous: tuple[StructureSegment, ...],
    current: tuple[StructureSegment, ...],
) -> tuple[OverlapSpeakerConstraint, ...]:
    """Infer same-person links only from time-aligned, near-identical overlap text."""

    best: dict[tuple[int, str], OverlapSpeakerConstraint] = {}
    for left in previous:
        if left.speaker_global is None or not left.text.strip():
            continue
        for right in current:
            if (
                right.speaker_local is None
                or not right.text.strip()
                or not left.span.overlaps(right.span)
            ):
                continue
            similarity = _text_similarity(left.text, right.text)
            intersection = _intersection_samples(left.span, right.span)
            temporal = intersection / min(left.span.duration_samples, right.span.duration_samples)
            evidence = 0.75 * similarity + 0.25 * temporal
            if similarity < 0.82 or temporal < 0.15:
                continue
            constraint = OverlapSpeakerConstraint(
                window_ordinal=right.window_ordinal,
                speaker_local=right.speaker_local,
                speaker_global=left.speaker_global,
                evidence_score=min(1.0, round(evidence, 6)),
            )
            key = (right.window_ordinal, right.speaker_local)
            if key not in best or constraint.evidence_score > best[key].evidence_score:
                best[key] = constraint
    return tuple(sorted(best.values(), key=lambda item: item.speaker_local))


def deduplicate_structure_segments(
    segments: tuple[StructureSegment, ...],
) -> tuple[tuple[StructureSegment, ...], tuple[DedupDiagnostic, ...]]:
    """Drop cross-window overlap duplicates by score; never concatenate model strings."""

    kept: list[StructureSegment] = []
    diagnostics: list[DedupDiagnostic] = []
    for candidate in sorted(
        segments,
        key=lambda item: (item.span.start_sample, item.span.end_sample, item.window_ordinal),
    ):
        duplicate_index: int | None = None
        duplicate_similarity = 0.0
        duplicate_overlap = 0
        for index, existing in enumerate(kept):
            if existing.window_ordinal == candidate.window_ordinal or not existing.span.overlaps(
                candidate.span
            ):
                continue
            similarity = _text_similarity(existing.text, candidate.text)
            overlap = _intersection_samples(existing.span, candidate.span)
            temporal = overlap / min(
                existing.span.duration_samples, candidate.span.duration_samples
            )
            if (similarity >= 0.88 and temporal >= 0.15) or (similarity == 1.0 and overlap > 0):
                duplicate_index = index
                duplicate_similarity = similarity
                duplicate_overlap = overlap
                break
        if duplicate_index is None:
            kept.append(candidate)
            continue
        existing = kept[duplicate_index]
        existing_key = (
            existing.selection_score,
            len(existing.text.strip()),
            -existing.window_ordinal,
        )
        candidate_key = (
            candidate.selection_score,
            len(candidate.text.strip()),
            -candidate.window_ordinal,
        )
        if candidate_key > existing_key:
            winner, loser = candidate, existing
            kept[duplicate_index] = candidate
        else:
            winner, loser = existing, candidate
        diagnostics.append(
            DedupDiagnostic(
                kept_window=winner.window_ordinal,
                dropped_window=loser.window_ordinal,
                kept_span=winner.span,
                dropped_span=loser.span,
                text_similarity=round(duplicate_similarity, 6),
                overlap_samples=duplicate_overlap,
            )
        )
    ordered = tuple(sorted(kept, key=lambda item: (item.span.start_sample, item.span.end_sample)))
    return ordered, tuple(diagnostics)


def _aggregate_local(
    label: str,
    segments: tuple[StructureSegment, ...],
    observations: tuple[EmbeddingObservation, ...],
    *,
    minimum_quality: float,
) -> _LocalCentroid:
    matching_segments = tuple(item for item in segments if item.speaker_local == label)
    matching_observations = tuple(
        item
        for item in observations
        if item.speaker_local == label
        and not item.overlap
        and item.signal_quality >= minimum_quality
    )
    all_spans = tuple(item.span for item in matching_segments) or tuple(
        item.span for item in matching_observations
    )
    if not all_spans:
        raise ValueError(f"speaker {label} has no timeline evidence")
    vector: tuple[float, ...] | None = None
    if matching_observations:
        dimensions = {len(item.vector) for item in matching_observations}
        if len(dimensions) != 1:
            raise ValueError("speaker embedding dimensions differ within a window")
        total_weight = sum(
            item.signal_quality * item.support_count for item in matching_observations
        )
        vector = _normalize(
            tuple(
                sum(
                    item.vector[index] * item.signal_quality * item.support_count
                    for item in matching_observations
                )
                / total_weight
                for index in range(len(matching_observations[0].vector))
            )
        )
    return _LocalCentroid(
        vector=vector,
        observation_count=sum(item.support_count for item in matching_observations),
        first_start_sample=min(item.start_sample for item in all_spans),
        last_end_sample=max(item.end_sample for item in all_spans),
    )


def _strongest_constraints(
    window_ordinal: int,
    constraints: tuple[OverlapSpeakerConstraint, ...],
) -> dict[str, OverlapSpeakerConstraint]:
    strongest: dict[str, OverlapSpeakerConstraint] = {}
    for constraint in constraints:
        if constraint.window_ordinal != window_ordinal:
            raise ValueError("overlap constraint belongs to a different window")
        current = strongest.get(constraint.speaker_local)
        if current is None or constraint.evidence_score > current.evidence_score:
            strongest[constraint.speaker_local] = constraint
    return strongest


def _candidate_margin(candidates: tuple[SpeakerCandidateScore, ...], index: int) -> float:
    selected = candidates[index]
    alternatives = [
        item.composite_score
        for candidate_index, item in enumerate(candidates)
        if candidate_index != index
    ]
    return selected.composite_score - max(alternatives) if alternatives else 2.0


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("speaker embedding dimensions differ across windows")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _normalize(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    return tuple(value / norm for value in vector) if norm else vector


def _normalized_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)


def _text_similarity(left: str, right: str) -> float:
    normalized_left = _normalized_text(left)
    normalized_right = _normalized_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right, autojunk=False).ratio()


def _intersection_samples(left: AudioSpan, right: AudioSpan) -> int:
    return max(
        0, min(left.end_sample, right.end_sample) - max(left.start_sample, right.start_sample)
    )
