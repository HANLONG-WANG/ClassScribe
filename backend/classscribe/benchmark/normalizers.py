"""Comparable, explicitly named normalization policies for local benchmark reports."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

_ENGLISH_WORD = re.compile("[^\\W_]+(?:['\\u2019][^\\W_]+)?|\\d+(?:[.,]\\d+)*", re.UNICODE)


def normalize_cjk(text: str) -> str:
    """NFKC, lowercase embedded Latin, remove spacing/punctuation; preserve digits."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def raw_cjk_units(text: str) -> tuple[str, ...]:
    """Readable/raw CER units: exact non-space Unicode code points including punctuation."""

    return tuple(character for character in text if not character.isspace())


def normalized_cjk_units(text: str) -> tuple[str, ...]:
    return tuple(normalize_cjk(text))


def raw_english_words(text: str) -> tuple[str, ...]:
    return tuple(_ENGLISH_WORD.findall(unicodedata.normalize("NFKC", text)))


def normalized_english_words(text: str) -> tuple[str, ...]:
    return tuple(word.casefold().replace("\u2019", "'") for word in raw_english_words(text))


def comparison_units(text: str, language: str, *, normalized: bool) -> tuple[str, ...]:
    if language not in {"zh", "ja", "en"}:
        raise ValueError("benchmark language must be zh, ja, or en")
    if language == "en":
        return normalized_english_words(text) if normalized else raw_english_words(text)
    return normalized_cjk_units(text) if normalized else raw_cjk_units(text)


def edit_distance(reference: Sequence[object], hypothesis: Sequence[object]) -> int:
    """Memory-bounded Levenshtein distance for character, word, or label sequences."""

    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, 1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def error_rate(reference: Sequence[object], hypothesis: Sequence[object]) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return edit_distance(reference, hypothesis) / len(reference)
