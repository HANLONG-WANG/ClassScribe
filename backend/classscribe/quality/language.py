"""Language-specific lexical and pronunciation comparison for ASR review."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from classscribe.quality.text import (
    normalized_edit_distance,
    strip_for_comparison,
    tokenize_for_language,
)

_UNITS = re.compile(
    r"(?<![A-Za-z])(?:kg|g|mg|km|m|cm|mm|hz|khz|mhz|gb|mb|ml|l|%|℃)(?![A-Za-z])",
    re.I,
)
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")
_NEGATIONS = {
    "zh": ("不", "没", "无", "非"),
    "ja": ("ない", "ぬ", "無", "非"),
    "en": ("no", "not", "never", "without"),
}


@dataclass(frozen=True, slots=True)
class ComparisonContext:
    readings: Mapping[str, str]
    english_phonemes: Mapping[str, str]
    proper_names: frozenset[str] = frozenset()
    course_terms: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CandidateComparison:
    language: str
    lexical_distance: float
    pronunciation_distance: float | None
    homophone_spelling_conflict: bool
    high_value_conflicts: tuple[str, ...]
    proper_noun_position_conflicts: tuple[str, ...]
    content_presence_conflict: bool


def compare_language_candidates(
    left: str,
    right: str,
    language: str,
    context: ComparisonContext,
) -> CandidateComparison:
    left_tokens = tokenize_for_language(left, language)
    right_tokens = tokenize_for_language(right, language)
    lexical = normalized_edit_distance(left_tokens, right_tokens)
    left_pronunciation = _pronunciation(left, language, context)
    right_pronunciation = _pronunciation(right, language, context)
    pronunciation = (
        normalized_edit_distance(tuple(left_pronunciation), tuple(right_pronunciation))
        if left_pronunciation and right_pronunciation
        else None
    )
    conflicts = _high_value_conflicts(left, right, language, context)
    position_conflicts = _proper_noun_position_conflicts(left, right, context)
    return CandidateComparison(
        language=language,
        lexical_distance=round(lexical, 6),
        pronunciation_distance=round(pronunciation, 6) if pronunciation is not None else None,
        homophone_spelling_conflict=bool(
            lexical > 0.05 and pronunciation is not None and pronunciation <= 0.15
        ),
        high_value_conflicts=conflicts,
        proper_noun_position_conflicts=position_conflicts,
        content_presence_conflict=bool(left.strip()) != bool(right.strip()),
    )


def normalize_kana(value: str) -> str:
    result: list[str] = []
    for character in unicodedata.normalize("NFKC", value):
        codepoint = ord(character)
        if 0x30A1 <= codepoint <= 0x30F6:
            result.append(chr(codepoint - 0x60))
        else:
            result.append(character.casefold())
    return "".join(result)


def _pronunciation(text: str, language: str, context: ComparisonContext) -> str:
    if language == "en":
        return " ".join(
            context.english_phonemes.get(token, token)
            for token in tokenize_for_language(text, language)
        )
    result = unicodedata.normalize("NFKC", text)
    for written in sorted(context.readings, key=len, reverse=True):
        result = result.replace(written, context.readings[written])
    result = strip_for_comparison(result)
    return normalize_kana(result) if language == "ja" else result.casefold()


def _high_value_conflicts(
    left: str,
    right: str,
    language: str,
    context: ComparisonContext,
) -> tuple[str, ...]:
    conflicts: list[str] = []
    for label, pattern in (("number", _NUMBERS), ("unit", _UNITS)):
        if set(match.group(0).casefold() for match in pattern.finditer(left)) != set(
            match.group(0).casefold() for match in pattern.finditer(right)
        ):
            conflicts.append(label)
    left_folded = unicodedata.normalize("NFKC", left).casefold()
    right_folded = unicodedata.normalize("NFKC", right).casefold()
    if {token for token in _NEGATIONS[language] if token in left_folded} != {
        token for token in _NEGATIONS[language] if token in right_folded
    }:
        conflicts.append("negation")
    for label, values in (
        ("proper_name", context.proper_names),
        ("course_term", context.course_terms),
    ):
        if {value for value in values if value.casefold() in left_folded} != {
            value for value in values if value.casefold() in right_folded
        }:
            conflicts.append(label)
    return tuple(conflicts)


def _proper_noun_position_conflicts(
    left: str,
    right: str,
    context: ComparisonContext,
) -> tuple[str, ...]:
    left_folded = unicodedata.normalize("NFKC", left).casefold()
    right_folded = unicodedata.normalize("NFKC", right).casefold()
    conflicts: list[str] = []
    for value in sorted(context.proper_names | context.course_terms):
        token = unicodedata.normalize("NFKC", value).casefold()
        left_index = left_folded.find(token)
        right_index = right_folded.find(token)
        if left_index < 0 or right_index < 0:
            continue
        left_position = left_index / max(len(left_folded), 1)
        right_position = right_index / max(len(right_folded), 1)
        if abs(left_position - right_position) > 0.2:
            conflicts.append(value)
    return tuple(conflicts)
