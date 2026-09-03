"""Merge full-coverage shorter reruns after a looping candidate is rejected."""

from __future__ import annotations

from collections.abc import Sequence

from classscribe.asr.models import ASRCandidateEvidence
from classscribe.quality.models import RetryDirective
from classscribe.timeline import AudioSpan


def merge_retry_pieces(
    directive: RetryDirective,
    pieces: Sequence[ASRCandidateEvidence],
    *,
    original_core: AudioSpan,
) -> ASRCandidateEvidence:
    if tuple(piece.audio_span for piece in pieces) != directive.retry_spans:
        raise ValueError("retry pieces must exactly match the ordered shorter-span directive")
    if not pieces:
        raise ValueError("retry merge needs at least one piece")
    first = pieces[0]
    if any(
        (
            piece.model_id,
            piece.model_revision,
            piece.language_requested,
            piece.role,
        )
        != (
            first.model_id,
            first.model_revision,
            first.language_requested,
            first.role,
        )
        for piece in pieces[1:]
    ):
        raise ValueError("retry pieces must come from one replacement model and revision")
    full_span = AudioSpan(
        directive.retry_spans[0].start_sample, directive.retry_spans[-1].end_sample
    )
    if not (
        full_span.start_sample <= original_core.start_sample
        and original_core.end_sample <= full_span.end_sample
    ):
        raise ValueError("retry pieces no longer contain the original core")
    separator = " " if first.language_requested == "en" else ""
    raw_text = separator.join(piece.raw_text.strip() for piece in pieces if piece.raw_text.strip())
    normalized_text = separator.join(
        piece.normalized_text.strip() for piece in pieces if piece.normalized_text.strip()
    )
    confidences = [piece.confidence_raw for piece in pieces if piece.confidence_raw is not None]
    metrics: dict[str, int | float | str | bool | None] = {
        "retry_piece_count": len(pieces),
        "inference_ms": sum(_metric_number(piece, "inference_ms") for piece in pieces),
        "peak_vram_mb": max(
            (_metric_number(piece, "peak_vram_mb") for piece in pieces), default=0.0
        ),
        "generated_tokens": int(sum(_metric_number(piece, "generated_tokens") for piece in pieces)),
        "full_coverage_shorter_rerun": True,
    }
    return ASRCandidateEvidence(
        model_id=first.model_id,
        model_revision=first.model_revision,
        role=first.role,
        language_requested=first.language_requested,
        language_reported=first.language_reported,
        audio_span=full_span,
        core_span=original_core,
        raw_text=raw_text,
        normalized_text=normalized_text,
        confidence_raw=sum(confidences) / len(confidences) if confidences else None,
        tokens=tuple(token for piece in pieces for token in piece.tokens),
        decode=dict(first.decode),
        metrics=metrics,
        warnings=tuple(dict.fromkeys(warning for piece in pieces for warning in piece.warnings)),
        provenance={
            "retry_piece_count": len(pieces),
            "retry_spans": [
                {"start_sample": span.start_sample, "end_sample": span.end_sample}
                for span in directive.retry_spans
            ],
            "preserved_full_canonical_coverage": True,
            "switched_model_after_rejection": directive.switch_model,
            "rejected_candidate_reason": directive.reason,
        },
    )


def _metric_number(piece: ASRCandidateEvidence, key: str) -> float:
    value = piece.metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
