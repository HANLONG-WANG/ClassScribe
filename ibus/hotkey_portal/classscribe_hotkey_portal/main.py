"""Wayland GlobalShortcuts portal companion launch interface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from classscribe_protocol import PROTOCOL_VERSION, default_dictation_socket


def health_status() -> dict[str, str | int]:
    return {
        "service": "classscribe-hotkey-portal",
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "portal_interface": "org.freedesktop.portal.GlobalShortcuts",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="classscribe-hotkey-portal")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--socket", type=Path, default=default_dictation_socket())
    args = parser.parse_args(argv)
    if args.health_check:
        print(json.dumps(health_status(), sort_keys=True))
        return 0
    from classscribe_hotkey_portal.runtime import run_portal

    return 0 if run_portal(args.socket) else 1


if __name__ == "__main__":
    raise SystemExit(main())
