"""Dependency-light health contract for this isolated worker."""

from __future__ import annotations

from classscribe_protocol import PROTOCOL_VERSION

WORKER_ID = "moss_en"
CAPABILITIES = ("asr_en", "reference_asr_zh", "reference_asr_ja", "greedy_decode")


def status() -> dict[str, str | int | tuple[str, ...]]:
    return {
        "worker_id": WORKER_ID,
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": CAPABILITIES,
    }
