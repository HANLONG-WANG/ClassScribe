"""Atomic output and deterministic failure fallback policies."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from sqlalchemy.orm import Session

from classscribe.db.models import ASRCandidate, DecisionEvent, TimingQuality, TranscriptSegment
from classscribe.errors import ErrorCode
from classscribe.timeline import AudioSpan, TimelineError


def atomic_write_bytes(destination: Path, content: bytes, *, mode: int = 0o600) -> None:
    """Write, fsync, and atomically replace a file in its destination directory."""

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(
    destination: Path, content: str, *, encoding: str = "utf-8", mode: int = 0o600
) -> None:
    atomic_write_bytes(destination, content.encode(encoding), mode=mode)


class DictationFailure(StrEnum):
    DAEMON_UNAVAILABLE = "daemon_unavailable"
    MICROPHONE_DISCONNECTED = "microphone_disconnected"
    GPU_OUT_OF_MEMORY = "gpu_out_of_memory"
    SOCKET_DISCONNECTED = "socket_disconnected"


@dataclass(frozen=True, slots=True)
class DictationFallback:
    error_code: ErrorCode
    cancel_preedit: bool
    commit_preedit: bool
    pass_through_keys: bool
    fallback_target: str | None
    status_message: str


DICTATION_FALLBACKS: Final = {
    DictationFailure.DAEMON_UNAVAILABLE: DictationFallback(
        ErrorCode.WORKER_SOCKET_DISCONNECTED,
        cancel_preedit=True,
        commit_preedit=False,
        pass_through_keys=True,
        fallback_target=None,
        status_message="听写服务不可用。键盘输入保持原样。",
    ),
    DictationFailure.MICROPHONE_DISCONNECTED: DictationFallback(
        ErrorCode.MICROPHONE_DISCONNECTED,
        cancel_preedit=True,
        commit_preedit=False,
        pass_through_keys=False,
        fallback_target=None,
        status_message="麦克风已断开。当前预编辑已取消。",
    ),
    DictationFailure.GPU_OUT_OF_MEMORY: DictationFallback(
        ErrorCode.GPU_OUT_OF_MEMORY,
        cancel_preedit=False,
        commit_preedit=False,
        pass_through_keys=False,
        fallback_target="small_model_or_cpu",
        status_message="GPU 显存不足。正在尝试本地小模型或 CPU 备用。",
    ),
    DictationFailure.SOCKET_DISCONNECTED: DictationFallback(
        ErrorCode.WORKER_SOCKET_DISCONNECTED,
        cancel_preedit=True,
        commit_preedit=False,
        pass_through_keys=False,
        fallback_target=None,
        status_message="本地连接中断。半句不会被静默提交。",
    ),
}


def dictation_fallback(failure: DictationFailure) -> DictationFallback:
    return DICTATION_FALLBACKS[failure]


def validate_candidate_timeline(
    session: Session,
    candidate: ASRCandidate,
    segment: TranscriptSegment,
    spans: list[AudioSpan],
) -> bool:
    """Invalidate the whole candidate on any non-monotonic or out-of-segment time."""

    valid = True
    previous_end = segment.start_sample
    try:
        for span in spans:
            if (
                span.start_sample < segment.start_sample
                or span.end_sample > segment.end_sample
                or span.start_sample < previous_end
            ):
                valid = False
                break
            previous_end = span.end_sample
    except TimelineError:
        valid = False
    if not valid:
        candidate.is_valid = False
        candidate.warnings_json = [
            *candidate.warnings_json,
            {"error_code": ErrorCode.CANDIDATE_TIMELINE_INVALID.value},
        ]
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="candidate_rejected",
                actor_type="automatic",
                actor_id=None,
                input_json={"candidate_id": candidate.id},
                output_json={"is_valid": False},
                rule_version="timeline-v1",
            )
        )
    return valid


def record_alignment_fallback(session: Session, segment: TranscriptSegment, *, detail: str) -> None:
    """Retain structural coarse time rather than fabricating precise word time."""

    segment.timing_quality = TimingQuality.STRUCTURE
    session.add(
        DecisionEvent(
            segment_id=segment.id,
            event_type="alignment_fallback",
            actor_type="automatic",
            actor_id=None,
            input_json={"error_code": ErrorCode.ALIGNMENT_FAILED.value},
            output_json={"timing_quality": TimingQuality.STRUCTURE.value, "detail": detail},
            rule_version="alignment-fallback-v1",
        )
    )


def record_window_dedup_conflict(
    session: Session, segment: TranscriptSegment, candidate_ids: list[str]
) -> None:
    """Keep both candidates and audit ambiguity rather than dropping content."""

    session.add(
        DecisionEvent(
            segment_id=segment.id,
            event_type="window_dedup_conflict",
            actor_type="automatic",
            actor_id=None,
            input_json={"candidate_ids": candidate_ids},
            output_json={"kept_all": True, "error_code": ErrorCode.WINDOW_DEDUP_CONFLICT.value},
            rule_version="window-dedup-v1",
        )
    )
