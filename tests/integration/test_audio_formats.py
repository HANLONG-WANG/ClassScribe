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


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")
def test_streaming_flac_without_duration_is_probed_and_still_enforces_limit(tmp_path: Path) -> None:
    from classscribe.errors import ClassScribeError, ErrorCode

    # A pipe cannot seek back to fill in STREAMINFO's total sample count.
    encoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=44100:duration=2.25",
            "-c:a",
            "flac",
            "-f",
            "flac",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    source = tmp_path / "streamed.flac"
    source.write_bytes(encoded)
    assert int.from_bytes(encoded[18:26], "big") & ((1 << 36) - 1) == 0
    pipeline = FFmpegMediaPipeline()
    assert float(pipeline.probe(source).duration_seconds) == pytest.approx(2.25)
    with pytest.raises(ClassScribeError) as failure:
        pipeline.probe(source, max_duration_seconds=1)
    assert failure.value.code == ErrorCode.AUDIO_TOO_LONG
    assert float(
        pipeline.probe(source, allow_long=True, max_duration_seconds=1).duration_seconds
    ) == pytest.approx(2.25)
