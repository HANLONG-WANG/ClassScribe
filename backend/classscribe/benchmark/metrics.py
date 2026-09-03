"""Text, punctuation, safety, timeline, speaker, and performance metrics."""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from itertools import pairwise

from classscribe.benchmark.models import SpeakerAnnotation, WordAnnotation
from classscribe.benchmark.normalizers import comparison_units, error_rate

TimedWord = WordAnnotation
SpeakerSpan = SpeakerAnnotation


@dataclass(frozen=True, slots=True)
class PRF:
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True, slots=True)
class PerformanceObservation:
    audio_seconds: float
    runtime_seconds: float
    peak_vram_mb: float = 0.0
    peak_ram_mb: float = 0.0
    interim_latency_ms: float | None = None
    commit_latency_ms: float | None = None
    load_seconds: float = 0.0
    preemption_resume_ms: float | None = None


def score_text(
    reference: str,
    hypothesis: str,
    language: str,
    *,
    terms: tuple[str, ...] = (),
    term_vocabulary: tuple[str, ...] = (),
    entities: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, object]:
    raw_reference = comparison_units(reference, language, normalized=False)
    raw_hypothesis = comparison_units(hypothesis, language, normalized=False)
    normalized_reference = comparison_units(reference, language, normalized=True)
    normalized_hypothesis = comparison_units(hypothesis, language, normalized=True)
    label = "wer" if language == "en" else "cer"
    result: dict[str, object] = {
        f"raw_{label}": error_rate(raw_reference, raw_hypothesis),
        f"normalized_{label}": error_rate(normalized_reference, normalized_hypothesis),
        "raw_policy": "readable_exact_words" if language == "en" else "exact_nonspace_chars",
        "normalized_policy": (
            "nfkc_lowercase_no_punctuation_words"
            if language == "en"
            else "nfkc_casefold_no_space_or_punctuation_chars"
        ),
        "term": asdict(_term_score(reference, hypothesis, terms, term_vocabulary)),
        "punctuation": punctuation_metrics(reference, hypothesis),
        "paired_quote_bracket_errors": paired_quote_bracket_errors(hypothesis),
    }
    if language == "en":
        result["capitalization_f1"] = capitalization_f1(reference, hypothesis)
    entity_values = entities or {}
    categories = {
        "number": entity_values.get("number", entity_values.get("numbers", ())),
        "unit": entity_values.get("unit", entity_values.get("units", ())),
        "negation": entity_values.get("negation", entity_values.get("negations", ())),
        "name": entity_values.get("name", entity_values.get("names", ())),
    }
    for category, values in categories.items():
        result[f"{category}_accuracy"] = _entity_recall(hypothesis, values)
    return result


def punctuation_metrics(reference: str, hypothesis: str) -> dict[str, float]:
    reference_events = Counter(_punctuation_events(reference))
    hypothesis_events = Counter(_punctuation_events(hypothesis))
    true_positive = sum((reference_events & hypothesis_events).values())
    predicted = sum(hypothesis_events.values())
    expected = sum(reference_events.values())
    prf = _prf(true_positive, predicted - true_positive, expected - true_positive)
    reference_sequence = tuple(item[1] for item in _punctuation_events(reference))
    hypothesis_sequence = tuple(item[1] for item in _punctuation_events(hypothesis))
    per = error_rate(reference_sequence, hypothesis_sequence)
    return {**asdict(prf), "per": per}


def capitalization_f1(reference: str, hypothesis: str) -> float:
    reference_letters = [item for item in reference if item.isalpha()]
    hypothesis_letters = [item for item in hypothesis if item.isalpha()]
    limit = min(len(reference_letters), len(hypothesis_letters))
    true_positive = false_positive = false_negative = 0
    for index in range(limit):
        expected = reference_letters[index].isupper()
        predicted = hypothesis_letters[index].isupper()
        true_positive += int(expected and predicted)
        false_positive += int(predicted and not expected)
        false_negative += int(expected and not predicted)
    false_negative += sum(item.isupper() for item in reference_letters[limit:])
    false_positive += sum(item.isupper() for item in hypothesis_letters[limit:])
    return _prf(true_positive, false_positive, false_negative).f1


def paired_quote_bracket_errors(text: str) -> int:
    pairs = {
        "(": ")",
        "[": "]",
        "{": "}",
        "\u300c": "\u300d",
        "\u300e": "\u300f",
        "\uff08": "\uff09",
    }
    closing = set(pairs.values())
    stack: list[str] = []
    errors = 0
    for character in text:
        if character in pairs:
            stack.append(pairs[character])
        elif character in closing:
            if stack and stack[-1] == character:
                stack.pop()
            else:
                errors += 1
    return errors + len(stack)


def score_safety(
    reference: str,
    hypothesis: str,
    *,
    duration_seconds: float,
    tags: tuple[str, ...] = (),
    reference_sentences: tuple[str, ...] = (),
    voiced_spans: tuple[tuple[int, int], ...] = (),
    predicted_spans: tuple[tuple[int, int], ...] = (),
) -> dict[str, float | int]:
    hours = max(duration_seconds / 3600, 1 / 3600)
    silence_hallucinations = int("silence" in tags and bool(_lexical(hypothesis)))
    loop_triggers = int(_has_loop(hypothesis))
    sentence_values = reference_sentences or _sentences(reference)
    omitted = sum(not _sentence_present(item, hypothesis) for item in sentence_values)
    coverage = _coverage(voiced_spans, predicted_spans)
    invalid = sum(_invalid_character(item) for item in hypothesis)
    return {
        "omission_rate": omitted / max(1, len(sentence_values)),
        "silence_hallucinations_per_hour": silence_hallucinations / hours,
        "loop_triggers_per_hour": loop_triggers / hours,
        "voiced_coverage": coverage,
        "invalid_character_rate": invalid / max(1, len(hypothesis)),
    }


def score_timeline(
    reference_words: tuple[TimedWord, ...],
    predicted_words: tuple[TimedWord, ...],
    *,
    reference_sentence_boundaries: tuple[int, ...] = (),
    predicted_sentence_boundaries: tuple[int, ...] = (),
    start_sample: int = 0,
    duration_samples: int,
) -> dict[str, float | int]:
    structural_errors = _timeline_errors(predicted_words, start_sample, duration_samples)
    pairs = _aligned_words(reference_words, predicted_words)
    errors_ms = [
        (abs(left.start_sample - right.start_sample) + abs(left.end_sample - right.end_sample))
        / 2
        / 16
        for left, right in pairs
    ]
    return {
        "structural_errors": structural_errors,
        "word_boundary_mae_ms": statistics.fmean(errors_ms) if errors_ms else 0.0,
        "sentence_boundary_f1_250ms": _boundary_f1(
            reference_sentence_boundaries, predicted_sentence_boundaries, 4_000
        ),
        "sentence_boundary_f1_500ms": _boundary_f1(
            reference_sentence_boundaries, predicted_sentence_boundaries, 8_000
        ),
    }


def score_speakers(
    reference: tuple[SpeakerSpan, ...],
    hypothesis: tuple[SpeakerSpan, ...],
    *,
    reference_words: tuple[TimedWord, ...] = (),
    predicted_words: tuple[TimedWord, ...] = (),
    language: str = "en",
    cross_window_speakers: tuple[str, ...] = (),
) -> dict[str, float | int]:
    mapping = _speaker_mapping(reference, hypothesis)
    boundaries = sorted(
        {
            point
            for span in (*reference, *hypothesis)
            for point in (span.start_sample, span.end_sample)
        }
    )
    missed = false_alarm = confusion = reference_total = 0
    for start, end in pairwise(boundaries):
        midpoint = (start + end) // 2
        expected = _active_speakers(reference, midpoint)
        predicted = {mapping.get(item, item) for item in _active_speakers(hypothesis, midpoint)}
        width = end - start
        reference_total += width * len(expected)
        correct = len(expected & predicted)
        missed += width * max(0, len(expected) - len(predicted))
        false_alarm += width * max(0, len(predicted) - len(expected))
        confusion += width * (min(len(expected), len(predicted)) - correct)
    der = (missed + false_alarm + confusion) / max(1, reference_total)
    jer_values: list[float] = []
    for speaker in sorted({item.speaker for item in reference}):
        reference_intervals = tuple(
            (item.start_sample, item.end_sample) for item in reference if item.speaker == speaker
        )
        predicted_intervals = tuple(
            (item.start_sample, item.end_sample)
            for item in hypothesis
            if mapping.get(item.speaker, item.speaker) == speaker
        )
        intersection = _intersection_duration(reference_intervals, predicted_intervals)
        union = _union_duration((*reference_intervals, *predicted_intervals))
        jer_values.append(1 - intersection / max(1, union))
    attributed_reference = tuple(f"[{item.speaker or '?'}]{item.text}" for item in reference_words)
    attributed_hypothesis = tuple(
        f"[{mapping.get(item.speaker or '?', item.speaker or '?')}]{item.text}"
        for item in predicted_words
    )
    return {
        "der": der,
        "jer": statistics.fmean(jer_values) if jer_values else 0.0,
        "speaker_attributed_error_rate": error_rate(
            comparison_units(" ".join(attributed_reference), language, normalized=True),
            comparison_units(" ".join(attributed_hypothesis), language, normalized=True),
        ),
        "cross_window_speaker_switches": sum(
            left != right for left, right in pairwise(cross_window_speakers)
        ),
    }


def aggregate_performance(
    observations: tuple[PerformanceObservation, ...],
) -> dict[str, float | None]:
    if not observations:
        raise ValueError("performance aggregation requires observations")
    audio_seconds = sum(item.audio_seconds for item in observations)
    runtime_seconds = sum(item.runtime_seconds for item in observations)
    interim = tuple(
        item.interim_latency_ms for item in observations if item.interim_latency_ms is not None
    )
    commit = tuple(
        item.commit_latency_ms for item in observations if item.commit_latency_ms is not None
    )
    resume = tuple(
        item.preemption_resume_ms for item in observations if item.preemption_resume_ms is not None
    )
    return {
        "rtf": runtime_seconds / max(audio_seconds, 1e-9),
        "audio_seconds": audio_seconds,
        "total_runtime_seconds": runtime_seconds,
        "projected_90m_seconds": runtime_seconds / max(audio_seconds, 1e-9) * 5400,
        "peak_vram_mb": max(item.peak_vram_mb for item in observations),
        "peak_ram_mb": max(item.peak_ram_mb for item in observations),
        "model_load_seconds": max(item.load_seconds for item in observations),
        "interim_latency_p50_ms": _percentile(interim, 0.5),
        "interim_latency_p95_ms": _percentile(interim, 0.95),
        "commit_latency_p50_ms": _percentile(commit, 0.5),
        "commit_latency_p95_ms": _percentile(commit, 0.95),
        "preemption_resume_p50_ms": _percentile(resume, 0.5),
        "preemption_resume_p95_ms": _percentile(resume, 0.95),
        "runtime_observation_coverage": sum(item.runtime_seconds > 0 for item in observations)
        / len(observations),
        "interim_observation_coverage": len(interim) / len(observations),
        "commit_observation_coverage": len(commit) / len(observations),
        "preemption_observation_coverage": len(resume) / len(observations),
    }


def _prf(true_positive: int, false_positive: int, false_negative: int) -> PRF:
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return PRF(precision, recall, f1)


def _punctuation_events(text: str) -> tuple[tuple[int, str], ...]:
    lexical_index = 0
    events: list[tuple[int, str]] = []
    for character in text:
        if unicodedata.category(character).startswith("P"):
            events.append((lexical_index, character))
        elif not character.isspace():
            lexical_index += 1
    return tuple(events)


def _term_score(
    reference: str,
    hypothesis: str,
    expected: tuple[str, ...],
    vocabulary: tuple[str, ...],
) -> PRF:
    universe = tuple(dict.fromkeys((*expected, *vocabulary)))
    expected_present = {item for item in universe if item and item in reference}
    predicted_present = {item for item in universe if item and item in hypothesis}
    return _prf(
        len(expected_present & predicted_present),
        len(predicted_present - expected_present),
        len(expected_present - predicted_present),
    )


def _entity_recall(hypothesis: str, values: tuple[str, ...]) -> float | None:
    expected = tuple(item for item in values if item)
    if not expected:
        return None
    return sum(item in hypothesis for item in expected) / len(expected)


def _lexical(text: str) -> str:
    return "".join(
        item
        for item in text
        if not item.isspace() and not unicodedata.category(item).startswith("P")
    )


def _has_loop(text: str) -> bool:
    units = tuple(_lexical(text))
    for size in range(3, min(10, len(units) // 4) + 1):
        for start in range(len(units) - size * 4 + 1):
            piece = units[start : start + size]
            if all(
                units[start + index * size : start + (index + 1) * size] == piece
                for index in range(1, 4)
            ):
                return True
    return False


def _sentences(text: str) -> tuple[str, ...]:
    pieces: list[str] = []
    current = ""
    for character in text:
        current += character
        if character in ".?!\u3002\uff01\uff1f":
            if _lexical(current):
                pieces.append(current)
            current = ""
    if _lexical(current):
        pieces.append(current)
    return tuple(pieces)


def _sentence_present(sentence: str, hypothesis: str) -> bool:
    source = _lexical(sentence).casefold()
    target = _lexical(hypothesis).casefold()
    if not source:
        return True
    if source in target:
        return True
    return 1 - error_rate(tuple(source), tuple(target)) >= 0.9


def _coverage(
    reference: tuple[tuple[int, int], ...], predicted: tuple[tuple[int, int], ...]
) -> float:
    total = _union_duration(reference)
    return _intersection_duration(reference, predicted) / max(1, total)


def _invalid_character(character: str) -> bool:
    category = unicodedata.category(character)
    return ord(character) == 0xFFFD or (category in {"Cc", "Cs", "Cn"} and character not in "\n\t")


def _timeline_errors(words: tuple[TimedWord, ...], start_sample: int, duration_samples: int) -> int:
    errors = 0
    previous_end = start_sample
    for item in words:
        invalid = (
            item.start_sample < start_sample
            or item.end_sample <= item.start_sample
            or item.end_sample > duration_samples
            or item.start_sample < previous_end
        )
        errors += int(invalid)
        previous_end = max(previous_end, item.end_sample)
    return errors


def _aligned_words(
    reference: tuple[TimedWord, ...], hypothesis: tuple[TimedWord, ...]
) -> tuple[tuple[TimedWord, TimedWord], ...]:
    by_text: dict[str, list[TimedWord]] = defaultdict(list)
    for item in hypothesis:
        by_text[item.text.casefold()].append(item)
    result: list[tuple[TimedWord, TimedWord]] = []
    consumed: Counter[str] = Counter()
    for item in reference:
        key = item.text.casefold()
        index = consumed[key]
        if index < len(by_text[key]):
            result.append((item, by_text[key][index]))
            consumed[key] += 1
    return tuple(result)


def _boundary_f1(reference: tuple[int, ...], hypothesis: tuple[int, ...], tolerance: int) -> float:
    remaining = list(hypothesis)
    true_positive = 0
    for expected in reference:
        matches = [
            (abs(expected - predicted), index)
            for index, predicted in enumerate(remaining)
            if abs(expected - predicted) <= tolerance
        ]
        if matches:
            _, index = min(matches)
            remaining.pop(index)
            true_positive += 1
    return _prf(true_positive, len(remaining), len(reference) - true_positive).f1


def _speaker_mapping(
    reference: tuple[SpeakerSpan, ...], hypothesis: tuple[SpeakerSpan, ...]
) -> dict[str, str]:
    overlap: Counter[tuple[str, str]] = Counter()
    for predicted in hypothesis:
        for expected in reference:
            width = max(
                0,
                min(predicted.end_sample, expected.end_sample)
                - max(predicted.start_sample, expected.start_sample),
            )
            overlap[(predicted.speaker, expected.speaker)] += width
    used_predicted: set[str] = set()
    used_expected: set[str] = set()
    mapping: dict[str, str] = {}
    for (predicted_label, expected_label), _width in sorted(
        overlap.items(), key=lambda item: (-item[1], item[0])
    ):
        if predicted_label not in used_predicted and expected_label not in used_expected:
            mapping[predicted_label] = expected_label
            used_predicted.add(predicted_label)
            used_expected.add(expected_label)
    return mapping


def _active_speakers(spans: tuple[SpeakerSpan, ...], sample: int) -> set[str]:
    return {item.speaker for item in spans if item.start_sample <= sample < item.end_sample}


def _union_duration(spans: tuple[tuple[int, int], ...]) -> int:
    total = 0
    current_start = current_end = -1
    for start, end in sorted(spans):
        if start > current_end:
            total += max(0, current_end - current_start)
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    return total + max(0, current_end - current_start)


def _intersection_duration(
    left: tuple[tuple[int, int], ...], right: tuple[tuple[int, int], ...]
) -> int:
    intersections: list[tuple[int, int]] = []
    for left_start, left_end in left:
        for right_start, right_end in right:
            start = max(left_start, right_start)
            end = min(left_end, right_end)
            if end > start:
                intersections.append((start, end))
    return _union_duration(tuple(intersections))


def _percentile(values: tuple[float, ...], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
