"""Bounded local course-material importers that produce suggestions, never insertions."""

from __future__ import annotations

import csv
import io
import re
import subprocess
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from classscribe.terminology.models import ConfirmationStatus, CourseTerm, TermSource

_CJK_PHRASE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]{2,20}")
_LATIN_TERM = re.compile(r"\b(?:[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]*)+|[A-Z]{2,12})\b")
_MAX_SOURCE_BYTES = 50_000_000
_MAX_EXTRACTED_CHARACTERS = 2_000_000


@dataclass(frozen=True, slots=True)
class MaterialImport:
    source: TermSource
    text: str
    suggestions: tuple[CourseTerm, ...]
    warnings: tuple[str, ...] = ()


def import_material(
    path: Path,
    *,
    language: str,
    source: TermSource | None = None,
) -> MaterialImport:
    """Extract local text and low-weight suggestions without mutating a glossary."""

    resolved_source = source or _source_for_suffix(path.suffix.lower())
    _validate_source(path)
    if resolved_source is TermSource.CSV:
        terms = import_csv_terms(path, language=language)
        text = "\n".join(term.canonical for term in terms)
        return MaterialImport(resolved_source, text, terms)
    if path.suffix.lower() == ".pptx":
        text = _pptx_text(path)
    elif path.suffix.lower() == ".pdf":
        text = _pdf_text(path)
    else:
        text = path.read_text(encoding="utf-8")
    text = text[:_MAX_EXTRACTED_CHARACTERS]
    return MaterialImport(resolved_source, text, suggest_terms(text, language, resolved_source))


def import_csv_terms(path: Path, *, language: str) -> tuple[CourseTerm, ...]:
    _validate_source(path)
    rows = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")))
    if rows.fieldnames is None or "canonical" not in rows.fieldnames:
        raise ValueError("course-term CSV requires a canonical column")
    terms: list[CourseTerm] = []
    for row in rows:
        canonical = (row.get("canonical") or "").strip()
        reading = (row.get("reading") or canonical).strip()
        if not canonical:
            continue
        aliases = tuple(
            item.strip() for item in re.split(r"[|;]", row.get("aliases") or "") if item.strip()
        )
        confirmed = (row.get("confirmation") or "suggested").strip().casefold() == "confirmed"
        weight = float(row.get("weight") or (1.0 if confirmed else 0.2))
        if not confirmed:
            weight = min(weight, 0.3)
        terms.append(
            CourseTerm(
                canonical=canonical,
                reading=reading,
                aliases=aliases,
                weight=weight,
                source=TermSource.CSV,
                confirmation=(
                    ConfirmationStatus.CONFIRMED if confirmed else ConfirmationStatus.SUGGESTED
                ),
                language=(row.get("language") or language).strip(),
                chapter=(row.get("chapter") or "").strip() or None,
            )
        )
    return tuple(terms)


def suggest_terms(text: str, language: str, source: TermSource) -> tuple[CourseTerm, ...]:
    """Return conservative low-weight candidates; callers must not treat these as emitted text."""

    if language not in {"zh", "ja", "en"}:
        raise ValueError("suggestion language must be zh, ja, or en")
    matches = _LATIN_TERM.findall(text)
    if language in {"zh", "ja"}:
        matches.extend(_CJK_PHRASE.findall(text))
    counts = Counter(item.strip() for item in matches if item.strip())
    return tuple(
        CourseTerm(
            canonical=canonical,
            reading=canonical,
            weight=min(0.3, 0.12 + 0.02 * count),
            source=source,
            confirmation=ConfirmationStatus.SUGGESTED,
            language=language,
        )
        for canonical, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


def confirmed_history_terms(
    values: list[tuple[str, str, tuple[str, ...], int]], *, language: str
) -> tuple[CourseTerm, ...]:
    """Convert only already user-confirmed historical spellings into high-weight terms."""

    return tuple(
        CourseTerm(
            canonical=canonical,
            reading=reading,
            aliases=aliases,
            weight=min(1.0, 0.75 + min(frequency, 10) * 0.025),
            source=TermSource.HISTORICAL_CONFIRMED,
            confirmation=ConfirmationStatus.CONFIRMED,
            language=language,
        )
        for canonical, reading, aliases, frequency in values
        if canonical.strip() and reading.strip() and frequency > 0
    )


def _validate_source(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("material must be a regular non-symlink local file")
    if path.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError("material exceeds the 50 MB extraction limit")


def _source_for_suffix(suffix: str) -> TermSource:
    try:
        return {
            ".txt": TermSource.TXT,
            ".md": TermSource.MARKDOWN,
            ".markdown": TermSource.MARKDOWN,
            ".csv": TermSource.CSV,
            ".pptx": TermSource.PPTX,
            ".pdf": TermSource.PDF,
        }[suffix]
    except KeyError as exc:
        raise ValueError(f"unsupported course material type: {suffix or '<none>'}") from exc


def _pptx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                item for item in archive.infolist() if item.filename.startswith("ppt/slides/")
            ]
            if any(item.flag_bits & 0x1 for item in members):
                raise ValueError("encrypted PPTX material is unsupported")
            if sum(item.file_size for item in members) > _MAX_SOURCE_BYTES:
                raise ValueError("expanded PPTX slides exceed 50 MB")
            fragments: list[str] = []
            for item in sorted(members, key=lambda value: value.filename):
                root = ElementTree.fromstring(archive.read(item))
                fragments.extend(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
            return "\n".join(fragments)
    except zipfile.BadZipFile as exc:
        raise ValueError("PPTX material is not a valid ZIP package") from exc


def _pdf_text(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "--", str(path), "-"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ValueError("local pdftotext extraction is unavailable") from exc
    if result.returncode != 0:
        raise ValueError("PDF text extraction failed")
    return result.stdout.decode("utf-8", errors="strict")
