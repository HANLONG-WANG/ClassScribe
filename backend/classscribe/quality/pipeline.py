"""Conditional ASR review orchestration; normal chunks run only their primary model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from classscribe.asr.models import ASRCandidateEvidence
from classscribe.quality.features import QualityFeatureExtractor
from classscribe.quality.language import ComparisonContext, compare_language_candidates
from classscribe.quality.models import QualityContext, QualityReport, RetryDirective
from classscribe.quality.routing import (
    ReviewRouter,
    SecondaryReviewDecision,
    TertiaryReviewDecision,
)

ReviewInvoker = Callable[[RetryDirective | None], Awaitable[tuple[str, ASRCandidateEvidence]]]


@dataclass(frozen=True, slots=True)
class ReviewedCandidate:
    candidate_id: str
    evidence: ASRCandidateEvidence
    quality: QualityReport


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    candidates: tuple[ReviewedCandidate, ...]
    secondary_decision: SecondaryReviewDecision
    tertiary_decision: TertiaryReviewDecision | None


class QualityReviewPipeline:
    def __init__(
        self,
        extractor: QualityFeatureExtractor | None = None,
        router: ReviewRouter | None = None,
    ) -> None:
        self.extractor = extractor or QualityFeatureExtractor()
        self.router = router or ReviewRouter()

    async def review(
        self,
        *,
        primary_id: str,
        primary: ASRCandidateEvidence,
        quality_context: QualityContext,
        comparison_context: ComparisonContext,
        run_secondary: ReviewInvoker,
        run_tertiary: ReviewInvoker,
    ) -> ReviewOutcome:
        primary_reviewed = self._inspect(primary_id, primary, quality_context)
        secondary_decision = self.router.secondary(primary_reviewed.quality)
        reviewed = [primary_reviewed]
        if not secondary_decision.run_secondary:
            return ReviewOutcome(tuple(reviewed), secondary_decision, None)

        secondary_id, secondary = await run_secondary(secondary_decision.retry)
        if secondary_decision.retry is not None:
            if secondary.model_id == primary.model_id:
                raise ValueError("loop retry must switch to a different ASR model")
            if secondary.provenance.get("preserved_full_canonical_coverage") is not True:
                raise ValueError("loop retry must merge all shorter spans back to full coverage")
        secondary_reviewed = self._inspect(
            secondary_id, secondary, self._next_model_context(quality_context)
        )
        reviewed.append(secondary_reviewed)
        comparison = compare_language_candidates(
            primary.normalized_text,
            secondary.normalized_text,
            primary.language_requested,
            comparison_context,
        )
        tertiary_decision = self.router.tertiary(
            primary_reviewed.quality,
            secondary_reviewed.quality,
            comparison,
        )
        if tertiary_decision.run_tertiary:
            tertiary_id, tertiary = await run_tertiary(None)
            reviewed.append(
                self._inspect(tertiary_id, tertiary, self._next_model_context(quality_context))
            )
        return ReviewOutcome(tuple(reviewed), secondary_decision, tertiary_decision)

    def _inspect(
        self,
        candidate_id: str,
        evidence: ASRCandidateEvidence,
        context: QualityContext,
    ) -> ReviewedCandidate:
        return ReviewedCandidate(
            candidate_id,
            evidence,
            self.extractor.inspect(candidate_id, evidence, context),
        )

    @staticmethod
    def _next_model_context(context: QualityContext) -> QualityContext:
        return replace(
            context,
            token_logprobs=(),
            ctc_posteriors=(),
            rnnt_posteriors=(),
            prefix_snapshots=(),
            calibrated_candidate_quality=None,
        )
