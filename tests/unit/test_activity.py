from __future__ import annotations

from typing import Any

import pytest
from classscribe.activity import ActivityReporter, activity_scope, report_activity
from classscribe.classroom.pipeline import PipelineEventBroker


def test_counters_coalesce_and_process_observations_do_not_advance_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    monkeypatch.setattr("classscribe.activity.time.monotonic", lambda: clock[0])
    events: list[dict[str, Any]] = []
    with activity_scope(ActivityReporter(lambda **payload: events.append(payload))):
        report_activity("verify_model", completed=0, total=1000)
        for number in range(1, 1000):
            report_activity(completed=number)
        assert len(events) == 1
        clock[0] += 1
        report_activity(measured=False, process_alive=True)
        assert events[-1]["completed"] == 999
        progress_at = events[-1]["progress_at"]
        clock[0] += 1
        report_activity(measured=False, process_alive=True)
        assert events[-1]["progress_at"] == progress_at
        clock[0] += 1
        report_activity(completed=999)
        assert events[-1]["progress_at"] == progress_at
        report_activity("load_model")
        assert "completed" not in events[-1]
        assert "total" not in events[-1]
    report_activity("outside_scope")
    assert events[-1]["operation"] == "load_model"


def test_old_attempts_and_terminal_activity_are_rejected_and_history_survives_heartbeats() -> None:
    broker = PipelineEventBroker(retained_per_job=4)
    broker.publish("job", "checkpoint_started", run_id="old")
    broker.publish("job", "activity", run_id="old", stage="vad", operation="verify_model")
    broker.publish("job", "checkpoint_started", run_id="new")
    broker.publish("job", "activity", run_id="new", stage="vad", operation="load_model")
    broker.publish("job", "activity", run_id="old", stage="vad", operation="verify_model")
    assert broker.activity("job")["run_id"] == "new"  # type: ignore[index]
    for _ in range(20):
        broker.publish("job", "activity", run_id="new", stage="vad", operation="load_model")
    assert len(broker.after("job")) == 4
    assert any(e.payload.get("operation") == "verify_model" for e in broker.recent("job"))
    broker.publish("job", "job_cancelled")
    broker.publish("job", "activity", run_id="new", stage="vad", operation="process_audio")
    assert broker.activity("job") is None
    assert broker.after("job")[-1].kind == "job_cancelled"
