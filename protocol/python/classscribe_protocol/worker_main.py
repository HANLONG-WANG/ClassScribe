"""Uniform command-line entry point used by every isolated model worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from classscribe_protocol.adapter import WorkerAdapter
from classscribe_protocol.cuda import cuda_status
from classscribe_protocol.rpc import RPCServer

StatusProvider = Callable[[], Mapping[str, Any]]
AdapterFactory = Callable[[], WorkerAdapter]


def run_worker(
    program: str,
    status_provider: StatusProvider,
    adapter_factory: AdapterFactory,
    argv: Sequence[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog=program)
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--cuda-check", action="store_true")
    parser.add_argument("--socket", type=Path)
    parser.add_argument(
        "--data-root",
        action="append",
        default=[],
        type=Path,
        help="restricted root containing read-only batch audio (repeatable)",
    )
    args = parser.parse_args(argv)
    if args.health_check:
        print(json.dumps(dict(status_provider()), sort_keys=True))
        return 0
    if args.cuda_check:
        print(json.dumps(cuda_status(), sort_keys=True))
        return 0
    if args.socket is None:
        parser.error("one of --health-check, --cuda-check, or --socket is required")
    if not args.socket.is_absolute():
        parser.error("--socket must be absolute")
    if any(not root.is_absolute() for root in args.data_root):
        parser.error("every --data-root must be absolute")
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    try:
        asyncio.run(_serve(args.socket, tuple(args.data_root), adapter_factory()))
    except KeyboardInterrupt:
        return 130
    return 0


async def _serve(socket_path: Path, data_roots: tuple[Path, ...], adapter: WorkerAdapter) -> None:
    server = RPCServer(socket_path, adapter, allowed_data_roots=data_roots)
    await server.start()
    try:
        await asyncio.Event().wait()
    finally:
        await server.close()
