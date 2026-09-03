"""English native-first punctuation/casing, protected fallback, and evaluation metrics."""

from __future__ import annotations

import re
import unicodedata

from classscribe.punctuation.guard import character_invariant
from classscribe.punctuation.models import PunctuationResult
from classscribe.quality.text import normalized_edit_distance

_WORD = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")


def punctuate_english(
    source_text: str,
    *,
    native_text: str | None,
    firered_proposal: str | None,
    protected_forms: tuple[str, ...] = (),
) -> PunctuationResult:
    rejected: list[str] = []
    if native_text is not None and _native_healthy(source_text, native_text, protected_forms):
        selected, selected_source = native_text, "native"
    else:
        if native_text is not None:
            rejected.append("native_case_or_punctuation_abnormal")
        if firered_proposal is not None and _proposal_safe(
            source_text, firered_proposal, protected_forms
        ):
            selected, selected_source = firered_proposal, "firered_punc_fallback"
        else:
            if firered_proposal is not None:
                rejected.append("firered_punc_changed_or_unprotected_text")
            selected, selected_source = source_text, "source_fallback"
    return PunctuationResult(
        selected,
        selected_source,
        character_invariant(source_text, selected),
        tuple(rejected),
        {"protected_form_count": len(protected_forms)},
    )


def case_sensitive_wer(reference: str, hypothesis: str) -> float:
    reference_words = tuple(match.group(0) for match in _WORD.finditer(reference))
    hypothesis_words = tuple(match.group(0) for match in _WORD.finditer(hypothesis))
    return normalized_edit_distance(reference_words, hypothesis_words)


def punctuation_f1(reference: str, hypothesis: str) -> float:
    reference_events = _punctuation_events(reference)
    hypothesis_events = _punctuation_events(hypothesis)
    true_positive = sum(
        min(count, hypothesis_events.get(key, 0)) for key, count in reference_events.items()
    )
    predicted = sum(hypothesis_events.values())
    expected = sum(reference_events.values())
    if predicted == expected == 0:
        return 1.0
    if not true_positive:
        return 0.0
    precision = true_positive / predicted
    recall = true_positive / expected
    return 2 * precision * recall / (precision + recall)


def _native_healthy(source: str, proposal: str, protected: tuple[str, ...]) -> bool:
    if not _proposal_safe(source, proposal, protected):
        return False
    words = _WORD.findall(proposal)
    punctuation = sum(unicodedata.category(char).startswith("P") for char in proposal)
    has_sentence_end = bool(re.search(r"[.!?][\"')\]]?\s*$", proposal.strip()))
    all_lower = len(words) >= 4 and all(word == word.lower() for word in words if word.isalpha())
    return punctuation <= max(4, len(words) // 2) and has_sentence_end and not all_lower


def _proposal_safe(source: str, proposal: str, protected: tuple[str, ...]) -> bool:
    if not character_invariant(source, proposal):
        return False
    return all(form not in source or form in proposal for form in protected)


def _punctuation_events(text: str) -> dict[tuple[int, str], int]:
    events: dict[tuple[int, str], int] = {}
    word_index = 0
    for character in text:
        if character.isalnum():
            word_index += 1
        elif unicodedata.category(character).startswith("P"):
            key = (word_index, character)
            events[key] = events.get(key, 0) + 1
    return events
