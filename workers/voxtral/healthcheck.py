"""Dependency-light Voxtral worker health check."""

from classscribe_protocol import PROTOCOL_VERSION

WORKER_ID = "voxtral"
CAPABILITIES = ("asr_zh", "asr_ja", "asr_en", "streaming")


def status() -> dict[str, str | int | tuple[str, ...]]:
    return {
        "worker_id": WORKER_ID,
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": CAPABILITIES,
    }
