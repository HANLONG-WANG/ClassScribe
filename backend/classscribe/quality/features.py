"""Four-domain ASR feature extraction and hard candidate validity decisions."""

from __future__ import annotations

import re
import unicodedata
from itertools import pairwise
from statistics import fmean

from classscribe.asr.models import ASRCandidateEvidence
from classscribe.quality.loop import inspect_decode_loop
from classscribe.quality.models import (
    HARD_REJECTION_ISSUES,
    QualityContext,
    QualityIssue,
    QualityReport,
    RetryDirective,
)
from classscribe.quality.text import normalized_edit_distance, tokenize_for_language
from classscribe.timeline import SAMPLE_RATE, AudioSpan

_PUNCTUATION = frozenset(
    '.,!?;:。、\uff01\uff1f\uff1b\uff1a「」『』\uff08\uff09()[]{}“”\u2018\u2019"'
)
_FINAL_PUNCTUATION = frozenset(".!?。\uff01\uff1f")
_PAIRINGS = (
    ("(", ")"),
    ("\uff08", "\uff09"),
    ("[", "]"),
    ("{", "}"),
    ("「", "」"),
    ("『", "』"),
)
_NUMERIC_ANOMALY = re.compile(
    r"(?:\d{8,}|(?:\b(?:kg|mg|ml|km|cm|mm|hz|khz|mhz|gb|mb)\b\s*){3,})",
    re.IGNORECASE,
)

_ISSUE_PENALTIES: dict[QualityIssue, float] = {
    QualityIssue.EMPTY_ON_SPEECH: 0.35,
    QualityIssue.SILENCE_HALLUCINATION: 1.0,
    QualityIssue.REPEATED_NGRAM: 1.0,
    QualityIssue.SHORTEST_LOOP: 1.0,
    QualityIssue.OUTPUT_TOO_DENSE: 1.0,
    QualityIssue.PREFIX_STAGNATION: 1.0,
    QualityIssue.REPETITION_COMPRESSION: 0.3,
    QualityIssue.REPEATED_SENTENCE: 0.4,
    QualityIssue.REPLACEMENT_CHARACTER: 1.0,
    QualityIssue.DOUBLE_QUESTION: 0.8,
    QualityIssue.ILLEGAL_CHARACTER: 1.0,
    QualityIssue.SCRIPT_MISMATCH: 1.0,
    QualityIssue.PUNCTUATION_DENSITY: 0.2,
    QualityIssue.UNPAIRED_PUNCTUATION: 0.15,
    QualityIssue.MISSING_SENTENCE_END: 0.05,
    QualityIssue.NUMERIC_UNIT_ANOMALY: 0.25,
    QualityIssue.LOW_TIMESTAMP_COVERAGE: 0.25,
    QualityIssue.NON_MONOTONIC_TIME: 1.0,
    QualityIssue.TIME_OUT_OF_RANGE: 1.0,
    QualityIssue.EXCESSIVE_TIME_OVERLAP: 1.0,
    QualityIssue.LATE_SPEECH_UNCOVERED: 0.35,
    QualityIssue.LENGTH_VOICE_MISMATCH: 0.35,
    QualityIssue.SUSPECTED_TERMINOLOGY_ERROR: 0.2,
    QualityIssue.STRUCTURE_DIVERGENCE: 0.25,
    QualityIssue.LOW_SNR: 0.2,
    QualityIssue.FAR_FIELD: 0.2,
    QualityIssue.LOW_CALIBRATED_QUALITY: 0.35,
}


class QualityFeatureExtractor:
    """Produce explainable features; raw model confidence is never cross-model calibrated."""

    def inspect(
        self,
        candidate_id: str,
        candidate: ASRCandidateEvidence,
        context: QualityContext,
    ) -> QualityReport:
        if candidate.audio_span != context.requested_span:
            raise ValueError("quality context and candidate must use the same canonical range")
        voiced_seconds = context.voiced_samples / SAMPLE_RATE
        loop = inspect_decode_loop(
            candidate.normalized_text,
            candidate.language_requested,
            voiced_seconds=voiced_seconds,
            prefix_snapshots=context.prefix_snapshots,
        )
        issues = list(loop.issues)
        acoustic = self._acoustic_model_features(candidate, context)
        text, text_issues = self._text_features(candidate, voiced_seconds)
        timing, timing_issues = self._timing_features(candidate, context)
        multi_model, multi_model_issues = self._multi_model_features(candidate, context)
        issues.extend(text_issues)
        issues.extend(timing_issues)
        issues.extend(multi_model_issues)
        issues.extend(self._acoustic_issues(candidate, context))
        text.update(loop.metrics)
        unique_issues = tuple(dict.fromkeys(issues))
        score = max(0.0, 1.0 - sum(_ISSUE_PENALTIES[issue] for issue in unique_issues))
        if context.calibrated_candidate_quality is not None:
            score = min(score, context.calibrated_candidate_quality)
            if context.calibrated_candidate_quality < 0.55:
                unique_issues = (*unique_issues, QualityIssue.LOW_CALIBRATED_QUALITY)
        valid = not any(issue in HARD_REJECTION_ISSUES for issue in unique_issues)
        retry = self._retry(candidate.audio_span, unique_issues) if not valid else None
        return QualityReport(
            candidate_id=candidate_id,
            model_id=candidate.model_id,
            valid_for_consensus=valid,
            quality_gate_score=round(score, 6),
            score_is_calibrated_probability=False,
            issues=unique_issues,
            acoustic_model_features=acoustic,
            text_features=text,
            timing_features=timing,
            multi_model_features=multi_model,
            retry=retry,
        )

    @staticmethod
    def _acoustic_model_features(
        candidate: ASRCandidateEvidence,
        context: QualityContext,
    ) -> dict[str, int | float | bool | str | None]:
        raw_token_confidences = tuple(
            token.confidence_raw for token in candidate.tokens if token.confidence_raw is not None
        )
        return {
            "confidence_raw_model_local": candidate.confidence_raw,
            "mean_token_confidence_raw_model_local": (
                round(fmean(raw_token_confidences), 6) if raw_token_confidences else None
            ),
            "mean_token_logprob_model_local": (
                round(fmean(context.token_logprobs), 6) if context.token_logprobs else None
            ),
            "mean_ctc_posterior_model_local": (
                round(fmean(context.ctc_posteriors), 6) if context.ctc_posteriors else None
            ),
            "mean_rnnt_posterior_model_local": (
                round(fmean(context.rnnt_posteriors), 6) if context.rnnt_posteriors else None
            ),
            "raw_signals_are_cross_model_probability": False,
            "calibrated_candidate_quality": context.calibrated_candidate_quality,
            "no_speech_probability": context.no_speech_probability,
            "vad_speech_ratio": context.vad_speech_ratio,
            "snr_db": context.snr_db,
            "clipping_ratio": context.clipping_ratio,
            "volume_rms": context.volume_rms,
            "far_field": context.far_field,
        }

    @staticmethod
    def _acoustic_issues(
        candidate: ASRCandidateEvidence,
        context: QualityContext,
    ) -> tuple[QualityIssue, ...]:
        issues: list[QualityIssue] = []
        visible_length = sum(not character.isspace() for character in candidate.normalized_text)
        if context.voiced_samples >= SAMPLE_RATE and visible_length == 0:
            issues.append(QualityIssue.EMPTY_ON_SPEECH)
        if (
            visible_length >= 8
            and context.vad_speech_ratio < 0.05
            and (context.no_speech_probability is None or context.no_speech_probability >= 0.8)
        ):
            issues.append(QualityIssue.SILENCE_HALLUCINATION)
        if context.snr_db is not None and context.snr_db < 10:
            issues.append(QualityIssue.LOW_SNR)
        if context.far_field:
            issues.append(QualityIssue.FAR_FIELD)
        return tuple(issues)

    @staticmethod
    def _text_features(
        candidate: ASRCandidateEvidence,
        voiced_seconds: float,
    ) -> tuple[dict[str, int | float | bool | str | None], tuple[QualityIssue, ...]]:
        text = candidate.normalized_text
        language = candidate.language_requested
        characters = [character for character in text if not character.isspace()]
        word_count = len(tokenize_for_language(text, language))
        punctuation_count = sum(character in _PUNCTUATION for character in characters)
        punctuation_density = punctuation_count / max(len(characters), 1)
        cased_letters = [character for character in text if character.isalpha()]
        uppercase_ratio = sum(character.isupper() for character in cased_letters) / max(
            len(cased_letters), 1
        )
        illegal_controls = sum(
            unicodedata.category(character) == "Cc" and character not in "\n\t"
            for character in text
        )
        script_mismatch, script_detail = _script_mismatch(text, language)
        paired = all(text.count(left) == text.count(right) for left, right in _PAIRINGS)
        issues: list[QualityIssue] = []
        if "�" in text:
            issues.append(QualityIssue.REPLACEMENT_CHARACTER)
        if "??" in text:
            issues.append(QualityIssue.DOUBLE_QUESTION)
        if illegal_controls:
            issues.append(QualityIssue.ILLEGAL_CHARACTER)
        if script_mismatch:
            issues.append(QualityIssue.SCRIPT_MISMATCH)
        if len(characters) >= 8 and punctuation_density > 0.35:
            issues.append(QualityIssue.PUNCTUATION_DENSITY)
        if not paired:
            issues.append(QualityIssue.UNPAIRED_PUNCTUATION)
        if len(characters) >= 12 and characters[-1] not in _FINAL_PUNCTUATION:
            issues.append(QualityIssue.MISSING_SENTENCE_END)
        if _NUMERIC_ANOMALY.search(text):
            issues.append(QualityIssue.NUMERIC_UNIT_ANOMALY)
        words_per_second = word_count / max(voiced_seconds, 0.1)
        characters_per_second = len(characters) / max(voiced_seconds, 0.1)
        if voiced_seconds >= 4 and text and characters_per_second < 0.25:
            issues.append(QualityIssue.LENGTH_VOICE_MISMATCH)
        return (
            {
                "visible_character_count": len(characters),
                "token_or_word_count": word_count,
                "characters_per_voiced_second": round(characters_per_second, 6),
                "tokens_per_voiced_second": round(words_per_second, 6),
                "punctuation_density": round(punctuation_density, 6),
                "punctuation_sequence": "".join(
                    character for character in text if character in _PUNCTUATION
                ),
                "uppercase_ratio": round(uppercase_ratio, 6),
                "case_scored_separately_from_lexical_content": language == "en",
                "has_sentence_end": bool(characters and characters[-1] in _FINAL_PUNCTUATION),
                "paired_parentheses_and_quotes": paired,
                "illegal_control_count": illegal_controls,
                "script_detail": script_detail,
                "numeric_unit_anomaly": bool(_NUMERIC_ANOMALY.search(text)),
            },
            tuple(issues),
        )

    @staticmethod
    def _timing_features(
        candidate: ASRCandidateEvidence,
        context: QualityContext,
    ) -> tuple[dict[str, int | float | bool | str | None], tuple[QualityIssue, ...]]:
        tokens = candidate.tokens
        monotonic = all(
            right.span.start_sample >= left.span.start_sample for left, right in pairwise(tokens)
        )
        in_range = all(_contains(context.requested_span, token.span) for token in tokens)
        overlap_samples = sum(
            max(0, left.span.end_sample - right.span.start_sample)
            for left, right in pairwise(tokens)
        )
        token_samples = sum(token.span.duration_samples for token in tokens)
        excessive_overlap = bool(tokens) and overlap_samples / max(token_samples, 1) > 0.25
        coverage_samples = _covered_samples(
            tuple(token.span for token in tokens), context.voiced_spans
        )
        coverage = coverage_samples / max(context.voiced_samples, 1)
        midpoint = (context.requested_span.start_sample + context.requested_span.end_sample) // 2
        late_speech = any(span.end_sample > midpoint for span in context.voiced_spans)
        last_token_end = max((token.span.end_sample for token in tokens), default=None)
        late_uncovered = bool(
            late_speech
            and last_token_end is not None
            and last_token_end < midpoint
            and context.voiced_samples >= 2 * SAMPLE_RATE
        )
        issues: list[QualityIssue] = []
        if not monotonic:
            issues.append(QualityIssue.NON_MONOTONIC_TIME)
        if not in_range:
            issues.append(QualityIssue.TIME_OUT_OF_RANGE)
        if excessive_overlap:
            issues.append(QualityIssue.EXCESSIVE_TIME_OVERLAP)
        if context.voiced_samples >= 2 * SAMPLE_RATE and coverage < 0.45:
            issues.append(QualityIssue.LOW_TIMESTAMP_COVERAGE)
        if late_uncovered:
            issues.append(QualityIssue.LATE_SPEECH_UNCOVERED)
        return (
            {
                "has_native_word_timing": bool(tokens),
                "word_timestamp_count": len(tokens),
                "timestamps_monotonic": monotonic,
                "timestamps_inside_request": in_range,
                "adjacent_overlap_samples": overlap_samples,
                "timestamp_voiced_coverage": round(coverage, 6),
                "speech_after_midpoint": late_speech,
                "last_word_end_sample": last_token_end,
                "late_speech_uncovered": late_uncovered,
            },
            tuple(issues),
        )

    @staticmethod
    def _multi_model_features(
        candidate: ASRCandidateEvidence,
        context: QualityContext,
    ) -> tuple[dict[str, int | float | bool | str | None], tuple[QualityIssue, ...]]:
        structure_tokens = tokenize_for_language(
            context.structure_text, candidate.language_requested
        )
        candidate_tokens = tokenize_for_language(
            candidate.normalized_text, candidate.language_requested
        )
        distance = (
            normalized_edit_distance(candidate_tokens, structure_tokens)
            if structure_tokens
            else None
        )
        issues: list[QualityIssue] = []
        if context.suspected_term_error:
            issues.append(QualityIssue.SUSPECTED_TERMINOLOGY_ERROR)
        if distance is not None and distance > 0.65:
            issues.append(QualityIssue.STRUCTURE_DIVERGENCE)
        return (
            {
                "structure_text_available": bool(structure_tokens),
                "structure_lexical_distance": round(distance, 6) if distance is not None else None,
                "suspected_terminology_error": context.suspected_term_error,
            },
            tuple(issues),
        )

    @staticmethod
    def _retry(span: AudioSpan, issues: tuple[QualityIssue, ...]) -> RetryDirective:
        midpoint = (span.start_sample + span.end_sample) // 2
        return RetryDirective(
            retry_spans=(
                AudioSpan(span.start_sample, midpoint),
                AudioSpan(midpoint, span.end_sample),
            ),
            switch_model=True,
            reject_candidate=True,
            reason=",".join(issue.value for issue in issues if issue in HARD_REJECTION_ISSUES),
        )


def _script_mismatch(text: str, language: str) -> tuple[bool, str]:
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return False, "no_letters"
    han = sum("\u3400" <= character <= "\u9fff" for character in letters)
    kana = sum("\u3040" <= character <= "\u30ff" for character in letters)
    latin = sum("LATIN" in unicodedata.name(character, "") for character in letters)
    if language == "ja":
        mismatch = len(letters) >= 6 and han / len(letters) >= 0.6 and kana == 0
    elif language == "en":
        mismatch = (len(letters) - latin) / len(letters) > 0.2
    elif language == "zh":
        mismatch = kana / len(letters) > 0.35
    else:
        raise ValueError("language must be zh, ja, or en")
    return mismatch, f"han={han},kana={kana},latin={latin},letters={len(letters)}"


def _contains(container: AudioSpan, candidate: AudioSpan) -> bool:
    return (
        container.start_sample <= candidate.start_sample
        and candidate.end_sample <= container.end_sample
    )


def _covered_samples(tokens: tuple[AudioSpan, ...], speech: tuple[AudioSpan, ...]) -> int:
    intersections = [
        AudioSpan(
            max(token.start_sample, voice.start_sample), min(token.end_sample, voice.end_sample)
        )
        for token in tokens
        for voice in speech
        if token.overlaps(voice)
    ]
    if not intersections:
        return 0
    ordered = sorted(intersections, key=lambda item: (item.start_sample, item.end_sample))
    start, end = ordered[0].start_sample, ordered[0].end_sample
    total = 0
    for span in ordered[1:]:
        if span.start_sample <= end:
            end = max(end, span.end_sample)
        else:
            total += end - start
            start, end = span.start_sample, span.end_sample
    return total + end - start
