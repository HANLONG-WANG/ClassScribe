"""Bound final-token provenance to the token's own audio interval."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def sources_for_span(
    sources: Sequence[dict[str, Any]], start_sample: int, end_sample: int
) -> list[dict[str, Any]]:
    """Retain overlapping source tokens, or the nearest timed source as fallback."""
    timed = [
        (source, source["source_start_sample"], source["source_end_sample"])
        for source in sources
        if type(source.get("source_start_sample")) is int
        and type(source.get("source_end_sample")) is int
        and source["source_end_sample"] >= source["source_start_sample"]
    ]
    timed_ids = {id(source) for source, _, _ in timed}
    untimed = [source for source in sources if id(source) not in timed_ids]
    overlapping = [
        source
        for source, source_start, source_end in timed
        if source_start < end_sample and source_end > start_sample
    ]
    if overlapping:
        return overlapping + untimed
    if timed:
        nearest_distance = min(
            max(source_start - end_sample, start_sample - source_end, 0)
            for _, source_start, source_end in timed
        )
        nearest = [
            source
            for source, source_start, source_end in timed
            if max(source_start - end_sample, start_sample - source_end, 0) == nearest_distance
        ]
        return nearest + untimed
    return list(sources)


def compact_token_provenance(
    provenance: dict[str, Any], start_sample: int, end_sample: int
) -> dict[str, Any]:
    """Compact legacy final-alignment evidence for API responses without changing storage."""
    sources = provenance.get("candidate_sources")
    if provenance.get("source_type") != "final_text_alignment" or not isinstance(sources, list):
        return provenance
    if not all(isinstance(source, dict) for source in sources):
        return provenance
    compact = sources_for_span(sources, start_sample, end_sample)
    if len(compact) == len(sources):
        return provenance
    return {**provenance, "candidate_sources": compact}
