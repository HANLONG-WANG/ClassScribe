#!/usr/bin/env python3
"""Score complete pinned-model prediction JSONL against a private local gold manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from classscribe.benchmark import BenchmarkRunner, load_gold_manifest, load_predictions


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="classscribe-benchmark")
    value.add_argument("--manifest", type=Path, required=True)
    value.add_argument("--manifest-version", required=True)
    value.add_argument("--predictions", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    value.add_argument("--parameters-json", type=Path)
    value.add_argument(
        "--starter-coverage",
        action="store_true",
        help="allow the documented initial 5-minute classroom set; never valid for ranking release",
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    manifest_bytes = args.manifest.read_bytes()
    records = load_gold_manifest(args.manifest)
    predictions = load_predictions(args.predictions)
    parameters = _object_file(args.parameters_json) if args.parameters_json else {}
    runner = BenchmarkRunner(
        records,
        manifest_version=args.manifest_version,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        hardware=_hardware(),
        parameters={
            **parameters,
            "offline": True,
            "prediction_contract": "benchmark-prediction-v1",
        },
        production_gold=not args.starter_coverage,
    )
    report = runner.run(predictions)
    report.write(args.output)
    print(
        json.dumps(
            {
                "status": report.status,
                "output": str(args.output),
                "manifest_sha256": report.manifest_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if report.status == "completed" else 2


def _hardware() -> dict[str, object]:
    gpu = _command(
        ("nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader")
    )
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "gpu": gpu,
    }


def _command(command: tuple[str, ...]) -> dict[str, object]:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "detail": type(exc).__name__}
    return {
        "available": result.returncode == 0,
        "returncode": result.returncode,
        "detail": (result.stdout or result.stderr).strip()[:2048],
    }


def _object_file(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("parameters JSON must contain an object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
