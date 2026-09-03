#!/usr/bin/env python3
"""Inventory the native IBus matrix and merge only explicit human-run evidence."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "config/ibus-compatibility.v1.json"
SESSION_TARGETS = ("wayland-gnome", "wayland-kde", "x11-gnome", "x11-kde")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-ibus-desktop")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "benchmarks/results/ibus-desktop-compatibility.json",
    )
    parser.add_argument("--require-complete", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    matrix = _object_file(args.matrix)
    evidence = _object_file(args.evidence) if args.evidence else {}
    report = build_report(matrix, evidence)
    _atomic_json(args.output, report)
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 2 if args.require_complete and report["status"] != "passed" else 0


def build_report(matrix: dict[str, Any], evidence: dict[str, Any]) -> dict[str, object]:
    required = matrix.get("required_scenarios")
    applications = matrix.get("applications")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("compatibility matrix required_scenarios is invalid")
    if not isinstance(applications, list):
        raise ValueError("compatibility matrix applications is invalid")
    application_evidence = evidence.get("applications", {})
    session_evidence = evidence.get("sessions", {})
    if not isinstance(application_evidence, Mapping) or not isinstance(session_evidence, Mapping):
        raise ValueError("desktop evidence applications/sessions must be objects")
    results: list[dict[str, object]] = []
    for item in applications:
        if not isinstance(item, dict):
            raise ValueError("compatibility application entry must be an object")
        identifier = str(item.get("id", ""))
        candidates = item.get("command_candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("command_candidates must be an array")
        command = next(
            (shutil.which(str(value)) for value in candidates if shutil.which(str(value))), None
        )
        supplied = application_evidence.get(identifier, {})
        if not isinstance(supplied, Mapping):
            raise ValueError(f"evidence for {identifier} must be an object")
        scenario_evidence = supplied.get("scenarios", {})
        if not isinstance(scenario_evidence, Mapping):
            raise ValueError(f"scenario evidence for {identifier} must be an object")
        scenarios: dict[str, str] = {}
        for scenario in required:
            state = str(scenario_evidence.get(scenario, "not_run"))
            if state not in {"passed", "failed", "blocked", "not_run"}:
                raise ValueError(f"invalid {identifier}/{scenario} evidence state")
            if state == "passed" and command is None:
                raise ValueError(f"cannot pass uninstalled application {identifier}")
            scenarios[scenario] = state
        complete = command is not None and all(value == "passed" for value in scenarios.values())
        results.append(
            {
                "id": identifier,
                "application": str(item.get("application", identifier)),
                "toolkit": str(item.get("toolkit", "unknown")),
                "installed_command": command,
                "preedit_attributes_expected": item.get("preedit_attributes") is True,
                "scenarios": scenarios,
                "notes": str(supplied.get("notes", "")),
                "passed": complete,
            }
        )
    sessions: dict[str, str] = {}
    for target in SESSION_TARGETS:
        state = str(session_evidence.get(target, "not_run"))
        if state not in {"passed", "failed", "blocked", "not_run"}:
            raise ValueError(f"invalid desktop session evidence state for {target}")
        sessions[target] = state
    passed = all(item["passed"] for item in results) and all(
        state == "passed" for state in sessions.values()
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "incomplete",
        "generated_at": datetime.now(UTC).isoformat(),
        "host": {
            "platform": platform.platform(),
            "session_type": os.environ.get("XDG_SESSION_TYPE"),
            "desktop": os.environ.get("XDG_CURRENT_DESKTOP"),
            "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
            "x11_display": os.environ.get("DISPLAY"),
        },
        "sessions": sessions,
        "applications": results,
        "claim_policy": "Only explicit supplied evidence may be marked passed.",
    }


def _object_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"JSON input is missing or unsafe: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
