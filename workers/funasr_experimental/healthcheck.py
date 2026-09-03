"""Dependency-light health contract for this isolated worker."""

from __future__ import annotations

from classscribe_protocol import PROTOCOL_VERSION

WORKER_ID = "funasr_experimental"
CAPABILITIES = ("asr_zh", "asr_ja", "asr_en", "hotwords", "loop_detection")


def status() -> dict[str, str | int | tuple[str, ...]]:
    return {
        "worker_id": WORKER_ID,
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": CAPABILITIES,
    }
