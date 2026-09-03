from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from classscribe.audio import FFmpegMediaPipeline
from classscribe.timeline import SAMPLE_RATE

FORMATS = {
    ".wav": "pcm_s16le",
    ".flac": "flac",
    ".mp3": "libmp3lame",
    ".m4a": "aac",
    ".aac": "aac",
    ".ogg": "libvorbis",
    ".opus": "libopus",
    ".mp4": "aac",
    ".mov": "aac",
    ".mkv": "flac",
    ".webm": "libopus",
}


def write_source(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(
            b"".join(
                struct.pack("<h", int(4_000 * math.sin(2 * math.pi * 220 * index / SAMPLE_RATE)))
                for index in range(SAMPLE_RATE // 4)
            )
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")
@pytest.mark.parametrize(("suffix", "codec"), FORMATS.items())
def test_every_supported_container_probes_and_normalizes(
    tmp_path: Path, suffix: str, codec: str
) -> None:
    source = tmp_path / "seed.wav"
    write_source(source)
    encoded = tmp_path / f"input{suffix}"
    if suffix == ".wav":
        shutil.copyfile(source, encoded)
    else:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-c:a",
                codec,
                "-y",
                str(encoded),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    pipeline = FFmpegMediaPipeline()
    metadata = pipeline.probe(encoded)
    master = pipeline.normalize(encoded, tmp_path / f"master-{suffix[1:]}/audio_master.wav")
    assert metadata.channels == 1
    assert 0 < master.duration_samples <= SAMPLE_RATE
    with wave.open(str(master.path), "rb") as recording:
        assert recording.getframerate() == SAMPLE_RATE
        assert recording.getnchannels() == 1
