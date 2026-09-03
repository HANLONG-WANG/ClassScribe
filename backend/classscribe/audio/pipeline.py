"""Stage-4 orchestration without importing concrete model runtimes."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from classscribe.audio.lid import FireRedLIDAdapter, LanguageRouter, LanguageRoutingSpan
from classscribe.audio.media import (
    AudioMaster,
    ChannelMixPolicy,
    FFmpegMediaPipeline,
    ImportedMedia,
)
from classscribe.audio.qc import AudioQualityReport, PCMQualityAnalyzer
from classscribe.audio.segmentation import (
    BoundaryCue,
    BoundaryKind,
    StructureWindow,
    TranscriptChunk,
    make_structure_windows,
    make_transcript_chunks,
)
from classscribe.audio.vad import FrameVADAdapter, SpeechRegionResult
from classscribe.contracts import LanguageMode
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import validate_restricted_directory
from classscribe.timeline import SAMPLE_RATE, AudioSpan


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    imported: ImportedMedia
    master: AudioMaster
    quality: AudioQualityReport


@dataclass(frozen=True, slots=True)
class AudioAnalysisArtifacts:
    quality: AudioQualityReport
    speech_regions: tuple[SpeechRegionResult, ...]
    language_spans: tuple[LanguageRoutingSpan, ...]
    structure_windows: tuple[StructureWindow, ...]
    transcript_chunks: tuple[TranscriptChunk, ...]


class AudioPreprocessor:
    def __init__(
        self,
        media: FFmpegMediaPipeline | None = None,
        quality: PCMQualityAnalyzer | None = None,
    ) -> None:
        self.media = media or FFmpegMediaPipeline()
        self.quality = quality or PCMQualityAnalyzer()

    def prepare(
        self,
        source: Path,
        *,
        source_directory: Path,
        master_path: Path,
        mix_policy: ChannelMixPolicy = ChannelMixPolicy.EQUAL,
    ) -> PreparedAudio:
        imported = self.media.import_source(source, source_directory)
        if master_path.parent.is_symlink():
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "master directory may not be a symlink"
            )
        master_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        master_path.parent.chmod(0o700)
        validate_restricted_directory(master_path.parent)
        descriptor, qc_name = tempfile.mkstemp(
            prefix=".channel-qc-", suffix=".wav", dir=master_path.parent
        )
        os.close(descriptor)
        qc_path = Path(qc_name)
        try:
            self.media.make_multichannel_qc_wav(imported.source_path, qc_path)
            channel_metrics = self.quality.analyze_channels(qc_path)
            best_channel = self.quality.choose_best_channel(channel_metrics)
            master = self.media.normalize(
                imported.source_path,
                master_path,
                mix_policy=mix_policy,
                best_channel=best_channel if mix_policy is ChannelMixPolicy.BEST else None,
            )
            report = self.quality.build_report(imported, master, qc_path)
            return PreparedAudio(imported, master, report)
        finally:
            qc_path.unlink(missing_ok=True)

    @staticmethod
    def analyze(
        prepared: PreparedAudio,
        *,
        language_mode: LanguageMode,
        vad: FrameVADAdapter,
        lid: FireRedLIDAdapter | None,
        router: LanguageRouter | None = None,
        extra_boundary_cues: tuple[BoundaryCue, ...] = (),
    ) -> AudioAnalysisArtifacts:
        total = prepared.master.duration_samples
        regions = vad.analyze(str(prepared.master.path), total_samples=total)
        updated_quality = PCMQualityAnalyzer.with_vad_regions(
            prepared.quality, tuple(item.span for item in regions)
        )
        silence_points, pause_cues = _silence_boundaries(regions)
        language_router = router or LanguageRouter()
        if language_mode is LanguageMode.AUTO_MIXED:
            if lid is None:
                raise ValueError("automatic/mixed language mode requires a LID adapter")
            observations = lid.observe(str(prepared.master.path), total)
        else:
            observations = ()
        languages = language_router.route(
            language_mode,
            total,
            observations=observations,
            silence_points=silence_points,
        )
        language_cues = tuple(
            BoundaryCue(item.span.start_sample, BoundaryKind.LANGUAGE_SWITCH, item.confidence_raw)
            for item in languages[1:]
        )
        structures = make_structure_windows(total)
        if regions:
            speech_extent = AudioSpan(regions[0].span.start_sample, regions[-1].span.end_sample)
            chunks = make_transcript_chunks(
                speech_extent, (*pause_cues, *language_cues, *extra_boundary_cues)
            )
        else:
            chunks = ()
        return AudioAnalysisArtifacts(updated_quality, regions, languages, structures, chunks)


def _silence_boundaries(
    regions: tuple[SpeechRegionResult, ...],
) -> tuple[tuple[int, ...], tuple[BoundaryCue, ...]]:
    points: list[int] = []
    cues: list[BoundaryCue] = []
    for left, right in pairwise(regions):
        gap = right.span.start_sample - left.span.end_sample
        if gap <= 0:
            continue
        point = left.span.end_sample + gap // 2
        points.append(point)
        if gap >= 300 * SAMPLE_RATE // 1000:
            kind = (
                BoundaryKind.NATURAL_PAUSE
                if gap <= 800 * SAMPLE_RATE // 1000
                else BoundaryKind.LONG_PAUSE
            )
            cues.append(BoundaryCue(point, kind))
    return tuple(points), tuple(cues)
