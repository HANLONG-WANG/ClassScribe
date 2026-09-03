"""Strict JSONL contracts for private local gold and model predictions."""

from __future__ import annotations

import json
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

Language = Literal["zh", "ja", "en"]
Scenario = Literal["classroom", "ibus"]
Split = Literal["train", "validation", "test"]
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class WordAnnotation:
    text: str
    start_sample: int
    end_sample: int
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class SpeakerAnnotation:
    speaker: str
    start_sample: int
    end_sample: int


@dataclass(frozen=True, slots=True)
class GoldRecord:
    item_id: str
    audio: str
    scenario: Scenario
    split: Split
    language: Language
    start_sample: int
    end_sample: int
    text: str
    speaker: str | None = None
    terms: tuple[str, ...] = ()
    term_vocabulary: tuple[str, ...] = ()
    entities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    words: tuple[WordAnnotation, ...] = ()
    sentence_boundaries: tuple[int, ...] = ()
    speaker_spans: tuple[SpeakerAnnotation, ...] = ()
    voiced_spans: tuple[tuple[int, int], ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def duration_samples(self) -> int:
        return self.end_sample - self.start_sample

    @property
    def duration_seconds(self) -> float:
        return self.duration_samples / 16_000

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GoldRecord:
        required = {
            "item_id",
            "audio",
            "scenario",
            "split",
            "language",
            "start_sample",
            "end_sample",
            "text",
        }
        missing = required - value.keys()
        if missing:
            raise ValueError(f"gold item is missing fields: {sorted(missing)}")
        audio = str(value["audio"])
        path = PurePosixPath(audio)
        if path.is_absolute() or ".." in path.parts or not audio:
            raise ValueError("gold audio must be a non-empty relative path")
        scenario = str(value["scenario"])
        split = str(value["split"])
        language = str(value["language"])
        if scenario not in {"classroom", "ibus"}:
            raise ValueError("gold scenario must be classroom or ibus")
        if split not in {"train", "validation", "test"}:
            raise ValueError("gold split must be train, validation, or test")
        if language not in {"zh", "ja", "en"}:
            raise ValueError("gold language must be zh, ja, or en")
        start = _integer(value["start_sample"], "start_sample")
        end = _integer(value["end_sample"], "end_sample")
        if start < 0 or end <= start:
            raise ValueError("gold sample range must be positive and non-empty")
        words = tuple(_word(item, start, end) for item in _list(value.get("words", []), "words"))
        speakers = tuple(
            _speaker(item, start, end)
            for item in _list(value.get("speaker_spans", []), "speaker_spans")
        )
        voiced = tuple(
            _span(item, start, end, "voiced_spans")
            for item in _list(value.get("voiced_spans", []), "voiced_spans")
        )
        boundaries = tuple(
            _integer(item, "sentence_boundaries")
            for item in _list(value.get("sentence_boundaries", []), "sentence_boundaries")
        )
        if any(item < start or item > end for item in boundaries):
            raise ValueError("sentence boundary is outside the gold range")
        if boundaries != tuple(sorted(set(boundaries))):
            raise ValueError("sentence boundaries must be unique and ordered")
        if words != tuple(sorted(words, key=lambda item: (item.start_sample, item.end_sample))):
            raise ValueError("gold words must be ordered by sample range")
        entities_raw = value.get("entities", {})
        if not isinstance(entities_raw, dict):
            raise ValueError("entities must be an object")
        entities = {
            str(key): tuple(str(item) for item in _list(items, f"entities.{key}"))
            for key, items in entities_raw.items()
        }
        item_id = str(value["item_id"])
        text = str(value["text"])
        if not item_id or (not text and "silence" not in value.get("tags", [])):
            raise ValueError("gold item_id/text must be non-empty except tagged silence")
        return cls(
            item_id=item_id,
            audio=audio,
            scenario=scenario,  # type: ignore[arg-type]
            split=split,  # type: ignore[arg-type]
            language=language,  # type: ignore[arg-type]
            start_sample=start,
            end_sample=end,
            text=text,
            speaker=str(value["speaker"]) if value.get("speaker") is not None else None,
            terms=tuple(str(item) for item in _list(value.get("terms", []), "terms")),
            term_vocabulary=tuple(
                str(item) for item in _list(value.get("term_vocabulary", []), "term_vocabulary")
            ),
            entities=entities,
            words=words,
            sentence_boundaries=boundaries,
            speaker_spans=speakers,
            voiced_spans=voiced,
            tags=tuple(str(item) for item in _list(value.get("tags", []), "tags")),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkPrediction:
    item_id: str
    model_id: str
    model_revision: str
    text: str
    confidence: float | None = None
    quality_features: tuple[float, ...] = ()
    words: tuple[WordAnnotation, ...] = ()
    sentence_boundaries: tuple[int, ...] = ()
    speaker_spans: tuple[SpeakerAnnotation, ...] = ()
    voiced_spans: tuple[tuple[int, int], ...] = ()
    runtime_seconds: float = 0.0
    load_seconds: float = 0.0
    peak_vram_mb: float = 0.0
    peak_ram_mb: float = 0.0
    interim_latency_ms: float | None = None
    commit_latency_ms: float | None = None
    preemption_resume_ms: float | None = None
    cross_window_speakers: tuple[str, ...] = ()
    provenance_coverage: float = 0.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BenchmarkPrediction:
        required = {"item_id", "model_id", "model_revision", "text"}
        missing = required - value.keys()
        if missing:
            raise ValueError(f"prediction is missing fields: {sorted(missing)}")
        revision = str(value["model_revision"])
        if not COMMIT_RE.fullmatch(revision):
            raise ValueError("prediction model_revision must be a full lowercase commit SHA")
        item_id = str(value["item_id"])
        model_id = str(value["model_id"])
        if not item_id or not model_id:
            raise ValueError("prediction item_id/model_id must be non-empty")
        return cls(
            item_id=item_id,
            model_id=model_id,
            model_revision=revision,
            text=str(value["text"]),
            confidence=_optional_probability(value.get("confidence"), "confidence"),
            quality_features=tuple(
                _number(item, "quality_features")
                for item in _list(value.get("quality_features", []), "quality_features")
            ),
            words=tuple(_prediction_word(item) for item in _list(value.get("words", []), "words")),
            sentence_boundaries=tuple(
                _integer(item, "sentence_boundaries")
                for item in _list(value.get("sentence_boundaries", []), "sentence_boundaries")
            ),
            speaker_spans=tuple(
                _prediction_speaker(item)
                for item in _list(value.get("speaker_spans", []), "speaker_spans")
            ),
            voiced_spans=tuple(
                _prediction_span(item, "voiced_spans")
                for item in _list(value.get("voiced_spans", []), "voiced_spans")
            ),
            runtime_seconds=_nonnegative(value.get("runtime_seconds", 0), "runtime_seconds"),
            load_seconds=_nonnegative(value.get("load_seconds", 0), "load_seconds"),
            peak_vram_mb=_nonnegative(value.get("peak_vram_mb", 0), "peak_vram_mb"),
            peak_ram_mb=_nonnegative(value.get("peak_ram_mb", 0), "peak_ram_mb"),
            interim_latency_ms=_optional_nonnegative(
                value.get("interim_latency_ms"), "interim_latency_ms"
            ),
            commit_latency_ms=_optional_nonnegative(
                value.get("commit_latency_ms"), "commit_latency_ms"
            ),
            preemption_resume_ms=_optional_nonnegative(
                value.get("preemption_resume_ms"), "preemption_resume_ms"
            ),
            cross_window_speakers=tuple(
                str(item)
                for item in _list(value.get("cross_window_speakers", []), "cross_window_speakers")
            ),
            provenance_coverage=_probability(
                value.get("provenance_coverage", 0), "provenance_coverage"
            ),
        )


@dataclass(frozen=True, slots=True)
class GoldCoverage:
    classroom_seconds: dict[str, float]
    ibus_items: dict[str, int]
    ibus_long_items: dict[str, int]
    splits: dict[str, int]

    @classmethod
    def inspect(cls, records: tuple[GoldRecord, ...]) -> GoldCoverage:
        classroom = {language: 0.0 for language in ("zh", "ja", "en")}
        ibus = {language: 0 for language in classroom}
        long_items = {language: 0 for language in classroom}
        splits = {split: 0 for split in ("train", "validation", "test")}
        for item in records:
            splits[item.split] += 1
            if item.scenario == "classroom":
                classroom[item.language] += item.duration_seconds
            else:
                ibus[item.language] += 1
                if item.duration_seconds > 60:
                    long_items[item.language] += 1
        return cls(classroom, ibus, long_items, splits)

    def require(self, *, production: bool) -> None:
        classroom_minimum = 1800 if production else 300
        ibus_minimum = 50 if production else 1
        errors: list[str] = []
        for language in ("zh", "ja", "en"):
            if self.classroom_seconds[language] < classroom_minimum:
                errors.append(f"{language} classroom < {classroom_minimum}s")
            if self.ibus_items[language] < ibus_minimum:
                errors.append(f"{language} IBus items < {ibus_minimum}")
            if self.ibus_long_items[language] < 1:
                errors.append(f"{language} IBus lacks a >60s item")
        if any(self.splits[split] == 0 for split in self.splits):
            errors.append("train/validation/test splits must all be non-empty")
        if errors:
            raise ValueError("gold coverage gate failed: " + "; ".join(errors))


def load_gold_manifest(path: Path, *, verify_audio: bool = True) -> tuple[GoldRecord, ...]:
    records = tuple(GoldRecord.from_dict(item) for item in _read_jsonl(path))
    identifiers = [item.item_id for item in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("gold manifest item_id values must be unique")
    splits_by_audio: dict[str, set[str]] = {}
    for item in records:
        splits_by_audio.setdefault(item.audio, set()).add(item.split)
    if leaked := sorted(audio for audio, splits in splits_by_audio.items() if len(splits) > 1):
        raise ValueError(f"gold audio crosses train/validation/test splits: {leaked}")
    if verify_audio:
        root = path.parent.resolve()
        maximum_by_audio: dict[str, int] = {}
        for item in records:
            maximum_by_audio[item.audio] = max(maximum_by_audio.get(item.audio, 0), item.end_sample)
        for relative, required_samples in maximum_by_audio.items():
            unresolved = root / relative
            if _contains_symlink(root, unresolved):
                raise ValueError(f"gold audio is missing or unsafe: {relative}")
            audio = unresolved.resolve()
            if (
                root not in audio.parents
                or not audio.is_file()
                or audio.suffix.casefold() != ".wav"
            ):
                raise ValueError(f"gold audio is missing, unsafe, or not canonical WAV: {relative}")
            try:
                with wave.open(str(audio), "rb") as reader:
                    canonical = (
                        reader.getframerate() == 16_000
                        and reader.getnchannels() == 1
                        and reader.getsampwidth() == 2
                        and reader.getcomptype() == "NONE"
                    )
                    available_samples = reader.getnframes()
            except (OSError, EOFError, wave.Error) as exc:
                raise ValueError(f"gold audio is not a readable canonical WAV: {relative}") from exc
            if not canonical or required_samples > available_samples:
                raise ValueError(f"gold audio format or sample range is invalid: {relative}")
    return records


def load_predictions(path: Path) -> tuple[BenchmarkPrediction, ...]:
    predictions = tuple(BenchmarkPrediction.from_dict(item) for item in _read_jsonl(path))
    keys = [(item.item_id, item.model_id, item.model_revision) for item in predictions]
    if len(keys) != len(set(keys)):
        raise ValueError("prediction item/model/revision rows must be unique")
    return predictions


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"JSONL file is missing or unsafe: {path}")
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("JSONL file exceeds 128 MiB")
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL line {line_number} must be an object")
        result.append(value)
    if not result:
        raise ValueError("JSONL file must contain at least one item")
    return result


def _contains_symlink(root: Path, candidate: Path) -> bool:
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise ValueError(f"{name} must be finite")
    return result


def _nonnegative(value: object, name: str) -> float:
    result = _number(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _probability(value: object, name: str) -> float:
    result = _number(value, name)
    if not 0 <= result <= 1:
        raise ValueError(f"{name} must be between zero and one")
    return result


def _optional_probability(value: object, name: str) -> float | None:
    return None if value is None else _probability(value, name)


def _optional_nonnegative(value: object, name: str) -> float | None:
    return None if value is None else _nonnegative(value, name)


def _span(value: Any, lower: int, upper: int, name: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} entries must be [start_sample, end_sample]")
    start, end = (_integer(item, name) for item in value)
    if start < lower or end <= start or end > upper:
        raise ValueError(f"{name} entry is outside the item range")
    return start, end


def _prediction_span(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} entries must be [start_sample, end_sample]")
    start, end = (_integer(item, name) for item in value)
    if start < 0 or end <= start:
        raise ValueError(f"{name} entry must be a non-empty sample span")
    return start, end


def _word(value: Any, lower: int, upper: int) -> WordAnnotation:
    result = _prediction_word(value)
    if result.start_sample < lower or result.end_sample > upper:
        raise ValueError("gold word is outside the item range")
    return result


def _prediction_word(value: Any) -> WordAnnotation:
    if not isinstance(value, dict):
        raise ValueError("word annotation must be an object")
    start = _integer(value.get("start_sample"), "word.start_sample")
    end = _integer(value.get("end_sample"), "word.end_sample")
    text = str(value.get("text", ""))
    if start < 0 or end <= start or not text:
        raise ValueError("word annotation must have text and a positive range")
    speaker = value.get("speaker")
    return WordAnnotation(text, start, end, str(speaker) if speaker is not None else None)


def _speaker(value: Any, lower: int, upper: int) -> SpeakerAnnotation:
    result = _prediction_speaker(value)
    if result.start_sample < lower or result.end_sample > upper:
        raise ValueError("gold speaker span is outside the item range")
    return result


def _prediction_speaker(value: Any) -> SpeakerAnnotation:
    if not isinstance(value, dict):
        raise ValueError("speaker annotation must be an object")
    start = _integer(value.get("start_sample"), "speaker.start_sample")
    end = _integer(value.get("end_sample"), "speaker.end_sample")
    speaker = str(value.get("speaker", ""))
    if start < 0 or end <= start or not speaker:
        raise ValueError("speaker annotation must have a label and positive range")
    return SpeakerAnnotation(speaker, start, end)
