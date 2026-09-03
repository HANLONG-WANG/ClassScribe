"""Chinese FireRedPunc proposal with acoustic sentence-boundary correction."""

from __future__ import annotations

from classscribe.punctuation.boundaries import acoustic_marks, apply_boundary_marks
from classscribe.punctuation.guard import (
    character_invariant,
    require_character_invariance,
    strip_punctuation_and_spacing,
)
from classscribe.punctuation.models import AcousticBoundary, PunctuationResult


def punctuate_chinese(
    text: str,
    *,
    firered_proposal: str | None,
    acoustic_boundaries: tuple[AcousticBoundary, ...] = (),
) -> PunctuationResult:
    if not text.strip():
        return PunctuationResult(text, "empty", True)
    rejected: list[str] = []
    if firered_proposal is not None and character_invariant(text, firered_proposal):
        proposed = firered_proposal
        source = "firered_punc"
    else:
        proposed = text
        source = "source_fallback"
        if firered_proposal is not None:
            rejected.append("firered_punc_changed_characters")
    length = len(strip_punctuation_and_spacing(proposed))
    marks = acoustic_marks(
        length, acoustic_boundaries, comma="\uff0c", stop="\u3002", question="\uff1f"
    )
    if marks:
        proposed = apply_boundary_marks(proposed, marks)
        source += "+acoustic_boundaries"
    require_character_invariance(text, proposed)
    return PunctuationResult(
        proposed,
        source,
        True,
        tuple(rejected),
        {"acoustic_boundary_count": len(marks), "firered_used": source.startswith("firered")},
    )
