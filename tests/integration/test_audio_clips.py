from __future__ import annotations

import array
import subprocess
import threading
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from classscribe.audio.clips import create_recording_clip, extract_clip
from classscribe.audio.media import file_sha256
from classscribe.db.models import Recording
from classscribe.errors import ClassScribeError
from classscribe.paths import AppPaths


def wav(path: Path) -> bytes:
    samples = array.array("h", ((index % 1000) - 500 for index in range(48000)))
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    return samples.tobytes()


@pytest.mark.parametrize("suffix", ["wav", "flac", "m4a"])
def test_actual_non_integral_clip(tmp_path: Path, suffix: str) -> None:
    original = tmp_path / "original.wav"
    pcm = wav(original)
    source = original
    if suffix != "wav":
        source = tmp_path / f"original.{suffix}"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(original), str(source)], check=True)
    digest = file_sha256(source)
    target = tmp_path / "clip.wav"
    assert extract_clip(source, target, 1234, 31987) == 30753
    assert file_sha256(source) == digest
    with wave.open(str(target), "rb") as output:
        result = output.readframes(output.getnframes())
    if suffix in ("wav", "flac"):
        assert result == pcm[1234 * 2 : 31987 * 2]


def test_cancelled_clip_keeps_source(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    wav(source)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        extract_clip(source, tmp_path / "clip.wav", 0, 16000, cancelled=cancel)
    assert not (tmp_path / "clip.wav").exists()
    assert source.is_file()


def test_clip_rejects_end_beyond_decoded_audio(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    wav(source)
    with pytest.raises(ClassScribeError):
        extract_clip(source, tmp_path / "clip.wav", 0, 48001)
    assert not (tmp_path / "clip.wav").exists()


def test_idempotency_and_provenance(database: Any, tmp_path: Path) -> None:
    _, sessions, _ = database
    paths = AppPaths.from_environment({}, home=tmp_path)
    identifier = str(uuid4())
    source = paths.data_path("recordings", identifier, "source", "original.wav")
    source.parent.mkdir(parents=True)
    wav(source)
    with sessions.begin() as session:
        session.add(
            Recording(
                id=identifier,
                source_name="original.wav",
                source_path=str(source),
                source_sha256=file_sha256(source),
                duration_samples=48000,
                sample_rate=16000,
                channels=1,
            )
        )
    key = str(uuid4())
    clip = create_recording_clip(sessions, paths, identifier, key, 1234, 30000)
    assert create_recording_clip(sessions, paths, identifier, key, 1234, 30000) == clip
    with pytest.raises(ClassScribeError, match="different parameters"):
        create_recording_clip(sessions, paths, identifier, key, 1235, 30000)
    with sessions() as session:
        item = session.get(Recording, clip)
        assert item.parent_recording_id == identifier
        assert item.source_start_sample == 1234
        assert item.duration_samples == 28766
    with pytest.raises(ClassScribeError, match="original recording"):
        create_recording_clip(sessions, paths, clip, str(uuid4()), 0, 16000)
