from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from classscribe.models.worker_process import WorkerProcess, WorkerProcessSpec, worker_command
from classscribe_protocol import Priority, RPCRequest

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "whisper_tiny_reference"
REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"


@pytest.mark.real_model
def test_real_neural_asr_load_transcribe_unload(tmp_path: Path) -> None:
    model_value = os.environ.get("CLASSSCRIBE_REAL_MODEL_PATH")
    if not model_value:
        pytest.skip("set CLASSSCRIBE_REAL_MODEL_PATH to the pinned local checkpoint")
    model_path = Path(model_value).resolve(strict=True)
    if not (model_path / "model.bin").is_file():
        pytest.fail("real-model path does not contain the pinned CTranslate2 model.bin")
    if shutil.which("espeak-ng") is None:
        pytest.skip("espeak-ng is required to synthesize the deterministic offline test phrase")
    audio_root = tmp_path / "data"
    audio_root.mkdir()
    raw = audio_root / "speech-raw.wav"
    canonical = audio_root / "speech.wav"
    subprocess.run(
        [
            "espeak-ng",
            "-v",
            "en-us",
            "-s",
            "140",
            "-w",
            str(raw),
            "Class Scribe verifies real speech recognition.",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(canonical),
        ],
        check=True,
    )
    raw.unlink()
    canonical.chmod(0o400)
    with wave.open(str(canonical), "rb") as audio:
        sample_count = audio.getnframes()

    async def scenario() -> None:
        worker = WorkerProcess(
            WorkerProcessSpec(
                worker_id="moss_en",
                command=worker_command(ROOT, "moss_en"),
                socket_path=tmp_path / "runtime/moss-en.sock",
                data_roots=(audio_root,),
            )
        )
        await worker.start(timeout_seconds=30)
        try:
            loaded = await worker.call(
                RPCRequest(
                    "real-load",
                    "real-e2e",
                    60_000,
                    Priority.BACKGROUND,
                    "load",
                    {
                        "model_id": MODEL_ID,
                        "model_revision": REVISION,
                        "model_path": str(model_path),
                    },
                )
            )
            assert loaded.ok
            assert loaded.result["real_model"] is True
            result = await worker.call(
                RPCRequest(
                    "real-transcribe",
                    "real-e2e",
                    60_000,
                    Priority.BACKGROUND,
                    "transcribe_batch",
                    {
                        "audio_path": str(canonical),
                        "start_sample": 0,
                        "end_sample": sample_count,
                        "sample_rate": 16_000,
                        "language": "en",
                        "hotwords": ["ClassScribe"],
                    },
                )
            )
            assert result.ok
            assert result.model_id == MODEL_ID
            assert result.model_revision == REVISION
            assert result.normalized_text
            assert result.segments
            assert all(
                0 <= item["start_sample"] < item["end_sample"] <= sample_count
                for item in result.segments
            )
            assert result.metrics["backend"] == "faster_whisper_cpu_int8"
            unloaded = await worker.call(
                RPCRequest(
                    "real-unload",
                    "real-e2e",
                    10_000,
                    Priority.BACKGROUND,
                    "unload",
                    {},
                )
            )
            assert unloaded.ok
            health = await worker.call(
                RPCRequest(
                    "post-unload-health",
                    "real-e2e",
                    1000,
                    Priority.BACKGROUND,
                    "health",
                    {},
                )
            )
            assert health.result["loaded"] is False
        finally:
            await worker.stop()

    asyncio.run(scenario())
