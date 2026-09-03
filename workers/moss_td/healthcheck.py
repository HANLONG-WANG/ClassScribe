"""Dependency-light health contract for this isolated worker."""

from __future__ import annotations

from classscribe_protocol import PROTOCOL_VERSION

WORKER_ID = "moss_td"
CAPABILITIES = (
    "structure_transcription",
    "absolute_timestamps",
    "speaker_events",
    "acoustic_events",
    "hotwords",
)


def status() -> dict[str, str | int | tuple[str, ...]]:
    return {
        "worker_id": WORKER_ID,
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": CAPABILITIES,
    }
