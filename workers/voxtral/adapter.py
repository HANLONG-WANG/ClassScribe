"""Voxtral runtime adapter boundary."""

from classscribe_protocol.adapter import StatefulAdapter


def create_adapter() -> StatefulAdapter:
    return StatefulAdapter(
        "voxtral",
        capabilities=("asr_zh", "asr_ja", "asr_en", "streaming"),
        supported_methods=(
            "transcribe_batch",
            "stream_open",
            "stream_push",
            "stream_flush",
            "stream_close",
        ),
    )
