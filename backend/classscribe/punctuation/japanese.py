"""Japanese boundary candidates projected onto the selected faithful character sequence."""

from __future__ import annotations

from classscribe.punctuation.boundaries import acoustic_marks, apply_boundary_marks
from classscribe.punctuation.guard import (
    character_invariant,
    is_punctuation_or_spacing,
    require_character_invariance,
    strip_punctuation_and_spacing,
)
from classscribe.punctuation.models import AcousticBoundary, BoundaryLabel, PunctuationResult
from classscribe.quality.text import normalized_edit_distance

_BOUNDARY_MARKS = frozenset({"\u3001", "\u3002", "\uff1f", "\uff01"})


def punctuate_japanese(
    text: str,
    *,
    granite_proposal: str | None,
    acoustic_boundaries: tuple[AcousticBoundary, ...] = (),
    tagger_labels: tuple[BoundaryLabel, ...] = (),
) -> PunctuationResult:
    if not text.strip():
        return PunctuationResult(text, "empty", True)
    length = len(strip_punctuation_and_spacing(text))
    rejected: list[str] = []
    marks: dict[int, str] = {}
    source_parts: list[str] = []
    if granite_proposal:
        source_chars = strip_punctuation_and_spacing(granite_proposal)
        target_chars = strip_punctuation_and_spacing(text)
        distance = normalized_edit_distance(tuple(source_chars), tuple(target_chars))
        if distance <= 0.25:
            marks.update(_project_granite_boundaries(granite_proposal, length))
            source_parts.append("granite_projection")
        else:
            rejected.append("granite_text_diverged")
    acoustic = acoustic_marks(
        length, acoustic_boundaries, comma="\u3001", stop="\u3002", question="\uff1f"
    )
    for position, mark in acoustic.items():
        marks.setdefault(position, mark)
    if acoustic:
        source_parts.append("acoustic_boundaries")
    if not marks:
        for label in tagger_labels:
            if label.confidence >= 0.7 and label.after_character <= length:
                marks[label.after_character] = label.punctuation
        if marks:
            source_parts.append("sequence_boundary_tagger")
    proposed = _balance_japanese_punctuation(apply_boundary_marks(text, marks))
    if not character_invariant(text, proposed) or _punctuation_density(proposed) > 0.35:
        rejected.append("projected_punctuation_failed_invariant_or_density")
        fallback_marks = acoustic_marks(
            length,
            acoustic_boundaries,
            comma="\u3001",
            stop="\u3002",
            question="\uff1f",
        )
        proposed = _balance_japanese_punctuation(apply_boundary_marks(text, fallback_marks))
        source_parts = ["acoustic_fallback"] if fallback_marks else ["source_fallback"]
    require_character_invariance(text, proposed)
    return PunctuationResult(
        proposed,
        "+".join(source_parts) or "source_fallback",
        True,
        tuple(rejected),
        {"boundary_count": len(marks), "punctuation_density": _punctuation_density(proposed)},
    )


def _project_granite_boundaries(proposal: str, target_length: int) -> dict[int, str]:
    source_length = len(strip_punctuation_and_spacing(proposal))
    if not source_length:
        return {}
    marks: dict[int, str] = {}
    ordinal = 0
    for character in proposal:
        if character in _BOUNDARY_MARKS:
            target = round(ordinal * target_length / source_length)
            marks[max(0, min(target, target_length))] = character
        elif not is_punctuation_or_spacing(character):
            ordinal += 1
    return marks


def _balance_japanese_punctuation(text: str) -> str:
    pairs = {"\u300c": "\u300d", "\uff08": "\uff09", "(": ")"}
    closers = {value: key for key, value in pairs.items()}
    stack: list[str] = []
    output: list[str] = []
    for character in text:
        if character in pairs:
            stack.append(character)
            output.append(character)
        elif character in closers:
            if stack and stack[-1] == closers[character]:
                stack.pop()
                output.append(character)
        else:
            output.append(character)
    output.extend(pairs[opening] for opening in reversed(stack))
    return "".join(output)


def _punctuation_density(text: str) -> float:
    lexical = max(1, len(strip_punctuation_and_spacing(text)))
    count = sum(is_punctuation_or_spacing(char) and not char.isspace() for char in text)
    return count / lexical
