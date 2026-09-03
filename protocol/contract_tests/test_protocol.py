from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from classscribe.contracts import LanguageMode, ModelSelectionMode, ProductSurface, TextLayer
from classscribe_protocol import PROTOCOL_VERSION, Envelope, ProtocolError, WorkerHello

ROOT = Path(__file__).resolve().parents[2]
WORKERS = (
    "moss_td",
    "firered",
    "granite",
    "qwen",
    "ark",
    "moss_en",
    "nemotron",
    "pyannote",
    "funasr_experimental",
    "voxtral",
    "vibevoice",
)


def test_envelope_requires_current_protocol_version() -> None:
    envelope = Envelope(PROTOCOL_VERSION, "request-1", "health", {})
    assert envelope.protocol_version == 1
    with pytest.raises(ProtocolError, match="unsupported"):
        Envelope(2, "request-2", "health", {})


def test_worker_hello_has_identity_and_capabilities() -> None:
    hello = WorkerHello(PROTOCOL_VERSION, "example", ("health",))
    assert hello.worker_id == "example"
    with pytest.raises(ProtocolError):
        WorkerHello(PROTOCOL_VERSION, "", ())


def test_python_enums_match_machine_contract() -> None:
    contract = json.loads((ROOT / "config/product-contract.v1.json").read_text(encoding="utf-8"))
    assert [item.value for item in ProductSurface] == contract["product_surfaces"]
    assert [item.value for item in LanguageMode] == contract["language_modes"]
    assert [item.value for item in ModelSelectionMode] == contract["model_selection_modes"]
    assert [item.value for item in TextLayer] == contract["text_layers"]


@pytest.mark.parametrize("worker", WORKERS)
def test_every_worker_exposes_versioned_health(worker: str) -> None:
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("COV_CORE", "COVERAGE_")):
            environment.pop(name)
    protocol_path = str(ROOT / "protocol/python")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (protocol_path, environment.get("PYTHONPATH", "")) if part
    )
    completed = subprocess.run(
        [sys.executable, "worker.py", "--health-check"],
        cwd=ROOT / "workers" / worker,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    status = json.loads(completed.stdout)
    assert status["worker_id"] == worker
    assert status["protocol_version"] == PROTOCOL_VERSION
    assert status["status"] == "ok"
    assert status["capabilities"]
