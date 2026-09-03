"""Auditable deterministic terminology correction over the smart layer only."""

from __future__ import annotations

import re
from dataclasses import dataclass

from classscribe.quality.text import normalized_edit_distance
from classscribe.terminology.models import CourseTerm


@dataclass(frozen=True, slots=True)
class TextLayers:
    raw_text: str
    faithful_text: str
    smart_corrected_text: str
    user_text: str | None = None

    @property
    def export_text(self) -> str:
        return select_export_text(self)


@dataclass(frozen=True, slots=True)
class CorrectionEvidence:
    emitted_forms: frozenset[str]
    observed_reading: str | None = None
    acoustic_reading_support: float | None = None


@dataclass(frozen=True, slots=True)
class CorrectionDiff:
    start: int
    end: int
    before: str
    after: str
    rule_id: str
    source: str


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    layers: TextLayers
    diffs: tuple[CorrectionDiff, ...]
    rule_version: str = "deterministic-terminology-v1"


def apply_terminology(
    layers: TextLayers,
    terms: tuple[CourseTerm, ...],
    evidence: dict[str, CorrectionEvidence],
    *,
    language: str,
) -> CorrectionResult:
    """Replace an actually emitted alias only when term and audio/reading evidence allow it."""

    source = layers.faithful_text
    replacements: list[tuple[int, int, CourseTerm, str]] = []
    for term in terms:
        if term.language not in {None, language}:
            continue
        proof = evidence.get(term.canonical)
        if proof is None or not _supported(term, proof):
            continue
        for alias in sorted(term.aliases, key=len, reverse=True):
            for match in _matches(source, alias, language):
                replacements.append((match.start(), match.end(), term, alias))
    selected: list[tuple[int, int, CourseTerm, str]] = []
    for item in sorted(replacements, key=lambda value: (value[0], -(value[1] - value[0]))):
        if not selected or item[0] >= selected[-1][1]:
            selected.append(item)
    output: list[str] = []
    diffs: list[CorrectionDiff] = []
    cursor = 0
    for start, end, term, alias in selected:
        output.extend((source[cursor:start], term.canonical))
        rule_id = f"course-term:{term.language or language}:{term.canonical}"
        diffs.append(CorrectionDiff(start, end, alias, term.canonical, rule_id, term.source.value))
        cursor = end
    output.append(source[cursor:])
    smart = "".join(output)
    return CorrectionResult(
        TextLayers(layers.raw_text, source, smart, layers.user_text),
        tuple(diffs),
    )


def select_export_text(layers: TextLayers) -> str:
    if layers.user_text is not None and layers.user_text.strip():
        return layers.user_text
    if layers.smart_corrected_text:
        return layers.smart_corrected_text
    return layers.faithful_text


def _supported(term: CourseTerm, evidence: CorrectionEvidence) -> bool:
    trusted_term = term.user_confirmed or term.weight >= 0.75
    emitted = term.canonical in evidence.emitted_forms or any(
        alias in evidence.emitted_forms for alias in term.aliases
    )
    reading_support = False
    if evidence.observed_reading and evidence.acoustic_reading_support is not None:
        distance = normalized_edit_distance(
            tuple(term.reading.casefold()), tuple(evidence.observed_reading.casefold())
        )
        reading_support = distance <= 0.2 and evidence.acoustic_reading_support >= 0.8
    return trusted_term and (emitted or reading_support)


def _matches(text: str, form: str, language: str) -> tuple[re.Match[str], ...]:
    if language == "en" or (form[:1].isascii() and form[:1].isalnum()):
        pattern = re.compile(rf"(?<![\w]){re.escape(form)}(?![\w])", re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(form))
    return tuple(pattern.finditer(text))
