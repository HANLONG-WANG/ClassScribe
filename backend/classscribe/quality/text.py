"""Small deterministic text primitives; long-string voting is deliberately absent."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Hashable, Sequence

_CJK_OR_KANA = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")
_WORD = re.compile(r"[A-Za-z]+(?:['\u2019-][A-Za-z]+)*|\d+(?:[.,]\d+)?|[^\W_]", re.UNICODE)


def tokenize_for_language(text: str, language: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in surface_tokens(text, language))


def surface_tokens(text: str, language: str) -> tuple[str, ...]:
    """Preserve display spelling while comparison callers normalize separately."""
    normalized = unicodedata.normalize("NFKC", text)
    if language in {"zh", "ja"}:
        result: list[str] = []
        latin_buffer: list[str] = []
        for character in normalized:
            if _CJK_OR_KANA.fullmatch(character):
                if latin_buffer:
                    result.append("".join(latin_buffer))
                    latin_buffer.clear()
                result.append(character)
            elif character.isalnum() or character in {"'", "\u2019", "-", "."}:
                latin_buffer.append(character)
            elif latin_buffer:
                result.append("".join(latin_buffer))
                latin_buffer.clear()
        if latin_buffer:
            result.append("".join(latin_buffer))
        return tuple(token for token in result if token)
    if language == "en":
        return tuple(match.group(0) for match in _WORD.finditer(normalized))
    raise ValueError("language must be zh, ja, or en")


def normalized_edit_distance[TokenT: Hashable](
    left: Sequence[TokenT], right: Sequence[TokenT]
) -> float:
    if not left and not right:
        return 0.0
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_item in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1] / max(len(left), len(right), 1)


def strip_for_comparison(text: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", text)
        if character.isalnum()
    )
