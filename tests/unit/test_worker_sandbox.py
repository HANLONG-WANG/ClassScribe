from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.worker_sandbox import WorkerSandbox


def test_remote_code_worker_sees_only_explicit_paths(tmp_path: Path) -> None:
    bubblewrap = tmp_path / "bwrap"
    bubblewrap.write_text("binary", encoding="utf-8")
    worker = tmp_path / "worker-env"
    python = worker / "bin/python"
    entrypoint = worker / "worker.py"
    python.parent.mkdir(parents=True)
    python.write_text("binary", encoding="utf-8")
    entrypoint.write_text("print('worker')", encoding="utf-8")
    model = tmp_path / "models/revision"
    output = tmp_path / "job/derived"
    socket = tmp_path / "runtime"
    for directory in (model, output, socket):
        directory.mkdir(parents=True)
    audio = tmp_path / "job/source.wav"
    audio.write_bytes(b"RIFF")

    command = WorkerSandbox(bubblewrap).command(
        worker_python=python,
        worker_entrypoint=entrypoint,
        model_revision=model,
        input_audio=audio,
        output_directory=output,
        socket_directory=socket,
        worker_arguments=("--socket", "/run/classscribe/worker.sock"),
    )
    rendered = "\0".join(command)
    assert "--unshare-all" in command
    assert "--clearenv" in command
    assert "HOME\0/home/classscribe" in rendered
    assert "USER\0classscribe" in rendered
    assert "LOGNAME\0classscribe" in rendered
    assert "HF_HUB_OFFLINE\0" + "1" in rendered
    assert "TRANSFORMERS_OFFLINE\0" + "1" in rendered
    assert f"{model}\0/model" in rendered
    assert f"{audio}\0/input/audio" in rendered
    assert f"{output}\0/output" in rendered
    assert f"{socket}\0/run/classscribe" in rendered
    assert str(tmp_path / "job") not in command
    assert "/home" in command
    assert "--dir\0/home/classscribe" in rendered
    assert "/etc/passwd" not in command
    assert str(Path.home()) not in command
    assert "--ro-bind\0/home" not in rendered


def test_provisioned_project_layout_binds_worker_and_venv_together(tmp_path: Path) -> None:
    bubblewrap = tmp_path / "bwrap"
    bubblewrap.write_text("binary", encoding="utf-8")
    worker = tmp_path / "worker-env"
    python = worker / ".venv/bin/python"
    entrypoint = worker / "worker.py"
    python.parent.mkdir(parents=True)
    python.write_text("binary", encoding="utf-8")
    entrypoint.write_text("print('worker')", encoding="utf-8")
    model = tmp_path / "models/revision"
    output = tmp_path / "job/derived"
    socket = tmp_path / "runtime"
    for directory in (model, output, socket):
        directory.mkdir(parents=True)
    audio = tmp_path / "job/source.wav"
    audio.write_bytes(b"RIFF")

    command = WorkerSandbox(bubblewrap).command(
        worker_python=python,
        worker_entrypoint=entrypoint,
        model_revision=model,
        input_audio=audio,
        output_directory=output,
        socket_directory=socket,
    )

    rendered = "\0".join(command)
    assert f"{worker}\0/worker" in rendered
    assert "/worker/.venv/bin/python" in command
    assert "/worker/worker.py" in command


def test_worker_sandbox_rejects_symlinked_input(tmp_path: Path) -> None:
    bubblewrap = tmp_path / "bwrap"
    bubblewrap.write_text("binary", encoding="utf-8")
    worker = tmp_path / "worker"
    (worker / "bin").mkdir(parents=True)
    python = worker / "bin/python"
    entrypoint = worker / "worker.py"
    python.write_text("binary", encoding="utf-8")
    entrypoint.write_text("worker", encoding="utf-8")
    model = tmp_path / "model"
    output = tmp_path / "output"
    socket = tmp_path / "socket"
    for directory in (model, output, socket):
        directory.mkdir()
    real_audio = tmp_path / "audio.wav"
    real_audio.write_bytes(b"audio")
    linked_audio = tmp_path / "linked.wav"
    linked_audio.symlink_to(real_audio)

    with pytest.raises(ClassScribeError) as raised:
        WorkerSandbox(bubblewrap).command(
            worker_python=python,
            worker_entrypoint=entrypoint,
            model_revision=model,
            input_audio=linked_audio,
            output_directory=output,
            socket_directory=socket,
        )
    assert raised.value.code is ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT
