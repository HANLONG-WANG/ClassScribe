"""Provider-specific transcription limits for the currently pinned MAI REST API."""

from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.timeline import SAMPLE_RATE

# API 2025-10-15 requires < 2 hours and < 250 MB. Keep a minute and 10 MB in reserve.
MAI_MAX_DURATION_SECONDS = 119 * 60
MAI_MAX_UPLOAD_BYTES = 240_000_000
LOCAL_MAX_DURATION_SECONDS = 90 * 60


def transcription_max_seconds(provider: str) -> int:
    return MAI_MAX_DURATION_SECONDS if provider == "azure_mai" else LOCAL_MAX_DURATION_SECONDS


def validate_transcription_duration(duration_samples: int, provider: str) -> None:
    maximum = transcription_max_seconds(provider)
    if duration_samples > maximum * SAMPLE_RATE:
        raise ClassScribeError(
            ErrorCode.AUDIO_TOO_LONG,
            f"转写时长不能超过 {maximum // 60} 分钟; 请缩短选段后再提交。",
        )
