"""Command-line boundary for the local ClassScribe core service."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from classscribe.api.app import create_app, health_status
from classscribe.classroom.online import reimport_mai_job
from classscribe.config import load_config
from classscribe.db.models import Job, JobStatus
from classscribe.db.session import create_sqlite_engine, make_session_factory
from classscribe.errors import ClassScribeError
from classscribe.paths import AppPaths
from classscribe.resources import resource_path
from classscribe.security import TokenStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="classscribe-core")
    parser.add_argument("--config", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--health-check", action="store_true")
    group.add_argument("--check-config", action="store_true")
    group.add_argument("--print-url", action="store_true")
    group.add_argument(
        "--reimport-mai",
        nargs="*",
        metavar="JOB_ID",
        help="rebuild completed MAI jobs from saved responses; no IDs means all",
    )
    return parser


def reimport_mai(paths: AppPaths, job_ids: Sequence[str]) -> int:
    sessions = make_session_factory(create_sqlite_engine(paths.data / "classscribe.sqlite3"))
    if not job_ids:
        with sessions() as session:
            job_ids = [
                job.id
                for job in session.scalars(
                    select(Job).where(Job.status == JobStatus.COMPLETED).order_by(Job.created_at)
                )
                if job.options_json.get("provider") == "azure_mai"
            ]
    failed = 0
    for job_id in job_ids:
        try:
            with sessions.begin() as session:
                count = reimport_mai_job(session, job_id)
        except (ClassScribeError, NoResultFound, OSError) as exc:
            failed += 1
            print(f"{job_id}: 失败 - {getattr(exc, 'detail', exc)}")
        else:
            print(f"{job_id}: {count} 个句段")
    print(f"完成 {len(job_ids) - failed}/{len(job_ids)} 个任务。")
    return 1 if failed else 0


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
    if args.print_url:
        host = "[::1]" if config.server.host == "::1" else config.server.host
        print(f"http://{host}:{config.server.port}")
        return 0
    if args.reimport_mai is not None:
        return reimport_mai(paths, args.reimport_mai)
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
