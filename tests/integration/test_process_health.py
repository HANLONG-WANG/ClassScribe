from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from classscribe.api.app import health_status as core_health
from classscribe_protocol import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[2]
PROCESSES = (
    ("ibus/dictationd/classscribe_dictationd/main.py", "classscribe-dictationd"),
    ("ibus/engine/classscribe_ibus_engine/main.py", "classscribe-ibus-engine"),
    ("ibus/hotkey_portal/classscribe_hotkey_portal/main.py", "classscribe-hotkey-portal"),
)


def subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("COV_CORE", "COVERAGE_")):
            environment.pop(name)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(ROOT / "protocol/python"), environment.get("PYTHONPATH", "")) if part
    )
    return environment


def test_core_health_contract() -> None:
    status = core_health()
    assert status["service"] == "classscribe-core"
    assert status["status"] == "ok"
    assert status["protocol_version"] == PROTOCOL_VERSION


@pytest.mark.parametrize(("script", "service"), PROCESSES)
def test_desktop_process_health_contract(script: str, service: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / script), "--health-check"],
        env=subprocess_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    status = json.loads(completed.stdout)
    assert status["service"] == service
    assert status["status"] == "ok"
    assert status["protocol_version"] == PROTOCOL_VERSION


def test_ibus_engine_declares_it_never_loads_models() -> None:
    script, _ = PROCESSES[1]
    completed = subprocess.run(
        [sys.executable, str(ROOT / script), "--health-check"],
        env=subprocess_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["loads_models"] is False
