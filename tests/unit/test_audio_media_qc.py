from __future__ import annotations

import hashlib
import math
import stat
import struct
import subprocess
import wave
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from classscribe.audio import (
    SUPPORTED_MEDIA_SUFFIXES,
    AudioPreprocessor,
    ChannelMixPolicy,
    FFmpegMediaPipeline,
    PCMQualityAnalyzer,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.timeline import SAMPLE_RATE


def write_stereo_wave(path: Path, *, seconds: int = 2, sample_rate: int = 48_000) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(seconds * sample_rate):
            quiet = int(500 * math.sin(2 * math.pi * 220 * index / sample_rate))
            clear = int(9_000 * math.sin(2 * math.pi * 330 * index / sample_rate))
            frames.extend(struct.pack("<hh", quiet, clear))
        output.writeframes(frames)
    return path


def test_supported_media_contract_is_complete() -> None:
    assert {
        ".wav",
        ".flac",
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
    } == SUPPORTED_MEDIA_SUFFIXES


def test_real_ffmpeg_import_qc_and_best_channel_master(tmp_path: Path) -> None:
    source = write_stereo_wave(tmp_path / "class.wav")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    prepared = AudioPreprocessor().prepare(
        source,
        source_directory=tmp_path / "job/source",
        master_path=tmp_path / "job/derived/audio_master.wav",
        mix_policy=ChannelMixPolicy.BEST,
    )

    assert prepared.imported.source_name == "class.wav"
    assert prepared.imported.source_sha256 == source_hash
    assert prepared.imported.source_path != source
    assert stat.S_IMODE(prepared.imported.source_path.stat().st_mode) == 0o400
    assert prepared.imported.source_path.read_bytes() == source.read_bytes()
    assert prepared.master.duration_samples == 2 * SAMPLE_RATE
    assert stat.S_IMODE(prepared.master.path.stat().st_mode) == 0o400
    with wave.open(str(prepared.master.path), "rb") as recording:
        assert recording.getframerate() == SAMPLE_RATE
        assert recording.getnchannels() == 1
        assert recording.getsampwidth() == 2
        assert recording.getcomptype() == "NONE"
        assert recording.getnframes() == 2 * SAMPLE_RATE
    assert len(prepared.quality.channels) == 2
    assert prepared.quality.selected_channel == 1
    assert prepared.quality.source_sha256 == source_hash
    assert (
        prepared.quality.master_sha256
        == hashlib.sha256(prepared.master.path.read_bytes()).hexdigest()
    )
    assert prepared.quality.processing_policy == "no_denoise_no_aec_no_aggressive_normalization"
    assert not list((tmp_path / "job/derived").glob(".channel-qc-*"))


def test_quality_report_warns_but_does_not_reject_clipping_dc_and_duration_mismatch(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clipped.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(struct.pack("<h", 32_767) * SAMPLE_RATE)
    prepared = AudioPreprocessor().prepare(
        source,
        source_directory=tmp_path / "source",
        master_path=tmp_path / "derived/audio_master.wav",
    )
    imported_with_wrong_duration = replace(
        prepared.imported,
        metadata=replace(
            prepared.imported.metadata,
            duration_seconds=prepared.imported.metadata.duration_seconds + 2,
        ),
    )
    report = PCMQualityAnalyzer().build_report(
        imported_with_wrong_duration, prepared.master, prepared.master.path
    )
    assert report.channels[0].peak > 0.99
    assert report.channels[0].clipping_ratio == 1.0
    assert report.channels[0].dc_offset > 0.99
    assert report.abnormal_interruption is True
    expected_warnings = {
        "clipping_detected",
        "dc_offset_detected",
        "abnormal_interruption_or_duration_mismatch",
    }
    assert set(report.warnings) >= expected_warnings


def test_unsupported_or_symlinked_sources_fail_safely(tmp_path: Path) -> None:
    unsupported = tmp_path / "audio.exe"
    unsupported.write_bytes(b"not audio")
    with pytest.raises(ClassScribeError) as suffix:
        FFmpegMediaPipeline().probe(unsupported)
    assert suffix.value.code is ErrorCode.UNSUPPORTED_MEDIA

    real = write_stereo_wave(tmp_path / "real.wav")
    linked = tmp_path / "linked.wav"
    linked.symlink_to(real)
    with pytest.raises(ClassScribeError) as symlink:
        FFmpegMediaPipeline().probe(linked)
    assert symlink.value.code is ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT


def test_missing_ffprobe_is_an_explicit_probe_error(tmp_path: Path) -> None:
    source = write_stereo_wave(tmp_path / "audio.wav")

    def unavailable(_command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("ffprobe missing")

    pipeline = FFmpegMediaPipeline(runner=unavailable)
    with pytest.raises(ClassScribeError) as raised:
        pipeline.probe(source)
    assert raised.value.code is ErrorCode.MEDIA_PROBE_FAILED


def test_channel_analyzer_is_memory_bounded_and_selects_clear_channel(tmp_path: Path) -> None:
    source = write_stereo_wave(tmp_path / "stereo.wav", seconds=3)
    channels = PCMQualityAnalyzer().analyze_channels(source)
    assert len(channels) == 2
    assert channels[1].rms > channels[0].rms * 10
    assert PCMQualityAnalyzer.choose_best_channel(channels) == 1
    assert all(0 <= channel.background_music_probability <= 1 for channel in channels)


def test_equal_downmix_is_explicit_and_does_not_add_enhancement_filters(tmp_path: Path) -> None:
    source = tmp_path / "anti-phase.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for index in range(SAMPLE_RATE):
            value = int(8_000 * math.sin(2 * math.pi * 220 * index / SAMPLE_RATE))
            frames.extend(struct.pack("<hh", value, -value))
        output.writeframes(frames)
    prepared = AudioPreprocessor().prepare(
        source,
        source_directory=tmp_path / "source",
        master_path=tmp_path / "derived/audio_master.wav",
        mix_policy=ChannelMixPolicy.EQUAL,
    )
    master_quality = PCMQualityAnalyzer().analyze_channels(prepared.master.path)[0]
    assert master_quality.rms < 1 / 32768
    assert prepared.quality.processing_policy == "no_denoise_no_aec_no_aggressive_normalization"


def test_ffmpeg_reports_output_audio_time(tmp_path: Path) -> None:
    from typing import Any

    from classscribe.activity import ActivityReporter, activity_scope

    events: list[dict[str, Any]] = []
    with activity_scope(ActivityReporter(lambda **payload: events.append(payload))):
        master = FFmpegMediaPipeline().normalize(
            write_stereo_wave(tmp_path / "lecture.wav"),
            tmp_path / "derived/audio_master.wav",
        )
    assert events[0]["operation"] == "normalize_audio"
    assert events[0]["completed"] == 0
    assert events[-1]["completed"] == pytest.approx(master.duration_samples / SAMPLE_RATE)
    assert events[-1]["total"] == 2
    assert events[-1]["unit"] == "seconds"
