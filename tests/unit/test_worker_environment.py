from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import WorkerEnvironmentProvisioner, provisioned_worker_command

ROOT = Path(__file__).resolve().parents[2]


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


def legacy_environment(source: Path, cache: Path, worker_id: str) -> Path:
    lock = source / "workers" / worker_id / "uv.lock"
    lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest()
    target = cache / worker_id / lock_sha
    project = target / "workers" / worker_id
    shutil.copytree(source / "workers" / worker_id, project)
    shutil.copytree(source / "protocol" / "python", target / "protocol" / "python")
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python)
    (target / "complete.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "worker_id": worker_id,
                "lock_sha256": lock_sha,
                "offline_runtime": True,
            }
        ),
        encoding="utf-8",
    )
    return project


def test_worker_environment_is_lock_and_source_addressed_atomic_and_reused(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    uv_configs: list[str] = []

    def run(command: Sequence[str], cwd: Path, environment: dict[str, str]) -> None:
        calls.append(tuple(command))
        assert cwd.name == "qwen"
        assert environment["PYTHONNOUSERSITE"] == "1"
        assert "UV_NO_CONFIG" not in environment
        if "--config-file" in command:
            config = Path(command[command.index("--config-file") + 1])
            assert stat.S_IMODE(config.stat().st_mode) == 0o600
            uv_configs.append(config.read_text(encoding="utf-8"))
            python = Path(environment["UV_PROJECT_ENVIRONMENT"]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)

    provisioner = WorkerEnvironmentProvisioner(
        source_tree(tmp_path / "source"), tmp_path / "cache", runner=run
    )
    project = provisioner.ensure("qwen")
    assert provisioned_worker_command(project)[1] == str(project / "worker.py")
    assert len(calls) == 1
    assert "--frozen" in calls[0] and "--no-editable" in calls[0] and "--preview" in calls[0]
    assert uv_configs == [
        '[extra-build-dependencies]\nclassscribe-protocol = ["hatchling>=1.27,<2"]\n'
    ]
    metadata = json.loads((project.parent.parent / "complete.json").read_text(encoding="utf-8"))
    assert not (project.parent.parent / "uv.toml").exists()
    assert metadata["worker_id"] == "qwen"
    assert len(metadata["source_sha256"]) == 64
    assert project.parents[2].name == hashlib.sha256(
        (tmp_path / "source/workers/qwen/uv.lock").read_bytes()
    ).hexdigest()
    assert provisioner.ensure("qwen") == project
    assert provisioner.resolve("qwen") == project
    assert len(calls) == 1


def test_worker_environment_rejects_unsafe_or_incomplete_pinned_target(tmp_path: Path) -> None:
    source = source_tree(tmp_path / "source")
    calls: list[tuple[str, ...]] = []

    def run(command: Sequence[str], _cwd: Path, environment: dict[str, str]) -> None:
        calls.append(tuple(command))
        python = Path(environment["UV_PROJECT_ENVIRONMENT"]) / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    provisioner = WorkerEnvironmentProvisioner(source, tmp_path / "cache", runner=run)
    with pytest.raises(ValueError):
        provisioner.ensure("../qwen")
    project = provisioner.ensure("qwen")
    (project.parent.parent / "complete.json").unlink()
    with pytest.raises(ClassScribeError) as raised:
        provisioner.ensure("qwen")
    assert raised.value.code is ErrorCode.MODEL_HEALTH_CHECK_FAILED
    with pytest.raises(ClassScribeError, match="not provisioned"):
        provisioner.resolve("qwen")


def test_worker_lock_change_blocks_old_environment_from_runtime_reuse(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: Sequence[str], _cwd: Path, environment: dict[str, str]) -> None:
        calls.append(tuple(command))
        python = Path(environment["UV_PROJECT_ENVIRONMENT"]) / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    source = source_tree(tmp_path / "source")
    provisioner = WorkerEnvironmentProvisioner(source, tmp_path / "cache", runner=run)
    old_project = provisioner.ensure("qwen")
    old_lock_sha = old_project.parents[2].name
    source_lock = source / "workers/qwen/uv.lock"
    source_lock.write_text("version = 2\n", encoding="utf-8")
    new_lock_sha = hashlib.sha256(source_lock.read_bytes()).hexdigest()

    with pytest.raises(ClassScribeError, match="not provisioned"):
        provisioner.resolve("qwen")

    new_project = provisioner.ensure("qwen")
    assert new_project != old_project
    assert old_project.is_dir()
    assert old_lock_sha != new_lock_sha
    assert new_project.parents[2].name == new_lock_sha
    assert provisioner.resolve("qwen") == new_project
    assert len(calls) == 2


def test_worker_source_change_blocks_stale_environment_and_provisions_a_new_copy(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: Sequence[str], _cwd: Path, environment: dict[str, str]) -> None:
        calls.append(tuple(command))
        python = Path(environment["UV_PROJECT_ENVIRONMENT"]) / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    source = source_tree(tmp_path / "source")
    provisioner = WorkerEnvironmentProvisioner(source, tmp_path / "cache", runner=run)
    old_project = provisioner.ensure("qwen")
    old_source_sha = old_project.parent.parent.name
    (source / "workers/qwen/adapter.py").write_text("ADAPTER = 2\n", encoding="utf-8")

    with pytest.raises(ClassScribeError, match="not provisioned"):
        provisioner.resolve("qwen")

    new_project = provisioner.ensure("qwen")
    assert new_project != old_project
    assert old_project.is_dir()
    assert new_project.parent.parent.name != old_source_sha
    assert (new_project / "adapter.py").read_text(encoding="utf-8") == "ADAPTER = 2\n"
    assert provisioner.resolve("qwen") == new_project
    assert len(calls) == 2


def test_unchanged_legacy_lock_only_environment_remains_runtime_compatible(
    tmp_path: Path,
) -> None:
    source = source_tree(tmp_path / "source")
    project = legacy_environment(source, tmp_path / "cache", "qwen")

    def unexpected_run(
        _command: Sequence[str], _cwd: Path, _environment: dict[str, str]
    ) -> None:
        raise AssertionError("a compatible legacy environment must remain read-only")

    provisioner = WorkerEnvironmentProvisioner(
        source, tmp_path / "cache", runner=unexpected_run
    )
    assert provisioner.resolve("qwen") == project
    assert provisioner.ensure("qwen") == project


def test_changed_source_rejects_legacy_copy_and_provisions_source_addressed_environment(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: Sequence[str], _cwd: Path, environment: dict[str, str]) -> None:
        calls.append(tuple(command))
        python = Path(environment["UV_PROJECT_ENVIRONMENT"]) / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    source = source_tree(tmp_path / "source")
    legacy_project = legacy_environment(source, tmp_path / "cache", "qwen")
    (source / "workers/qwen/adapter.py").write_text("ADAPTER = 2\n", encoding="utf-8")
    provisioner = WorkerEnvironmentProvisioner(source, tmp_path / "cache", runner=run)

    with pytest.raises(ClassScribeError, match="not provisioned"):
        provisioner.resolve("qwen")

    project = provisioner.ensure("qwen")
    assert project != legacy_project
    assert project.parent.parent.parent == legacy_project.parent.parent
    assert legacy_project.is_dir()
    assert (project / "adapter.py").read_text(encoding="utf-8") == "ADAPTER = 2\n"
    assert provisioner.resolve("qwen") == project
    assert len(calls) == 1


def test_firered_worker_directly_locks_python_312_fbank_runtime() -> None:
    project = tomllib.loads((ROOT / "workers/firered/pyproject.toml").read_text())
    assert "kaldi-native-fbank==1.19.0" in project["project"]["dependencies"]

    lock = tomllib.loads((ROOT / "workers/firered/uv.lock").read_text())
    packages = {package["name"]: package for package in lock["package"]}
    assert packages["kaldi-native-fbank"]["version"] == "1.19.0"
    worker = packages["classscribe-worker-firered"]
    assert {dependency["name"] for dependency in worker["dependencies"]} >= {
        "fireredasr2s",
        "kaldi-native-fbank",
    }
    requirements = {
        requirement["name"]: requirement
        for requirement in worker["metadata"]["requires-dist"]
    }
    assert requirements["kaldi-native-fbank"]["specifier"] == "==1.19.0"


def test_granite_worker_directly_locks_matching_torchaudio_runtime() -> None:
    project = tomllib.loads((ROOT / "workers/granite/pyproject.toml").read_text())
    assert "torchaudio==2.11.0" in project["project"]["dependencies"]

    lock = tomllib.loads((ROOT / "workers/granite/uv.lock").read_text())
    packages = {package["name"]: package for package in lock["package"]}
    assert packages["torch"]["version"] == "2.14.0"
    assert packages["torchaudio"]["version"] == "2.11.0"
    worker = packages["classscribe-worker-granite"]
    assert {dependency["name"] for dependency in worker["dependencies"]} >= {
        "torch",
        "torchaudio",
    }
    requirements = {
        requirement["name"]: requirement
        for requirement in worker["metadata"]["requires-dist"]
    }
    assert requirements["torchaudio"]["specifier"] == "==2.11.0"


def test_nemotron_worker_locks_official_prompt_model_source() -> None:
    revision = "c9040511b2dbefe64767d9b8853b3a20d63a2cd2"
    repository = "https://github.com/NVIDIA/NeMo.git"
    project = tomllib.loads((ROOT / "workers/nemotron/pyproject.toml").read_text())
    assert "nemo-toolkit[asr]==3.1.0" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["environments"] == ["sys_platform == 'linux'"]
    assert project["tool"]["uv"]["sources"]["nemo-toolkit"] == {
        "git": repository,
        "rev": revision,
    }

    lock = tomllib.loads((ROOT / "workers/nemotron/uv.lock").read_text())
    packages = {package["name"]: package for package in lock["package"]}
    nemo = packages["nemo-toolkit"]
    assert nemo["version"] == "3.1.0+c9040511b2"
    assert nemo["source"] == {"git": f"{repository}?rev={revision}#{revision}"}
    worker = packages["classscribe-worker-nemotron"]
    requirements = {
        requirement["name"]: requirement
        for requirement in worker["metadata"]["requires-dist"]
    }
    assert requirements["nemo-toolkit"] == {
        "name": "nemo-toolkit",
        "extras": ["asr"],
        "git": f"{repository}?rev={revision}",
    }
