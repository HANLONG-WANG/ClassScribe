"""Strict course terminology and material-source values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class TermSource(StrEnum):
    MANUAL = "manual"
    TXT = "txt"
    MARKDOWN = "markdown"
    CSV = "csv"
    PPTX = "pptx"
    PDF = "pdf"
    HANDOUT = "handout"
    TEXTBOOK = "textbook"
    HISTORICAL_CONFIRMED = "historical_confirmed"
    AUTOMATIC_SUGGESTION = "automatic_suggestion"


class ConfirmationStatus(StrEnum):
    CONFIRMED = "confirmed"
    SUGGESTED = "suggested"


@dataclass(frozen=True, slots=True)
class CoursePerson:
    canonical: str
    reading: str

    def __post_init__(self) -> None:
        if not self.canonical.strip() or not self.reading.strip():
            raise ValueError("instructor canonical form and reading are required")


@dataclass(frozen=True, slots=True)
class CourseTerm:
    canonical: str
    reading: str
    aliases: tuple[str, ...] = ()
    weight: float = 1.0
    source: TermSource = TermSource.MANUAL
    confirmation: ConfirmationStatus = ConfirmationStatus.CONFIRMED
    language: str | None = None
    chapter: str | None = None

    def __post_init__(self) -> None:
        canonical = self.canonical.strip()
        reading = self.reading.strip()
        if not canonical or not reading:
            raise ValueError("term canonical form and reading are required")
        if any(not alias.strip() for alias in self.aliases):
            raise ValueError("term aliases must not be empty")
        if len(set(self.aliases)) != len(self.aliases) or canonical in self.aliases:
            raise ValueError("term aliases must be unique and differ from canonical")
        if not math.isfinite(self.weight) or not 0 <= self.weight <= 1:
            raise ValueError("term weight must be finite and within [0, 1]")
        if self.language is not None and self.language not in {"zh", "ja", "en"}:
            raise ValueError("term language must be zh, ja, en, or omitted")
        if self.confirmation is ConfirmationStatus.SUGGESTED and self.weight > 0.3:
            raise ValueError("automatic/unconfirmed suggestions must remain low weight")

    @property
    def user_confirmed(self) -> bool:
        return self.confirmation is ConfirmationStatus.CONFIRMED


@dataclass(frozen=True, slots=True)
class CourseConfig:
    course_id: str
    language: str
    name: str
    instructors: tuple[CoursePerson, ...]
    terms: tuple[CourseTerm, ...]
    chapter: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.course_id.strip() or not self.name.strip():
            raise ValueError("course_id and name are required")
        if self.language not in {"zh", "ja", "en"}:
            raise ValueError("course language must be zh, ja, or en")
        if self.version < 1:
            raise ValueError("course config version must be positive")
        identities = {(term.language or self.language, term.canonical) for term in self.terms}
        if len(identities) != len(self.terms):
            raise ValueError("course terms must have unique language/canonical pairs")


def load_course_config(path: Path) -> CourseConfig:
    """Load one bounded, local, non-symlink YAML course configuration."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("course config must be a regular non-symlink file")
    data = path.read_bytes()
    if len(data) > 2_000_000:
        raise ValueError("course config exceeds 2 MB")
    raw = yaml.safe_load(data)
    if not isinstance(raw, dict):
        raise ValueError("course config root must be an object")
    allowed = {"course_id", "language", "name", "instructors", "terms", "chapter", "version"}
    unknown = raw.keys() - allowed
    if unknown:
        raise ValueError(f"unknown course config fields: {sorted(unknown)}")
    language = _required_string(raw, "language")
    instructors_raw = raw.get("instructors", [])
    terms_raw = raw.get("terms", [])
    if not isinstance(instructors_raw, list) or not isinstance(terms_raw, list):
        raise ValueError("instructors and terms must be lists")
    instructors = tuple(_person(item) for item in instructors_raw)
    terms = tuple(_term(item, default_language=language) for item in terms_raw)
    chapter = raw.get("chapter")
    if chapter is not None and (not isinstance(chapter, str) or not chapter.strip()):
        raise ValueError("chapter must be a non-empty string when supplied")
    version = raw.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("course config version must be an integer")
    return CourseConfig(
        course_id=_required_string(raw, "course_id"),
        language=language,
        name=_required_string(raw, "name"),
        instructors=instructors,
        terms=terms,
        chapter=chapter.strip() if isinstance(chapter, str) else None,
        version=version,
    )


def _person(value: Any) -> CoursePerson:
    if not isinstance(value, dict) or value.keys() - {"canonical", "reading"}:
        raise ValueError("each instructor must contain only canonical and reading")
    return CoursePerson(_required_string(value, "canonical"), _required_string(value, "reading"))


def _term(value: Any, *, default_language: str) -> CourseTerm:
    allowed = {
        "canonical",
        "reading",
        "aliases",
        "weight",
        "source",
        "confirmation",
        "language",
        "chapter",
    }
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError("course term has unknown fields")
    aliases = value.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
        raise ValueError("term aliases must be a string list")
    weight = value.get("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        raise ValueError("term weight must be numeric")
    language = value.get("language", default_language)
    chapter = value.get("chapter")
    if not isinstance(language, str) or (chapter is not None and not isinstance(chapter, str)):
        raise ValueError("term language/chapter values are invalid")
    return CourseTerm(
        canonical=_required_string(value, "canonical"),
        reading=_required_string(value, "reading"),
        aliases=tuple(item.strip() for item in aliases),
        weight=float(weight),
        source=TermSource(str(value.get("source", TermSource.MANUAL.value))),
        confirmation=ConfirmationStatus(
            str(value.get("confirmation", ConfirmationStatus.CONFIRMED.value))
        ),
        language=language,
        chapter=chapter.strip() if chapter else None,
    )


def _required_string(value: dict[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return item.strip()
