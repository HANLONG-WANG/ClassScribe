from __future__ import annotations

import io
import json
import logging
import stat
import zipfile
from collections.abc import Sequence
from pathlib import Path

from classscribe.diagnostics import (
    DiagnosticCollector,
    export_diagnostic_bundle,
    snapshot_payload,
)
from classscribe.observability import LocalMetrics, LogContext, PrivacyJSONFormatter, log_event


def test_structured_logs_have_fixed_fields_and_redact_text_and_secrets() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(PrivacyJSONFormatter())
    logger = logging.getLogger("classscribe-test-redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    log_event(
        logger,
        logging.INFO,
        "segment.accepted",
        LogContext(
            service="core",
            request_id="request-1",
            job_id="job-1",
            segment_id="segment-1",
            model_id="model-1",
            stage="quality",
            duration_ms=50,
            vram_mb=100,
            error_code=None,
        ),
        details={
            "raw_text": "private transcript sentence",
            "authorization": "Bearer secret-value",
            "nested": {"hf_token": "hf_abcdefghijklmnop"},
            "quality_score": 0.9,
        },
    )
    payload = json.loads(output.getvalue())
    for field in (
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
    ):
        assert field in payload
    serialized = json.dumps(payload)
    assert "private transcript sentence" not in serialized
    assert "secret-value" not in serialized
    assert "hf_abcdefghijklmnop" not in serialized
    assert payload["details"]["quality_score"] == 0.9


def test_metrics_cover_required_counters_stage_time_and_ibus_percentiles() -> None:
    metrics = LocalMetrics()
    for name in (
        "worker_load",
        "worker_unload",
        "oom",
        "repetition_rejected",
        "fallback",
        "automatic_adopted",
        "low_confidence",
    ):
        metrics.increment(name)
    metrics.observe_stage("asr", 100)
    for latency in (10, 20, 30, 40, 50):
        metrics.observe_ibus_latency(latency)
    snapshot = metrics.snapshot()
    assert snapshot["stage_duration_ms"]["asr"] == {"count": 1, "total": 100, "max": 100}
    assert snapshot["ibus_latency_ms"] == {"count": 5, "p50": 30, "p95": 40}
    assert all(snapshot["counters"][name] == 1 for name in snapshot["counters"])


def test_diagnostic_bundle_is_local_inventory_without_sensitive_content(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str]) -> tuple[int, str]:
        commands.append(tuple(command))
        return 0, "test version"

    collector = DiagnosticCollector(runner=runner)
    snapshot = collector.collect(
        workers=({"worker_id": "qwen", "status": "ok", "token": "worker-secret"},),
        models=({"model_id": "qwen", "version": "revision-1"},),
        gpu_lease={"owner": "job-1"},
        gpu_queue=({"job_id": "job-2"},),
        recent_errors=(
            {
                "error_code": "WORKER_CRASH",
                "raw_text": "sensitive transcript",
                "authorization": "Bearer top-secret",
            },
        ),
    )
    payload = snapshot_payload(snapshot)
    assert payload["system"]["kernel"]
    assert payload["gpu"]["status"] == "ok"
    assert {component["name"] for component in payload["components"]} == {
        "FFmpeg",
        "PipeWire",
        "IBus",
        "Portal",
    }
    destination = tmp_path / "diagnostics.zip"
    export_diagnostic_bundle(destination, snapshot)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with zipfile.ZipFile(destination) as archive:
        content = archive.read("diagnostics.json").decode("utf-8")
    assert "sensitive transcript" not in content
    assert "top-secret" not in content
    assert "worker-secret" not in content
    assert "[REDACTED]" in content
    assert not list(tmp_path.glob("*.tmp"))
