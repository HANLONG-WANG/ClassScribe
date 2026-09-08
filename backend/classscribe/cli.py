"""Command-line boundary for the local ClassScribe core service."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from classscribe.api.app import create_app, health_status
from classscribe.config import load_config
from classscribe.paths import AppPaths
from classscribe.resources import resource_path
from classscribe.security import TokenStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="classscribe-core")
    parser.add_argument("--config", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--health-check", action="store_true")
    group.add_argument("--check-config", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = AppPaths.from_environment()
    config = load_config(args.config or paths.config / "config.yaml")
    if args.health_check:
        print(json.dumps(health_status(), sort_keys=True))
        return 0
    if args.check_config:
        print(config.model_dump_json(indent=2))
        return 0
    paths.ensure()
    api_token = TokenStore(paths.config / "api-token").load_or_create()
    uvicorn.run(
        create_app(
            config=config,
            api_token=api_token,
            enable_scheduler_ipc=True,
            runtime_paths=paths,
            static_directory=resource_path("frontend/dist"),
        ),
        host=config.server.host,
        port=config.server.port,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
