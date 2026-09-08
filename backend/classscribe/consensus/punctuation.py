"""Recover display punctuation from the candidates that supplied the winning words."""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Any

from classscribe.consensus.models import ConsensusCandidate, FinalToken
from classscribe.punctuation.boundaries import apply_boundary_marks
from classscribe.punctuation.guard import (
    character_invariant,
    is_punctuation_or_spacing,
    require_character_invariance,
)


def _surface(text: str) -> tuple[str, dict[int, int], dict[int, str]]:
    characters: list[str] = []
    offsets = {0: 0}
    marks: dict[int, str] = {}
    ordinal = 0
    for char in text:
        if is_punctuation_or_spacing(char):
            if not char.isspace():
                position = len(characters)
                marks[position] = marks.get(position, "") + char
            continue
        characters.extend(
            item
            for item in unicodedata.normalize("NFKC", char)
            if not is_punctuation_or_spacing(item)
        )
        ordinal += 1
        offsets[len(characters)] = ordinal
    return "".join(characters), offsets, marks


def restore_native_punctuation(
    text: str,
    tokens: tuple[FinalToken, ...],
    candidates: tuple[ConsensusCandidate, ...],
) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Project only boundaries inside matching text owned by the same selected candidate.

    Normalization is used for matching only. Ambiguous edits and candidate switches
    never receive inferred punctuation; the winning character sequence stays intact.
    """
    target, offsets, _ = _surface(text)
    owners = [
        token.provenance.get("selected_candidate_id")
        for token in tokens
        for _ in _surface(token.text)[0]
    ]
    if len(owners) != len(target):
        return text, ()
    for candidate in candidates:
        raw = candidate.evidence.raw_text
        if (
            owners
            and candidate.quality.valid_for_consensus
            and all(owner == candidate.candidate_id for owner in owners)
            and character_invariant(text, raw)
        ):
            return raw, (
                {
                    "candidate_id": candidate.candidate_id,
                    "model_id": candidate.evidence.model_id,
                    "boundary_count": len(_surface(raw)[2]),
                    "exact_surface": True,
                },
            )
    marks: dict[int, str] = {}
    evidence: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.quality.valid_for_consensus:
            continue
        source, _, boundaries = _surface(candidate.evidence.raw_text)
        adopted = 0
        for block in SequenceMatcher(None, source, target, autojunk=False).get_matching_blocks():
            if not block.size:
                continue
            matched = source[block.a : block.a + block.size]
            if source != target and (
                block.size < 4 or source.count(matched) != 1 or target.count(matched) != 1
            ):
                continue
            for position, mark in boundaries.items():
                # Require matching context on both sides, or matching utterance edges.
                interior = block.a < position < block.a + block.size
                start = position == block.a == block.b == 0
                end = position == block.a + block.size == len(
                    source
                ) and block.b + block.size == len(target)
                if not (interior or start or end):
                    continue
                mapped = block.b + position - block.a
                if mapped not in offsets:
                    continue
                adjacent = owners[max(0, mapped - 1) : min(len(owners), mapped + 1)]
                if not adjacent or any(owner != candidate.candidate_id for owner in adjacent):
                    continue
                ordinal = offsets[mapped]
                marks.setdefault(ordinal, mark)
                adopted += 1
        if adopted:
            evidence.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "model_id": candidate.evidence.model_id,
                    "boundary_count": adopted,
                }
            )
    restored = apply_boundary_marks(text, marks)
    require_character_invariance(text, restored)
    return restored, tuple(evidence)
