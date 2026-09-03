"""Token-time-first alignment with constrained dynamic programming for untimed text."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

from classscribe.consensus.models import (
    AlignedVote,
    AlignmentColumn,
    ConsensusCandidate,
    ReliabilityProfile,
)
from classscribe.quality.text import tokenize_for_language
from classscribe.timeline import AudioSpan


@dataclass(frozen=True, slots=True)
class _CandidateToken:
    text: str
    normalized: str
    span: AudioSpan
    source_index: int
    timing_source: str


def align_candidates(
    candidates: tuple[ConsensusCandidate, ...],
    canonical_span: AudioSpan,
    language: str,
    reliability: ReliabilityProfile,
) -> tuple[AlignmentColumn, ...]:
    usable = tuple(candidate for candidate in candidates if candidate.quality.valid_for_consensus)
    if not usable:
        return ()
    if any(candidate.evidence.audio_span != canonical_span for candidate in usable):
        raise ValueError("all consensus candidates must share one canonical audio interval")
    token_sets = {
        candidate.candidate_id: _candidate_tokens(candidate, language) for candidate in usable
    }
    anchor = max(
        usable,
        key=lambda candidate: (
            bool(candidate.evidence.tokens),
            len(token_sets[candidate.candidate_id]),
            candidate.quality.quality_gate_score,
            reliability.value(candidate.evidence.model_id),
            candidate.candidate_id,
        ),
    )
    anchor_tokens = token_sets[anchor.candidate_id]
    if not anchor_tokens:
        return ()
    by_key: dict[tuple[int, int], list[AlignedVote]] = defaultdict(list)
    preferred_spans: dict[tuple[int, int], list[tuple[AudioSpan, bool]]] = defaultdict(list)
    for index, token in enumerate(anchor_tokens):
        key = (2 * index + 1, 0)
        vote = _vote(anchor, token)
        by_key[key].append(vote)
        preferred_spans[key].append((token.span, token.timing_source.startswith("native")))

    anchor_normalized = tuple(token.normalized for token in anchor_tokens)
    for candidate in usable:
        if candidate.candidate_id == anchor.candidate_id:
            continue
        tokens = token_sets[candidate.candidate_id]
        operations = _dynamic_programming_alignment(
            anchor_normalized, tuple(token.normalized for token in tokens)
        )
        insertion_counts: dict[int, int] = defaultdict(int)
        anchor_position = 0
        for anchor_index, candidate_index in operations:
            if anchor_index is not None:
                anchor_position = anchor_index + 1
            if candidate_index is None:
                continue
            token = tokens[candidate_index]
            if anchor_index is None:
                gap_key = 2 * anchor_position
                insertion_counts[gap_key] += 1
                key = (gap_key, insertion_counts[gap_key])
            else:
                key = (2 * anchor_index + 1, 0)
            by_key[key].append(_vote(candidate, token))
            preferred_spans[key].append((token.span, token.timing_source.startswith("native")))

    ordered_keys = sorted(by_key)
    spans = _monotonic_column_spans(ordered_keys, preferred_spans, canonical_span)
    return tuple(
        AlignmentColumn(index, spans[key], tuple(by_key[key]))
        for index, key in enumerate(ordered_keys)
    )


def _candidate_tokens(
    candidate: ConsensusCandidate,
    language: str,
) -> tuple[_CandidateToken, ...]:
    result: list[_CandidateToken] = []
    if candidate.evidence.tokens:
        for source_index, token in enumerate(candidate.evidence.tokens):
            pieces = tokenize_for_language(token.text, language) or (token.text,)
            spans = _partition(token.span, len(pieces))
            result.extend(
                _CandidateToken(
                    piece,
                    piece.casefold(),
                    spans[piece_index],
                    source_index,
                    "native" if len(pieces) == 1 else "native_token_subdivision",
                )
                for piece_index, piece in enumerate(pieces)
            )
        return tuple(result)
    pieces = tokenize_for_language(candidate.evidence.normalized_text, language)
    spans = _partition(candidate.evidence.core_span, len(pieces))
    return tuple(
        _CandidateToken(piece, piece.casefold(), spans[index], index, "constrained_interval")
        for index, piece in enumerate(pieces)
    )


def _partition(span: AudioSpan, count: int) -> tuple[AudioSpan, ...]:
    if count == 0:
        return ()
    if span.duration_samples < count:
        raise ValueError("audio range is too short to assign positive token intervals")
    boundaries = [
        span.start_sample + span.duration_samples * index // count for index in range(count + 1)
    ]
    return tuple(AudioSpan(boundaries[index], boundaries[index + 1]) for index in range(count))


def _vote(candidate: ConsensusCandidate, token: _CandidateToken) -> AlignedVote:
    return AlignedVote(
        candidate_id=candidate.candidate_id,
        model_id=candidate.evidence.model_id,
        model_revision=candidate.evidence.model_revision,
        source_index=token.source_index,
        text=token.text,
        normalized=token.normalized,
        source_span=token.span,
        timing_source=token.timing_source,
    )


def _dynamic_programming_alignment(
    anchor: tuple[str, ...], candidate: tuple[str, ...]
) -> tuple[tuple[int | None, int | None], ...]:
    rows, columns = len(anchor) + 1, len(candidate) + 1
    cost = [[0] * columns for _ in range(rows)]
    step = [[""] * columns for _ in range(rows)]
    for row in range(1, rows):
        cost[row][0], step[row][0] = row, "delete"
    for column in range(1, columns):
        cost[0][column], step[0][column] = column, "insert"
    for row in range(1, rows):
        for column in range(1, columns):
            options = (
                (
                    cost[row - 1][column - 1] + (anchor[row - 1] != candidate[column - 1]),
                    0,
                    "pair",
                ),
                (cost[row - 1][column] + 1, 1, "delete"),
                (cost[row][column - 1] + 1, 2, "insert"),
            )
            best_cost, _priority, best_step = min(options)
            cost[row][column], step[row][column] = best_cost, best_step
    operations: list[tuple[int | None, int | None]] = []
    row, column = len(anchor), len(candidate)
    while row or column:
        operation = step[row][column]
        if operation == "pair":
            operations.append((row - 1, column - 1))
            row -= 1
            column -= 1
        elif operation == "delete":
            operations.append((row - 1, None))
            row -= 1
        else:
            operations.append((None, column - 1))
            column -= 1
    return tuple(reversed(operations))


def _monotonic_column_spans(
    keys: list[tuple[int, int]],
    preferred: dict[tuple[int, int], list[tuple[AudioSpan, bool]]],
    canonical: AudioSpan,
) -> dict[tuple[int, int], AudioSpan]:
    if canonical.duration_samples < len(keys):
        raise ValueError("canonical range cannot contain all alignment columns")
    centers: list[int] = []
    for index, key in enumerate(keys):
        native = [span for span, is_native in preferred[key] if is_native]
        available = native or [span for span, _is_native in preferred[key]]
        if available:
            desired = sorted((span.start_sample + span.end_sample) // 2 for span in available)[
                len(available) // 2
            ]
        else:
            desired = canonical.start_sample + canonical.duration_samples * (index + 1) // (
                len(keys) + 1
            )
        lower = canonical.start_sample + index
        upper = canonical.end_sample - (len(keys) - index)
        centers.append(max(lower, min(desired, upper)))
    for index in range(1, len(centers)):
        centers[index] = max(centers[index], centers[index - 1] + 1)
    boundaries = [canonical.start_sample]
    for index, (left, right) in enumerate(pairwise(centers), start=1):
        desired = (left + right) // 2
        boundaries.append(
            max(
                boundaries[-1] + 1,
                min(desired, canonical.end_sample - (len(keys) - index)),
            )
        )
    boundaries.append(canonical.end_sample)
    return {
        key: AudioSpan(boundaries[index], boundaries[index + 1]) for index, key in enumerate(keys)
    }
