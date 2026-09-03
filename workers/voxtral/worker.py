"""Launch the isolated Voxtral worker."""

from adapter import create_adapter
from classscribe_protocol.worker_main import run_worker
from healthcheck import status

if __name__ == "__main__":
    raise SystemExit(run_worker("classscribe-worker-voxtral", status, create_adapter))
