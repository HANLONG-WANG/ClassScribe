"""Thin IBus engine launch interface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from classscribe_protocol import PROTOCOL_VERSION, default_dictation_socket


def health_status() -> dict[str, str | int | bool]:
    return {
        "service": "classscribe-ibus-engine",
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "loads_models": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="classscribe-ibus-engine")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--socket", type=Path, default=default_dictation_socket())
    args = parser.parse_args(argv)
    if args.health_check:
        print(json.dumps(health_status(), sort_keys=True))
        return 0
    from classscribe_ibus_engine.runtime import run_ibus

    run_ibus(args.socket)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
