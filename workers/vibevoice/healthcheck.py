"""Dependency-light VibeVoice worker health check."""

from classscribe_protocol import PROTOCOL_VERSION

WORKER_ID = "vibevoice"
CAPABILITIES = ("asr_zh", "asr_ja", "asr_en", "streaming", "diarization", "hotwords")


def status() -> dict[str, str | int | tuple[str, ...]]:
    return {
        "worker_id": WORKER_ID,
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": CAPABILITIES,
    }
