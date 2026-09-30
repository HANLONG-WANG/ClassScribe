"""Assign contextual ASR output to its unique core while retaining source evidence."""

from dataclasses import replace

from classscribe.asr.models import ASRCandidateEvidence
from classscribe.quality.text import surface_tokens
from classscribe.timeline import AudioSpan


def core_evidence(evidence: ASRCandidateEvidence) -> ASRCandidateEvidence:
    if evidence.audio_span == evidence.core_span:
        return evidence
    core = evidence.core_span
    full = surface_tokens(evidence.normalized_text, evidence.language_requested)
    native = tuple(
        piece
        for token in evidence.tokens
        for piece in surface_tokens(token.text, evidence.language_requested)
    )
    selected = tuple(
        replace(
            token,
            span=AudioSpan(
                max(core.start_sample, token.span.start_sample),
                min(core.end_sample, token.span.end_sample),
            ),
        )
        for token in evidence.tokens
        if core.start_sample
        <= (token.span.start_sample + token.span.end_sample) // 2
        < core.end_sample
        and token.span.overlaps(core)
    )
    if native == full and evidence.tokens:
        pieces = tuple(
            piece
            for token in selected
            for piece in surface_tokens(token.text, evidence.language_requested)
        )
        method = "native_token_midpoint"
    else:
        # Without complete native timing there is no authority to delete words.
        # Preserve the full hypothesis and require review of the boundary instead.
        pieces = full
        method = "unresolved_context_boundary"
    separator = " " if evidence.language_requested == "en" else ""
    return replace(
        evidence,
        normalized_text=separator.join(pieces)
        if method == "native_token_midpoint"
        else evidence.normalized_text,
        tokens=selected,
        warnings=(*evidence.warnings, "context output assigned to unique core: " + method),
        provenance={
            **evidence.provenance,
            "context_assignment": method,
            "original_normalized_text": evidence.normalized_text,
            "original_tokens": [
                {
                    "text": token.text,
                    "start_sample": token.span.start_sample,
                    "end_sample": token.span.end_sample,
                    "source_token_index": token.source_index,
                }
                for token in evidence.tokens
            ],
        },
    )
