#!/usr/bin/env python3
"""Validate the frozen source release and emit a machine-readable result."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from classscribe.release_check import validate_installed_release, validate_release

ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="classscribe-release-check")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--installed-prefix", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = (
        validate_installed_release(args.installed_prefix)
        if args.installed_prefix is not None
        else validate_release(args.root)
    ).as_dict()
    if args.output is not None:
        _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["status"] == "ready" else 2


def _write(path: Path, result: dict[str, object]) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_existed:
        path.parent.chmod(0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("output parent must be a real directory")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


if __name__ == "__main__":
    raise SystemExit(main())
