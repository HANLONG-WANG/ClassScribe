"""Typed alignment, vote, provenance, and final-consensus values."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from classscribe.asr.models import ASRCandidateEvidence
from classscribe.quality.models import QualityReport
from classscribe.timeline import AudioSpan


@dataclass(frozen=True, slots=True)
class ConsensusCandidate:
    candidate_id: str
    evidence: ASRCandidateEvidence
    quality: QualityReport

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("consensus candidate ID must not be empty")
        if self.quality.candidate_id != self.candidate_id:
            raise ValueError("candidate and quality report IDs differ")


@dataclass(frozen=True, slots=True)
class ReliabilityProfile:
    values: Mapping[str, float]
    source: str
    locally_calibrated: bool
    scene: str

    def __post_init__(self) -> None:
        if not self.source or not self.scene:
            raise ValueError("reliability source and scene must not be empty")
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in self.values.values()):
            raise ValueError("reliability values must be finite and within [0, 1]")

    def value(self, model_id: str) -> float:
        return self.values.get(model_id, 0.5) if self.locally_calibrated else 0.5


@dataclass(frozen=True, slots=True)
class DeterministicTermRule:
    rule_id: str
    language: str
    canonical: str
    aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.rule_id or self.language not in {"zh", "ja", "en"} or not self.canonical:
            raise ValueError("terminology rule identity, language, and canonical form are required")
        if not self.aliases or any(not alias for alias in self.aliases):
            raise ValueError("terminology rule needs at least one non-empty alias")


@dataclass(frozen=True, slots=True)
class AlignedVote:
    candidate_id: str
    model_id: str
    model_revision: str
    source_index: int
    text: str
    normalized: str
    source_span: AudioSpan
    timing_source: str


@dataclass(frozen=True, slots=True)
class AlignmentColumn:
    ordinal: int
    span: AudioSpan
    votes: tuple[AlignedVote, ...]


@dataclass(frozen=True, slots=True)
class FinalToken:
    text: str
    span: AudioSpan
    support_score: float
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    language: str
    canonical_span: AudioSpan
    text: str
    tokens: tuple[FinalToken, ...]
    strategy: str
    consensus_support_score: float
    score_is_calibrated_probability: bool
    low_confidence: bool
    warnings: tuple[str, ...]
    rule_version: str = "time-aligned-confusion-network-v1"

    def __post_init__(self) -> None:
        if self.language not in {"zh", "ja", "en"}:
            raise ValueError("consensus language must be zh, ja, or en")
        if not 0 <= self.consensus_support_score <= 1:
            raise ValueError("consensus support score must be within [0, 1]")
        previous_end = self.canonical_span.start_sample
        for token in self.tokens:
            if (
                token.span.start_sample < previous_end
                or token.span.end_sample <= token.span.start_sample
                or token.span.end_sample > self.canonical_span.end_sample
                or not math.isfinite(token.support_score)
                or not 0 <= token.support_score <= 1
            ):
                raise ValueError("final token times must be ordered inside the canonical range")
            previous_end = token.span.end_sample
