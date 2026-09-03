"""Language-independent punctuation character-invariance guard."""

from __future__ import annotations

import unicodedata


class PunctuationInvariantError(ValueError):
    pass


def is_punctuation_or_spacing(character: str) -> bool:
    return character.isspace() or unicodedata.category(character).startswith(("P", "Z"))


def strip_punctuation_and_spacing(text: str) -> str:
    return "".join(character for character in text if not is_punctuation_or_spacing(character))


def require_character_invariance(before: str, after: str) -> None:
    if strip_punctuation_and_spacing(before) != strip_punctuation_and_spacing(after):
        raise PunctuationInvariantError("punctuation proposal changed the non-punctuation sequence")


def character_invariant(before: str, after: str) -> bool:
    try:
        require_character_invariance(before, after)
    except PunctuationInvariantError:
        return False
    return True
