"""Text-only reading boundaries; offsets never imply new audio timestamps."""
# ruff: noqa: RUF001

import re
from collections.abc import Sequence
from dataclasses import dataclass

PARAGRAPH_CHARACTERS = 300
PARAGRAPH_SENTENCES = 6
PARAGRAPH_PAUSE_MS = 1200

_TERMINATORS = frozenset("。！？.!?")
_CLOSING_MARKS = frozenset("\"'”’」』】）》〉]}")
_FORMATTING_MARKS = frozenset("。！？.!?,，、;；:：\"'“”‘’「」『』【】（）()[]{}《》〈〉…")
_ABBREVIATIONS = frozenset(
    [
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "no",
        "fig",
        "inc",
        "ltd",
        "approx",
        "dept",
        "a.m",
        "p.m",
    ]
)


def _period_boundary(text: str, index: int) -> bool:
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if following.isdecimal():
        return False
    if previous.isascii() and following.isascii() and previous.isalnum() and following.isalnum():
        return False
    start = index
    while start > 0 and (
        text[start - 1].isascii() and (text[start - 1].isalpha() or text[start - 1] == ".")
    ):
        start -= 1
    word = text[start : index + 1]
    if word[:-1].lower() in _ABBREVIATIONS or re.fullmatch(r"(?:[A-Za-z]\.){2,}", word):
        return False
    if re.fullmatch(r"[A-Z]\.", word):
        next_index = index + 1
        while next_index < len(text) and text[next_index].isspace():
            next_index += 1
        return not (
            next_index < len(text) and text[next_index].isascii() and text[next_index].isupper()
        )
    return True


def sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Partition the original text, retaining quotes, spacing and blank lines."""
    spans: list[tuple[int, int]] = []
    start = index = 0
    while index < len(text):
        blank_line = re.match(r"\n[ \t\r]*\n", text[index:]) if text[index] == "\n" else None
        boundary = text[index] in _TERMINATORS and (
            text[index] != "." or _period_boundary(text, index)
        )
        if not boundary and blank_line is None:
            index += 1
            continue
        end = index + (blank_line.end() if blank_line else 1)
        if boundary:
            while end < len(text) and text[end] in _TERMINATORS:
                end += 1
            while end < len(text) and text[end] in _CLOSING_MARKS:
                end += 1
        while end < len(text) and text[end].isspace():
            end += 1
        if text[start:end].strip():
            spans.append((start, end))
            start = end
        index = end
    if start < len(text) or not spans:
        spans.append((start, len(text)))
    return tuple(spans)


def lexical_positions(text: str) -> tuple[tuple[str, int], ...]:
    """Ignore formatting, but retain decimal separators, times and contractions."""
    result: list[tuple[str, int]] = []
    for index, character in enumerate(text):
        previous = text[index - 1] if index else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        significant = (
            (character == "." and following.isdecimal())
            or (character in ",:" and previous.isdigit() and following.isdigit())
            or (character in "'’" and previous.isalnum() and following.isalnum())
        )
        if not character.isspace() and (character not in _FORMATTING_MARKS or significant):
            result.append((character, index))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class TextSlice:
    source_index: int
    start: int
    end: int


def paragraph_slices(
    texts: Sequence[str], boundaries: Sequence[bool]
) -> tuple[tuple[TextSlice, ...], ...]:
    """Group sentence slices with soft size limits and explicit source boundaries."""
    paragraphs: list[tuple[TextSlice, ...]] = []
    continuations: set[int] = set()
    current: list[TextSlice] = []
    characters = 0
    previous_text = ""
    for source_index, (text, source_boundary) in enumerate(zip(texts, boundaries, strict=True)):
        for start, end in sentence_spans(text):
            piece = text[start:end]
            length = len(piece.strip())
            hard_boundary = bool(
                (start == 0 and source_boundary) or re.search(r"\n[ \t\r]*\n\s*$", previous_text)
            )
            size_boundary = (
                len(current) >= PARAGRAPH_SENTENCES or characters + length > PARAGRAPH_CHARACTERS
            )
            if current and (hard_boundary or size_boundary):
                paragraphs.append(tuple(current))
                if not hard_boundary:
                    continuations.add(len(paragraphs))
                current = []
                characters = 0
            current.append(TextSlice(source_index, start, end))
            characters += length
            previous_text = piece
    if current:
        paragraphs.append(tuple(current))
    # Balance a small tail only across a size split, never a speaker/pause/manual boundary.
    for index in sorted(continuations):
        previous, tail = paragraphs[index - 1], paragraphs[index]
        if len(tail) != 1 or len(previous) < 4:
            continue
        for count in range(min(2, len(previous) - 3), 0, -1):
            balanced = previous[-count:] + tail
            length = sum(
                len(texts[piece.source_index][piece.start : piece.end].strip())
                for piece in balanced
            )
            if length <= PARAGRAPH_CHARACTERS:
                paragraphs[index - 1] = previous[:-count]
                paragraphs[index] = balanced
                break
    return tuple(paragraphs)
