"""Launch interface for the isolated qwen worker."""

from __future__ import annotations

from adapter import create_adapter
from classscribe_protocol.worker_main import run_worker
from healthcheck import status


def main() -> int:
    return run_worker("classscribe-worker-qwen", status, create_adapter)


if __name__ == "__main__":
    raise SystemExit(main())
