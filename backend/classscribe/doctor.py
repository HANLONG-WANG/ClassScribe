"""Local, redacted diagnostics command for Fedora support workflows."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from classscribe.config import load_config
from classscribe.diagnostics import DiagnosticCollector, export_diagnostic_bundle, snapshot_payload
from classscribe.system_check import FedoraDependencyChecker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="classscribe-doctor")
    parser.add_argument(
        "--bundle",
        type=Path,
        help="write a 0600 zip containing only the same redacted diagnostics JSON",
    )
    parser.add_argument("--config", type=Path, help="validate an explicit user configuration")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    dependencies = FedoraDependencyChecker().check()
    snapshot = DiagnosticCollector().collect()
    payload = snapshot_payload(snapshot)
    payload["dependencies"] = dependencies.as_dict()
    payload["configuration"] = {
        "status": "ok",
        "version": config.config_version,
        "runtime_offline": config.privacy.runtime_offline,
        "loopback_host": config.server.host,
    }
    if args.bundle is not None:
        export_diagnostic_bundle(args.bundle, snapshot)
        payload["bundle"] = {"written": True, "path": str(args.bundle)}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if dependencies.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
