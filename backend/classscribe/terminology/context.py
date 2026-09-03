"""Bounded rolling course context and pronunciation-keyword selection."""

from __future__ import annotations

from dataclasses import dataclass

from classscribe.terminology.models import CourseTerm


@dataclass(frozen=True, slots=True)
class ConfirmedSegment:
    text: str
    language: str
    course_id: str
    chapter: str | None
    ordinal: int
    confirmed: bool = True


@dataclass(frozen=True, slots=True)
class TermUsage:
    term: CourseTerm
    course_id: str
    recent_frequency: int = 0


@dataclass(frozen=True, slots=True)
class ContextBundle:
    confirmed_segments: tuple[str, ...]
    keywords: tuple[CourseTerm, ...]
    rendered: str
    bias_only: bool = True
    permits_unsupported_insertion: bool = False


def select_rolling_context(
    *,
    course_id: str,
    chapter: str | None,
    language: str,
    history: tuple[ConfirmedSegment, ...],
    usages: tuple[TermUsage, ...],
    recent_count: int = 3,
    top_k: int = 12,
    max_characters: int = 2400,
) -> ContextBundle:
    """Select only one to three confirmed segments plus relevant course terms."""

    if not 1 <= recent_count <= 3 or not 1 <= top_k <= 32 or max_characters < 128:
        raise ValueError("context bounds are invalid")
    eligible = sorted(
        (
            item
            for item in history
            if item.confirmed and item.course_id == course_id and item.language == language
        ),
        key=lambda item: item.ordinal,
    )[-recent_count:]
    relevant = [
        item
        for item in usages
        if item.course_id == course_id and (item.term.language in {None, language})
    ]
    ranked = sorted(
        relevant,
        key=lambda item: (
            -(40 if chapter and item.term.chapter == chapter else 0),
            -item.recent_frequency,
            -item.term.weight,
            item.term.canonical,
        ),
    )[:top_k]
    segments = tuple(item.text.strip() for item in eligible if item.text.strip())
    terms = tuple(item.term for item in ranked)
    parts: list[str] = []
    if segments:
        parts.append("Recently confirmed transcript:\n" + "\n".join(segments))
    if terms:
        parts.append(
            "Possible course terms (bias only; use only with acoustic support):\n"
            + "; ".join(f"{term.canonical} ({term.reading})" for term in terms)
        )
    rendered = "\n\n".join(parts)
    if len(rendered) > max_characters:
        rendered = rendered[:max_characters].rsplit(" ", 1)[0].rstrip()
    return ContextBundle(segments, terms, rendered)
