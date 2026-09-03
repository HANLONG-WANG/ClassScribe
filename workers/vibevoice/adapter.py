"""VibeVoice runtime adapter boundary."""

from classscribe_protocol.adapter import StatefulAdapter


def create_adapter() -> StatefulAdapter:
    return StatefulAdapter(
        "vibevoice",
        capabilities=("asr_zh", "asr_ja", "asr_en", "streaming", "diarization", "hotwords"),
        supported_methods=(
            "transcribe_batch",
            "stream_open",
            "stream_push",
            "stream_flush",
            "stream_close",
            "diarize",
        ),
    )
