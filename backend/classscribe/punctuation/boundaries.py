"""Insertion-only projection of acoustic/model boundary evidence."""

from __future__ import annotations

from collections.abc import Mapping

from classscribe.punctuation.guard import is_punctuation_or_spacing
from classscribe.punctuation.models import AcousticBoundary


def apply_boundary_marks(text: str, marks: Mapping[int, str]) -> str:
    """Insert marks after non-punctuation character ordinals without changing any character."""

    if any(index < 0 for index in marks):
        raise ValueError("boundary index must be non-negative")
    # Existing punctuation wins over inferred marks at the same boundary.
    existing: set[int] = set()
    ordinal = 0
    for character in text:
        if not is_punctuation_or_spacing(character):
            ordinal += 1
        elif not character.isspace():
            existing.add(ordinal)
    if any(index > ordinal for index in marks):
        raise ValueError("boundary index exceeds the input character sequence")
    output: list[str] = [marks[0]] if 0 in marks and 0 not in existing else []
    ordinal = 0
    for character in text:
        output.append(character)
        if not is_punctuation_or_spacing(character):
            ordinal += 1
            if ordinal in marks and ordinal not in existing:
                output.append(marks[ordinal])
    return "".join(output)


def acoustic_marks(
    length: int,
    boundaries: tuple[AcousticBoundary, ...],
    *,
    comma: str,
    stop: str,
    question: str,
) -> dict[int, str]:
    marks: dict[int, str] = {}
    for value in boundaries:
        index = value.after_character
        if index > length:
            continue
        pause_ms = value.pause_ms
        if value.rising_intonation:
            marks[index] = question
        elif value.speaker_changed or pause_ms >= 700:
            marks[index] = stop
        elif pause_ms >= 250 and index not in marks:
            marks[index] = comma
    return marks
