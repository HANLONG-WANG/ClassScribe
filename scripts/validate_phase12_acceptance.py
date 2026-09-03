#!/usr/bin/env python3
"""Produce a fail-closed Phase 12 acceptance record from local evidence files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from classscribe.benchmark import load_gold_manifest, validate_phase12_acceptance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-phase12-acceptance")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--benchmark-report", type=Path, required=True)
    parser.add_argument("--desktop-report", type=Path, required=True)
    parser.add_argument("--test-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_bytes = args.manifest.read_bytes()
    result = validate_phase12_acceptance(
        load_gold_manifest(args.manifest),
        _object(args.benchmark_report),
        _object(args.desktop_report),
        _object(args.test_evidence),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    _write(args.output, result.as_dict())
    print(json.dumps({"status": result.status, "output": str(args.output)}, sort_keys=True))
    return 0 if result.status == "passed" else 2


def _object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"evidence file is missing or unsafe: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence JSON must contain an object")
    return value


def _write(path: Path, value: dict[str, object]) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_existed:
        path.parent.chmod(0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("output parent must be a real directory")
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
