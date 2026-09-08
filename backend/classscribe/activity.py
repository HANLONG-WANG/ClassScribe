"""Transaction-independent, context-local pipeline activity reporting.

Only measurements advance progress timestamps. Process observations do not.
Callbacks run at most once a second for counters; operation boundaries flush immediately.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_CURRENT: ContextVar[ActivityReporter | None] = ContextVar("classscribe_activity", default=None)


class ActivityReporter:
    def __init__(self, publish: Callable[..., Any], **identity: Any) -> None:
        self.publish = publish
        self.identity = identity
        self.state: dict[str, Any] = {}
        self.last_sent = 0.0
        self.lock = threading.Lock()

    def report(
        self,
        operation: str | None = None,
        *,
        measured: bool = True,
        force: bool = False,
        **details: Any,
    ) -> None:
        with self.lock:
            now = datetime.now(UTC).isoformat()
            changed = operation is not None and (
                operation != self.state.get("operation")
                or any(
                    key in details and details[key] != self.state.get(key)
                    for key in ("model_id", "request_id")
                )
            )
            advanced = changed or any(
                self.state.get(key) != value
                for key, value in details.items()
                if key not in {"process_alive", "process_checked_at"}
            )
            if changed:
                for key in (
                    "completed",
                    "total",
                    "unit",
                    "files_completed",
                    "files_total",
                    "object",
                    "process_alive",
                    "process_checked_at",
                    "timeout_seconds",
                ):
                    self.state.pop(key, None)
                self.state.update(operation=operation, started_at=now)
            if details.get("model_id", self.state.get("model_id")) != self.state.get("model_id"):
                self.state.pop("model_name", None)
            self.state.update(details)
            if measured and advanced:
                self.state["progress_at"] = now
            if changed or force or time.monotonic() - self.last_sent >= 1:
                self.last_sent = time.monotonic()
                self.publish(**self.identity, **self.state)


@contextmanager
def activity_scope(reporter: ActivityReporter) -> Iterator[None]:
    token = _CURRENT.set(reporter)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def report_activity(
    operation: str | None = None, *, measured: bool = True, force: bool = False, **details: Any
) -> None:
    reporter = _CURRENT.get()
    if reporter is not None:
        reporter.report(operation, measured=measured, force=force, **details)
