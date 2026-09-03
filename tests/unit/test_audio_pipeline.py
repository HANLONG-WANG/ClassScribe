from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from classscribe.audio import (
    AudioMaster,
    AudioPreprocessor,
    AudioQualityReport,
    ChannelQuality,
    FireRedLIDAdapter,
    FireRedVADAdapter,
    ImportedMedia,
    MediaMetadata,
    PreparedAudio,
    VADFrame,
)
from classscribe.audio.vad import callable_backend
from classscribe.contracts import LanguageMode
from classscribe.timeline import SAMPLE_RATE, AudioSpan


def prepared(path: Path, total: int) -> PreparedAudio:
    channel = ChannelQuality(0, 0.5, 0.1, -20.0, 0, 0, 0.2, 0.8, 15.0, 0.1)
    imported = ImportedMedia(
        "audio.wav",
        path,
        "a" * 64,
        100,
        MediaMetadata("wav", "pcm_s16le", Decimal(total) / SAMPLE_RATE, SAMPLE_RATE, 1, None),
    )
    master = AudioMaster(path, "b" * 64, total)
    report = AudioQualityReport(
        total,
        total / SAMPLE_RATE,
        SAMPLE_RATE,
        1,
        "pcm_s16le",
        "wav",
        SAMPLE_RATE,
        1,
        "pcm_s16le",
        imported.source_sha256,
        master.sha256,
        (channel,),
        0,
        0.8,
        0.2,
        False,
        (),
    )
    return PreparedAudio(imported, master, report)


def test_pipeline_manual_language_never_calls_lid_and_preserves_absolute_offsets(
    tmp_path: Path,
) -> None:
    total = 45 * SAMPLE_RATE
    audio = tmp_path / "audio_master.wav"
    audio.write_bytes(b"fixture path only")
    frames = (
        VADFrame(2 * SAMPLE_RATE, 20 * SAMPLE_RATE, 0.9),
        VADFrame(20 * SAMPLE_RATE, 21 * SAMPLE_RATE, 0.1),
        VADFrame(21 * SAMPLE_RATE, 44 * SAMPLE_RATE, 0.85),
    )
    vad = FireRedVADAdapter(
        callable_backend(lambda _path, _streaming: frames),
        min_speech_ms=100,
        min_silence_ms=300,
        padding_ms=0,
    )
    artifacts = AudioPreprocessor.analyze(
        prepared(audio, total), language_mode=LanguageMode.JAPANESE, vad=vad, lid=None
    )
    assert artifacts.language_spans[0].language is LanguageMode.JAPANESE
    assert artifacts.language_spans[0].decision["global_lid_bypassed"] is True
    assert artifacts.quality.speech_ratio == pytest.approx(41 / 45)
    assert artifacts.quality.silence_ratio == pytest.approx(4 / 45)
    assert artifacts.speech_regions[0].span.start_sample == 2 * SAMPLE_RATE
    assert artifacts.speech_regions[-1].span.end_sample == 44 * SAMPLE_RATE
    assert artifacts.structure_windows[0].span.start_sample == 0
    assert artifacts.structure_windows[-1].span.end_sample == total
    assert artifacts.transcript_chunks[0].core_span.start_sample == 2 * SAMPLE_RATE
    assert artifacts.transcript_chunks[-1].core_span.end_sample == 44 * SAMPLE_RATE


def test_ninety_minute_master_runs_vad_lid_and_both_slice_policies_without_drift(
    tmp_path: Path,
) -> None:
    total = 90 * 60 * SAMPLE_RATE
    audio = tmp_path / "audio_master.wav"
    audio.write_bytes(b"fixture path only")
    vad = FireRedVADAdapter(
        callable_backend(lambda _path, _streaming: (VADFrame(0, total, 0.99),)),
        min_speech_ms=100,
        padding_ms=0,
    )

    class JapaneseBackend:
        def probabilities(self, audio_path: str, span: AudioSpan) -> dict[LanguageMode, float]:
            del audio_path, span
            return {
                LanguageMode.CHINESE: 0.04,
                LanguageMode.JAPANESE: 0.92,
                LanguageMode.ENGLISH: 0.04,
            }

    artifacts = AudioPreprocessor.analyze(
        prepared(audio, total),
        language_mode=LanguageMode.AUTO_MIXED,
        vad=vad,
        lid=FireRedLIDAdapter(JapaneseBackend()),
    )
    assert artifacts.speech_regions[0].span.end_sample == total
    assert len(artifacts.language_spans) == 1
    assert artifacts.language_spans[0].language is LanguageMode.JAPANESE
    assert artifacts.language_spans[0].span.end_sample == total
    assert artifacts.language_spans[0].decision["observations"]
    assert artifacts.structure_windows[-1].span.end_sample == total
    assert artifacts.transcript_chunks[-1].core_span.end_sample == total
    assert all(chunk.core_span.end_sample <= total for chunk in artifacts.transcript_chunks)
