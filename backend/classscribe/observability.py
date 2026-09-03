"""Structured privacy-preserving logs and local metrics."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

LOG_FIELDS: Final = (
    "timestamp",
    "level",
    "service",
    "request_id",
    "job_id",
    "segment_id",
    "model_id",
    "stage",
    "duration_ms",
    "vram_mb",
    "error_code",
)
SENSITIVE_KEYS: Final = {
    "api_token",
    "authorization",
    "committed_text",
    "credentials",
    "faithful_text",
    "hf_token",
    "password",
    "raw_text",
    "secret",
    "smart_corrected_text",
    "text",
    "token",
    "transcript",
    "user_text",
}
SECRET_PATTERN = re.compile(r"(?i)(bearer\s+\S+|hf_[a-z0-9]{16,})")


def redact(value: Any, *, home: Path | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if str(key).lower() in SENSITIVE_KEYS
            else redact(item, home=home)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, home=home) for item in value]
    if isinstance(value, str):
        cleaned = SECRET_PATTERN.sub("[REDACTED]", value)
        if home is not None:
            cleaned = cleaned.replace(str(home), "[HOME]")
        return cleaned
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return repr(value)


@dataclass(frozen=True, slots=True)
class LogContext:
    service: str
    request_id: str | None = None
    job_id: str | None = None
    segment_id: str | None = None
    model_id: str | None = None
    stage: str | None = None
    duration_ms: int | None = None
    vram_mb: int | None = None
    error_code: str | None = None


class PrivacyJSONFormatter(logging.Formatter):
    """Serialize fixed log fields and redact user content/secrets recursively."""

    def format(self, record: logging.LogRecord) -> str:
        context = getattr(record, "classscribe_context", None)
        if not isinstance(context, LogContext):
            context = LogContext(service=record.name)
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            **asdict(context),
            "event": getattr(record, "event", record.getMessage()),
        }
        details = getattr(record, "details", None)
        if details is not None:
            payload["details"] = details
        return json.dumps(redact(payload, home=Path.home()), ensure_ascii=False, sort_keys=True)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    context: LogContext,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    logger.log(
        level,
        event,
        extra={"event": event, "classscribe_context": context, "details": details},
    )


class LocalMetrics:
    """In-memory metrics snapshot with no exporter or telemetry path."""

    def __init__(self, *, max_latency_samples: int = 2_000) -> None:
        self._lock = threading.Lock()
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._stage_durations: defaultdict[str, list[int]] = defaultdict(list)
        self._ibus_latencies: deque[int] = deque(maxlen=max_latency_samples)

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_stage(self, stage: str, duration_ms: int) -> None:
        with self._lock:
            self._stage_durations[stage].append(duration_ms)

    def observe_ibus_latency(self, duration_ms: int) -> None:
        with self._lock:
            self._ibus_latencies.append(duration_ms)

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
        return ordered[index]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = list(self._ibus_latencies)
            return {
                "counters": dict(self._counters),
                "stage_duration_ms": {
                    stage: {
                        "count": len(values),
                        "total": sum(values),
                        "max": max(values),
                    }
                    for stage, values in self._stage_durations.items()
                },
                "ibus_latency_ms": {
                    "count": len(latencies),
                    "p50": self._percentile(latencies, 0.50),
                    "p95": self._percentile(latencies, 0.95),
                },
            }
