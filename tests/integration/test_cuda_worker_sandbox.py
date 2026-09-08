"""Opt-in GPU regression using an already provisioned worker, without model weights."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from classscribe.models.inference import nvidia_devices
from classscribe.worker_sandbox import WorkerSandbox


@pytest.mark.real_model
def test_cuda_and_triton_initialize_inside_the_production_sandbox(tmp_path: Path) -> None:
    value = os.environ.get("CLASSSCRIBE_CUDA_WORKER_PROJECT")
    if not value:
        pytest.skip("set CLASSSCRIBE_CUDA_WORKER_PROJECT to a provisioned CUDA worker")
    project = Path(value).resolve(strict=True)
    devices = nvidia_devices()
    if not devices:
        pytest.skip("no NVIDIA devices exposed to the test process")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    command = WorkerSandbox.detect().command(
        worker_python=project / ".venv/bin/python",
        worker_entrypoint=project / "worker.py",
        model_revision=tmp_path,
        input_audio=audio,
        output_directory=tmp_path,
        socket_directory=tmp_path,
        gpu_devices=devices,
    )
    probe = (
        "import torch; torch.cuda.init(); "
        "x=torch.ones(1,device='cuda:0'); assert x.is_cuda; "
        "from triton.backends.nvidia.driver import CudaUtils; CudaUtils(); "
        "print('CUDA_TRITON_OK', torch.cuda.get_device_name(0))"
    )
    completed = subprocess.run(
        [*command[:-1], "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "CUDA_TRITON_OK" in completed.stdout
