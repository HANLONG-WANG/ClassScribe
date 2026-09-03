from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import WorkerEnvironmentProvisioner, provisioned_worker_command


def source_tree(root: Path) -> Path:
    worker = root / "workers" / "qwen"
    protocol = root / "protocol" / "python" / "classscribe_protocol"
    worker.mkdir(parents=True)
    protocol.mkdir(parents=True)
    (worker / "pyproject.toml").write_text("[tool.uv]\npackage = false\n", encoding="utf-8")
    (worker / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (worker / "worker.py").write_text("print('worker')\n", encoding="utf-8")
    (worker / "adapter.py").write_text("ADAPTER = True\n", encoding="utf-8")
    (worker / "healthcheck.py").write_text("HEALTH = True\n", encoding="utf-8")
    (protocol.parent / "pyproject.toml").write_text(
        "[project]\nname='protocol'\n", encoding="utf-8"
    )
    (protocol / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
    return root


def test_worker_environment_is_lock_addressed_atomic_and_reused(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: Sequence[str], cwd: Path, environment: dict[str, str]) -> None:
        calls.append(tuple(command))
        assert cwd.name == "qwen"
        assert environment["PYTHONNOUSERSITE"] == "1"
        if command[1:4] == ("-m", "venv", "--copies"):
            python = Path(command[4]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            shutil.copy2(sys.executable, python)

    provisioner = WorkerEnvironmentProvisioner(
        source_tree(tmp_path / "source"), tmp_path / "cache", runner=run
    )
    project = provisioner.ensure("qwen")
    assert provisioned_worker_command(project)[1] == str(project / "worker.py")
    assert len(calls) == 2
    assert "--frozen" in calls[1] and "--no-editable" in calls[1]
    metadata = json.loads((project.parent.parent / "complete.json").read_text(encoding="utf-8"))
    assert metadata["worker_id"] == "qwen"
    assert provisioner.ensure("qwen") == project
    assert provisioner.resolve("qwen") == project
    assert len(calls) == 2


def test_worker_environment_rejects_unsafe_or_incomplete_pinned_target(tmp_path: Path) -> None:
    source = source_tree(tmp_path / "source")
    provisioner = WorkerEnvironmentProvisioner(source, tmp_path / "cache")
    with pytest.raises(ValueError):
        provisioner.ensure("../qwen")
    digest = hashlib.sha256((source / "workers/qwen/uv.lock").read_bytes()).hexdigest()
    target = tmp_path / "cache" / "qwen" / digest
    target.mkdir(parents=True)
    with pytest.raises(ClassScribeError) as raised:
        provisioner.ensure("qwen")
    assert raised.value.code is ErrorCode.MODEL_HEALTH_CHECK_FAILED
    with pytest.raises(ClassScribeError, match="not provisioned"):
        provisioner.resolve("qwen")
