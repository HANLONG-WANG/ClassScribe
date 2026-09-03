"""Memory-bounded PCM quality analysis used for warnings and routing only."""

from __future__ import annotations

import math
import sys
import wave
from array import array
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any

from classscribe.audio.media import AudioMaster, ImportedMedia
from classscribe.timeline import SAMPLE_RATE, AudioSpan


@dataclass(frozen=True, slots=True)
class ChannelQuality:
    channel: int
    peak: float
    rms: float
    lufs_approx: float | None
    clipping_ratio: float
    dc_offset: float
    silence_ratio: float
    speech_ratio: float
    snr_db_approx: float | None
    background_music_probability: float


@dataclass(frozen=True, slots=True)
class AudioQualityReport:
    duration_samples: int
    duration_seconds: float
    source_sample_rate: int
    source_channels: int
    source_codec: str
    source_format: str
    master_sample_rate: int
    master_channels: int
    master_codec: str
    source_sha256: str
    master_sha256: str
    channels: tuple[ChannelQuality, ...]
    selected_channel: int
    speech_ratio: float
    silence_ratio: float
    abnormal_interruption: bool
    warnings: tuple[str, ...]
    processing_policy: str = "no_denoise_no_aec_no_aggressive_normalization"

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "channels": [asdict(channel) for channel in self.channels],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class _Accumulator:
    count: int = 0
    total: int = 0
    square_total: int = 0
    peak: int = 0
    clipped: int = 0
    zero_crossings: int = 0
    previous: int | None = None

    def add(self, sample: int) -> None:
        self.count += 1
        self.total += sample
        self.square_total += sample * sample
        self.peak = max(self.peak, abs(sample))
        if abs(sample) >= 32_735:
            self.clipped += 1
        if self.previous is not None and (
            self.previous < 0 <= sample or self.previous >= 0 > sample
        ):
            self.zero_crossings += 1
        self.previous = sample


class PCMQualityAnalyzer:
    """Analyze PCM WAV in bounded chunks; no enhancement is performed."""

    def __init__(self, *, silence_threshold: float = 0.003, window_ms: int = 100) -> None:
        if not 0 < silence_threshold < 1 or window_ms <= 0:
            raise ValueError("invalid quality-analysis parameters")
        self.silence_threshold = silence_threshold
        self.window_ms = window_ms

    def analyze_channels(self, path: Path) -> tuple[ChannelQuality, ...]:
        with wave.open(str(path), "rb") as recording:
            if recording.getsampwidth() != 2 or recording.getcomptype() != "NONE":
                raise ValueError("quality analysis requires uncompressed 16-bit PCM WAV")
            channels = recording.getnchannels()
            rate = recording.getframerate()
            if channels <= 0 or rate <= 0:
                raise ValueError("invalid PCM dimensions")
            accumulators = [_Accumulator() for _ in range(channels)]
            window_frames = max(1, rate * self.window_ms // 1000)
            window_squares = [0] * channels
            window_count = 0
            window_rms: list[list[float]] = [[] for _ in range(channels)]
            while True:
                raw = recording.readframes(min(window_frames - window_count, 8192))
                if not raw:
                    break
                samples = array("h")
                samples.frombytes(raw)
                if sys.byteorder != "little":
                    samples.byteswap()
                frames = len(samples) // channels
                for frame in range(frames):
                    base = frame * channels
                    for channel in range(channels):
                        sample = samples[base + channel]
                        accumulators[channel].add(sample)
                        window_squares[channel] += sample * sample
                    window_count += 1
                    if window_count == window_frames:
                        self._close_window(window_squares, window_count, window_rms)
                        window_squares = [0] * channels
                        window_count = 0
            if window_count:
                self._close_window(window_squares, window_count, window_rms)
        return tuple(
            self._metrics(index, accumulator, window_rms[index])
            for index, accumulator in enumerate(accumulators)
        )

    @staticmethod
    def _close_window(squares: list[int], count: int, destination: list[list[float]]) -> None:
        for channel, square_total in enumerate(squares):
            destination[channel].append(math.sqrt(square_total / count) / 32768.0)

    def _metrics(
        self, channel: int, accumulator: _Accumulator, windows: list[float]
    ) -> ChannelQuality:
        if accumulator.count == 0:
            return ChannelQuality(channel, 0, 0, None, 0, 0, 1, 0, None, 0)
        rms = math.sqrt(accumulator.square_total / accumulator.count) / 32768.0
        peak = accumulator.peak / 32768.0
        dc = accumulator.total / accumulator.count / 32768.0
        silence_windows = sum(value < self.silence_threshold for value in windows)
        silence_ratio = silence_windows / len(windows) if windows else 1.0
        speech_ratio = 1.0 - silence_ratio
        non_silent = sorted(value for value in windows if value >= self.silence_threshold)
        noise_windows = sorted(windows)[: max(1, len(windows) // 10)] if windows else []
        noise_rms = median(noise_windows) if noise_windows else 0.0
        signal_rms = median(non_silent) if non_silent else 0.0
        snr = 20 * math.log10(signal_rms / max(noise_rms, 1 / 32768)) if signal_rms > 0 else None
        lufs = 20 * math.log10(rms) - 0.691 if rms > 0 else None
        energy_mean = sum(windows) / len(windows) if windows else 0
        variation = (
            math.sqrt(sum((value - energy_mean) ** 2 for value in windows) / len(windows))
            / max(energy_mean, 1e-9)
            if windows
            else 0
        )
        zcr = accumulator.zero_crossings / accumulator.count
        tonal = 1.0 if 0.02 <= zcr <= 0.35 else 0.25
        stability = max(0.0, min(1.0, 1.0 - variation))
        music_probability = max(0.0, min(1.0, speech_ratio * stability * tonal))
        return ChannelQuality(
            channel=channel,
            peak=peak,
            rms=rms,
            lufs_approx=lufs,
            clipping_ratio=accumulator.clipped / accumulator.count,
            dc_offset=dc,
            silence_ratio=silence_ratio,
            speech_ratio=speech_ratio,
            snr_db_approx=snr,
            background_music_probability=music_probability,
        )

    @staticmethod
    def choose_best_channel(channels: tuple[ChannelQuality, ...]) -> int:
        if not channels:
            raise ValueError("at least one channel is required")
        return max(
            channels,
            key=lambda item: (
                item.speech_ratio + item.rms - 4 * item.clipping_ratio - abs(item.dc_offset)
            ),
        ).channel

    def build_report(
        self,
        imported: ImportedMedia,
        master: AudioMaster,
        channel_pcm: Path,
        *,
        speech_regions: tuple[AudioSpan, ...] = (),
    ) -> AudioQualityReport:
        channels = self.analyze_channels(channel_pcm)
        selected = self.choose_best_channel(channels)
        if speech_regions:
            speech_samples = sum(region.duration_samples for region in speech_regions)
            speech_ratio = min(1.0, speech_samples / max(master.duration_samples, 1))
        else:
            speech_ratio = channels[selected].speech_ratio
        silence_ratio = 1.0 - speech_ratio
        reported_samples = imported.metadata.duration_seconds * SAMPLE_RATE
        abnormal = abs(reported_samples - master.duration_samples) > SAMPLE_RATE // 2
        warnings: list[str] = []
        chosen = channels[selected]
        if chosen.clipping_ratio > 0.001:
            warnings.append("clipping_detected")
        if abs(chosen.dc_offset) > 0.02:
            warnings.append("dc_offset_detected")
        if speech_ratio < 0.05:
            warnings.append("very_low_speech_ratio")
        if chosen.background_music_probability >= 0.65:
            warnings.append("background_music_likely")
        if abnormal:
            warnings.append("abnormal_interruption_or_duration_mismatch")
        return AudioQualityReport(
            duration_samples=master.duration_samples,
            duration_seconds=master.duration_samples / SAMPLE_RATE,
            source_sample_rate=imported.metadata.sample_rate,
            source_channels=imported.metadata.channels,
            source_codec=imported.metadata.codec_name,
            source_format=imported.metadata.format_name,
            master_sample_rate=master.sample_rate,
            master_channels=master.channels,
            master_codec=master.codec,
            source_sha256=imported.source_sha256,
            master_sha256=master.sha256,
            channels=channels,
            selected_channel=selected,
            speech_ratio=speech_ratio,
            silence_ratio=silence_ratio,
            abnormal_interruption=abnormal,
            warnings=tuple(warnings),
        )

    @staticmethod
    def with_vad_regions(
        report: AudioQualityReport, regions: tuple[AudioSpan, ...]
    ) -> AudioQualityReport:
        """Replace amplitude occupancy with final VAD occupancy; retain warning-only policy."""

        speech_samples = sum(region.duration_samples for region in regions)
        speech_ratio = min(1.0, speech_samples / max(report.duration_samples, 1))
        warnings = [item for item in report.warnings if item != "very_low_speech_ratio"]
        if speech_ratio < 0.05:
            warnings.append("very_low_speech_ratio")
        return replace(
            report,
            speech_ratio=speech_ratio,
            silence_ratio=1.0 - speech_ratio,
            warnings=tuple(warnings),
        )
