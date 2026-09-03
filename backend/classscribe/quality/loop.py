"""Decode-loop and output-density rejection before multi-model consensus."""

from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise

from classscribe.quality.models import QualityIssue
from classscribe.quality.text import tokenize_for_language


@dataclass(frozen=True, slots=True)
class LoopInspection:
    issues: tuple[QualityIssue, ...]
    metrics: dict[str, int | float | bool | str]


def inspect_decode_loop(
    text: str,
    language: str,
    *,
    voiced_seconds: float,
    prefix_snapshots: tuple[str, ...] = (),
    max_ngram_occurrences: int = 3,
    max_characters_per_voiced_second: float = 24.0,
) -> LoopInspection:
    tokens = tokenize_for_language(text, language)
    issues: list[QualityIssue] = []
    maximum_ngram_count = 0
    maximum_ngram_size = 0
    for size in range(3, min(10, len(tokens)) + 1):
        counts = Counter(
            tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)
        )
        size_maximum = max(counts.values(), default=0)
        if size_maximum > maximum_ngram_count:
            maximum_ngram_count = size_maximum
            maximum_ngram_size = size
    if maximum_ngram_count > max_ngram_occurrences:
        issues.append(QualityIssue.REPEATED_NGRAM)

    shortest_period = _shortest_repeated_suffix(tokens)
    if shortest_period is not None:
        issues.append(QualityIssue.SHORTEST_LOOP)

    visible_characters = sum(not character.isspace() for character in text)
    characters_per_voiced_second = visible_characters / max(voiced_seconds, 0.1)
    if visible_characters >= 24 and characters_per_voiced_second > max_characters_per_voiced_second:
        issues.append(QualityIssue.OUTPUT_TOO_DENSE)

    encoded = text.encode("utf-8")
    compression_ratio = len(zlib.compress(encoded)) / max(len(encoded), 1)
    if len(tokens) >= 20 and compression_ratio < 0.28:
        issues.append(QualityIssue.REPETITION_COMPRESSION)

    repeated_sentence_count = _maximum_sentence_count(text)
    if repeated_sentence_count > 2:
        issues.append(QualityIssue.REPEATED_SENTENCE)

    prefix_stalled = _prefix_novelty_stalled(prefix_snapshots, language)
    if prefix_stalled:
        issues.append(QualityIssue.PREFIX_STAGNATION)

    return LoopInspection(
        issues=tuple(dict.fromkeys(issues)),
        metrics={
            "token_count": len(tokens),
            "maximum_3_to_10_ngram_occurrences": maximum_ngram_count,
            "maximum_repeated_ngram_size": maximum_ngram_size,
            "shortest_loop_period_tokens": shortest_period or 0,
            "characters_per_voiced_second": round(characters_per_voiced_second, 6),
            "compression_ratio": round(compression_ratio, 6),
            "maximum_repeated_sentence_count": repeated_sentence_count,
            "prefix_novelty_stalled": prefix_stalled,
        },
    )


def _shortest_repeated_suffix(tokens: tuple[str, ...]) -> int | None:
    for period in range(1, len(tokens) // 4 + 1):
        repeated = tokens[-period:]
        if all(
            tokens[-period * cycle : -period * (cycle - 1) or None] == repeated
            for cycle in range(1, 5)
        ):
            return period
    return None


def _maximum_sentence_count(text: str) -> int:
    normalized = text
    for punctuation in ("。", "\uff01", "\uff1f", "!", "?", ";", "\uff1b"):
        normalized = normalized.replace(punctuation, "\n")
    sentences = ["".join(item.split()).casefold() for item in normalized.splitlines()]
    counts = Counter(item for item in sentences if len(item) >= 3)
    return max(counts.values(), default=0)


def _prefix_novelty_stalled(snapshots: tuple[str, ...], language: str) -> bool:
    if len(snapshots) < 4:
        return False
    recent = tuple(tokenize_for_language(snapshot, language) for snapshot in snapshots[-4:])
    if max(map(len, recent)) - min(map(len, recent)) <= 1:
        return True
    stalled_transitions = 0
    for previous, current in pairwise(recent):
        if len(current) < len(previous) or current[: len(previous)] != previous:
            stalled_transitions = 0
            continue
        appended = current[len(previous) :]
        history = set(previous[-12:])
        stalled_transitions = (
            stalled_transitions + 1 if appended and set(appended) <= history else 0
        )
    return stalled_transitions >= 3
