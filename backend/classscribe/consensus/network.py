"""Time-aligned token confusion network with explicit evidence provenance."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from classscribe.consensus.alignment import align_candidates
from classscribe.consensus.models import (
    AlignedVote,
    ConsensusCandidate,
    ConsensusResult,
    DeterministicTermRule,
    FinalToken,
    ReliabilityProfile,
)
from classscribe.consensus.punctuation import restore_native_punctuation
from classscribe.quality.language import normalize_kana
from classscribe.timeline import AudioSpan, format_sample_timestamp

_INAUDIBLE = {
    "zh": "\uff3b听不清 {start}\u2013{end}\uff3d",
    "ja": "\uff3b聞き取り不明 {start}\u2013{end}\uff3d",
    "en": "[inaudible {start}\u2013{end}]",
}


@dataclass(frozen=True, slots=True)
class ConsensusInputs:
    reliability: ReliabilityProfile
    calibrated_token_confidence: Mapping[tuple[str, int], float]
    voiced_spans: tuple[AudioSpan, ...]
    glossary_terms: frozenset[str] = frozenset()
    terminology_rules: tuple[DeterministicTermRule, ...] = ()

    def __post_init__(self) -> None:
        if any(not 0 <= value <= 1 for value in self.calibrated_token_confidence.values()):
            raise ValueError("calibrated token confidence must be within [0, 1]")


class ConfusionNetwork:
    def resolve(
        self,
        candidates: tuple[ConsensusCandidate, ...],
        *,
        canonical_span: AudioSpan,
        language: str,
        inputs: ConsensusInputs,
    ) -> ConsensusResult:
        if any(candidate.evidence.audio_span != canonical_span for candidate in candidates):
            raise ValueError(
                "all candidates, including rejected evidence, must share one canonical range"
            )
        if any(candidate.evidence.language_requested != language for candidate in candidates):
            raise ValueError("all consensus candidates must use the requested manual language")
        usable = tuple(
            candidate for candidate in candidates if candidate.quality.valid_for_consensus
        )
        columns = align_candidates(usable, canonical_span, language, inputs.reliability)
        if not columns:
            return _inaudible(language, canonical_span, "no_valid_asr_candidate")
        final: list[FinalToken] = []
        column_support: list[float] = []
        for column in columns:
            weighted = [(vote, self._weight(vote, language, inputs)) for vote in column.votes]
            totals: dict[str, float] = defaultdict(float)
            for vote, weight in weighted:
                totals[vote.normalized] += weight
            winner = max(totals, key=lambda token: (totals[token], token))
            winner_votes = tuple(item for item in weighted if item[0].normalized == winner)
            winning_weight = totals[winner]
            total_weight = sum(weight for _vote, weight in weighted)
            support = winning_weight / max(total_weight, 1e-12)
            selected_vote = max(winner_votes, key=lambda item: (item[1], item[0].candidate_id))[0]
            text, rule = _apply_terminology_rule(selected_vote.text, language, inputs)
            provenance = {
                "source_type": "deterministic_terminology_rule" if rule else "asr_candidate",
                "candidate_sources": [
                    {
                        "candidate_id": vote.candidate_id,
                        "model_id": vote.model_id,
                        "model_revision": vote.model_revision,
                        "source_token_index": vote.source_index,
                        "source_start_sample": vote.source_span.start_sample,
                        "source_end_sample": vote.source_span.end_sample,
                        "timing_source": vote.timing_source,
                        "vote_weight": round(weight, 8),
                    }
                    for vote, weight in winner_votes
                ],
                "selected_candidate_id": selected_vote.candidate_id,
                "alignment_column": column.ordinal,
                "absolute_samples": True,
                "rule_id": rule.rule_id if rule else None,
                "canonical_added_only_by_explicit_rule": bool(rule),
                "reliability_source": inputs.reliability.source,
                "reliability_locally_calibrated": inputs.reliability.locally_calibrated,
                "token_confidence_calibrated": (
                    selected_vote.candidate_id,
                    selected_vote.source_index,
                )
                in inputs.calibrated_token_confidence,
            }
            final.append(FinalToken(text, column.span, round(support, 6), provenance))
            column_support.append(support)
        evidence_quality = max(candidate.quality.quality_gate_score for candidate in usable)
        overall = sum(column_support) / len(column_support) * evidence_quality
        if overall < 0.45:
            return self._best_candidate_fallback(
                usable,
                canonical_span,
                language,
                inputs,
                overall,
            )
        text = _join_tokens(tuple(token.text for token in final), language)
        text, punctuation_sources = restore_native_punctuation(text, tuple(final), usable)
        low_confidence = overall < 0.67
        return ConsensusResult(
            language=language,
            canonical_span=canonical_span,
            text=text,
            tokens=tuple(final),
            strategy="time_aligned_confusion_network",
            consensus_support_score=round(overall, 6),
            score_is_calibrated_probability=False,
            low_confidence=low_confidence,
            warnings=("low_consensus_support",) if low_confidence else (),
            punctuation_sources=punctuation_sources,
        )

    @staticmethod
    def _weight(vote: AlignedVote, language: str, inputs: ConsensusInputs) -> float:
        reliability = inputs.reliability.value(vote.model_id)
        calibrated_confidence = inputs.calibrated_token_confidence.get(
            (vote.candidate_id, vote.source_index), 0.5
        )
        acoustic_coverage = _coverage(vote.source_span, inputs.voiced_spans)
        glossary = (
            1.15 if vote.normalized in {item.casefold() for item in inputs.glossary_terms} else 1.0
        )
        language_legality = 1.0 if _legal_token(vote.text, language) else 0.0
        return (
            reliability
            * calibrated_confidence
            * (0.5 + 0.5 * acoustic_coverage)
            * glossary
            * language_legality
        )

    def _best_candidate_fallback(
        self,
        candidates: tuple[ConsensusCandidate, ...],
        canonical_span: AudioSpan,
        language: str,
        inputs: ConsensusInputs,
        support: float,
    ) -> ConsensusResult:
        trusted = max(
            candidates,
            key=lambda candidate: (
                candidate.quality.quality_gate_score
                * inputs.reliability.value(candidate.evidence.model_id),
                candidate.candidate_id,
            ),
        )
        trusted_columns = align_candidates((trusted,), canonical_span, language, inputs.reliability)
        tokens = tuple(
            FinalToken(
                text=column.votes[0].text,
                span=column.span,
                support_score=round(support, 6),
                provenance={
                    "source_type": "asr_candidate",
                    "selected_candidate_id": trusted.candidate_id,
                    "candidate_sources": [
                        {
                            "candidate_id": trusted.candidate_id,
                            "model_id": trusted.evidence.model_id,
                            "model_revision": trusted.evidence.model_revision,
                            "source_token_index": column.votes[0].source_index,
                            "source_start_sample": column.votes[0].source_span.start_sample,
                            "source_end_sample": column.votes[0].source_span.end_sample,
                            "timing_source": column.votes[0].timing_source,
                            "vote_weight": round(
                                self._weight(column.votes[0], language, inputs), 8
                            ),
                        }
                    ],
                    "fallback_reason": "confusion_network_support_too_low",
                    "absolute_samples": True,
                    "reliability_source": inputs.reliability.source,
                    "reliability_locally_calibrated": inputs.reliability.locally_calibrated,
                    "token_confidence_calibrated": (
                        trusted.candidate_id,
                        column.votes[0].source_index,
                    )
                    in inputs.calibrated_token_confidence,
                },
            )
            for column in trusted_columns
        )
        text, punctuation_sources = restore_native_punctuation(
            _join_tokens(tuple(token.text for token in tokens), language), tokens, (trusted,)
        )
        return ConsensusResult(
            language=language,
            canonical_span=canonical_span,
            text=text,
            tokens=tokens,
            strategy="best_candidate_low_confidence",
            consensus_support_score=round(support, 6),
            score_is_calibrated_probability=False,
            low_confidence=True,
            warnings=("no_reliable_consensus_used_best_acoustic_candidate",),
            punctuation_sources=punctuation_sources,
        )


def _apply_terminology_rule(
    text: str,
    language: str,
    inputs: ConsensusInputs,
) -> tuple[str, DeterministicTermRule | None]:
    normalized = normalize_kana(text).casefold()
    for rule in inputs.terminology_rules:
        if rule.language != language:
            continue
        aliases = {normalize_kana(alias).casefold() for alias in rule.aliases}
        if normalized in aliases:
            return rule.canonical, rule
    return text, None


def _coverage(span: AudioSpan, voiced: tuple[AudioSpan, ...]) -> float:
    covered = sum(
        min(span.end_sample, voice.end_sample) - max(span.start_sample, voice.start_sample)
        for voice in voiced
        if span.overlaps(voice)
    )
    return min(1.0, covered / span.duration_samples)


def _legal_token(text: str, language: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return True
    if language == "en":
        return all("LATIN" in _unicode_name(character) for character in letters)
    if language == "ja":
        return all(
            "CJK" in _unicode_name(character)
            or "HIRAGANA" in _unicode_name(character)
            or "KATAKANA" in _unicode_name(character)
            or "LATIN" in _unicode_name(character)
            for character in letters
        )
    return all(
        "CJK" in _unicode_name(character) or "LATIN" in _unicode_name(character)
        for character in letters
    )


def _unicode_name(character: str) -> str:
    return unicodedata.name(character, "")


def _join_tokens(tokens: tuple[str, ...], language: str) -> str:
    if language in {"zh", "ja"}:
        return "".join(tokens)
    return re.sub(r"\s+([.,!?;:])", r"\1", " ".join(tokens)).strip()


def _inaudible(language: str, span: AudioSpan, reason: str) -> ConsensusResult:
    marker = _INAUDIBLE[language].format(
        start=format_sample_timestamp(span.start_sample),
        end=format_sample_timestamp(span.end_sample),
    )
    token = FinalToken(
        marker,
        span,
        0.0,
        {
            "source_type": "inaudible_marker",
            "reason": reason,
            "absolute_samples": True,
            "not_generated_completion": True,
        },
    )
    return ConsensusResult(
        language,
        span,
        marker,
        (token,),
        "inaudible_marker",
        0.0,
        False,
        True,
        (reason,),
    )
