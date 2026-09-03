"""Short-transaction persistence for reproducible benchmark reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from classscribe.benchmark.runner import BenchmarkReport
from classscribe.db.models import BenchmarkItem, BenchmarkRun, BenchmarkStatus


class BenchmarkRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def complete(self, run_id: str, report: BenchmarkReport) -> BenchmarkRun:
        with self.sessions.begin() as session:
            run = session.get(BenchmarkRun, run_id)
            if run is None:
                raise ValueError("benchmark run does not exist")
            if run.status is not BenchmarkStatus.RUNNING or run.items:
                raise ValueError("benchmark run is not an empty running run")
            if run.manifest_version != report.manifest_version:
                raise ValueError("benchmark report manifest version differs from its run")
            run.hardware_json = report.hardware
            run.parameters_json = {
                **run.parameters_json,
                **report.parameters,
                "manifest_sha256": report.manifest_sha256,
                "calibrations": list(report.calibrations),
            }
            run.metrics_json = _aggregate_report_metrics(report.items)
            run.ranking_json = list(report.rankings)
            run.status = (
                BenchmarkStatus.COMPLETED
                if report.status == "completed"
                else BenchmarkStatus.FAILED
            )
            run.completed_at = datetime.now(UTC)
            for entry in report.items:
                gold = entry.get("gold")
                prediction = entry.get("prediction")
                metrics = entry.get("metrics")
                if not all(isinstance(value, dict) for value in (gold, prediction, metrics)):
                    raise ValueError("benchmark report item lacks gold/prediction/metrics")
                session.add(
                    BenchmarkItem(
                        run_id=run.id,
                        item_key=str(entry["item_id"]),
                        model_id=str(entry["model_id"]),
                        model_revision=str(entry["model_revision"]),
                        gold_json=gold,
                        prediction_json=prediction,
                        metrics_json=metrics,
                    )
                )
            session.flush()
            return run

    def fail(self, run_id: str, *, error_code: str) -> None:
        if not error_code:
            raise ValueError("benchmark failure requires an error code")
        with self.sessions.begin() as session:
            run = session.get(BenchmarkRun, run_id)
            if run is None or run.status is not BenchmarkStatus.RUNNING:
                raise ValueError("benchmark run is not running")
            run.status = BenchmarkStatus.FAILED
            run.metrics_json = {"error_code": error_code}
            run.completed_at = datetime.now(UTC)


def _aggregate_report_metrics(items: tuple[dict[str, object], ...]) -> dict[str, Any]:
    return {
        "item_count": len(items),
        "test_item_count": sum(item.get("split") == "test" for item in items),
        "products": sorted({str(item.get("scenario")) for item in items}),
        "languages": sorted({str(item.get("language")) for item in items}),
        "models": sorted({str(item.get("model_id")) for item in items}),
    }
