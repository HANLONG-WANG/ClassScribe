"""Fault-isolated long-form structural transcription pipeline."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise
from pathlib import Path

from classscribe_protocol import RPCRequest, RPCResponse

from classscribe.activity import report_activity
from classscribe.audio.segmentation import (
    BoundaryCue,
    BoundaryKind,
    StructureWindow,
    TranscriptChunk,
    make_transcript_chunks,
)
from classscribe.config import ClassroomConfig
from classscribe.structure.anomaly import StructureHealthReport, inspect_structure_result
from classscribe.structure.diarization import (
    PyannoteResult,
    apply_pyannote_fallback,
    build_pyannote_request,
    parse_pyannote_response,
)
from classscribe.structure.models import (
    STRUCTURE_TEXT_ROLE,
    StructureResult,
    StructureSegment,
    build_moss_structure_request,
    parse_moss_structure_response,
)
from classscribe.structure.persistence import (
    SpeakerTimelineSpan,
    StructureRepository,
    speaker_timeline_from_diarization,
    speaker_timeline_from_segments,
)
from classscribe.structure.stitching import (
    DedupDiagnostic,
    EmbeddingObservation,
    GlobalSpeakerTracker,
    SpeakerMatchDiagnostic,
    deduplicate_structure_segments,
    infer_overlap_constraints,
)
from classscribe.timeline import SAMPLE_RATE, AudioSpan

RPCInvoker = Callable[[RPCRequest], Awaitable[RPCResponse]]


class WindowStructurePath(StrEnum):
    MOSS = "moss_structure"
    PYANNOTE_FALLBACK = "pyannote_fallback_with_coarse_moss_text"
    MOSS_COARSE = "moss_coarse_after_pyannote_failure"
    COARSE_VAD = "coarse_vad_after_worker_failure"
    SILENCE = "no_speech"


@dataclass(frozen=True, slots=True)
class WindowStructureOutcome:
    window: StructureWindow
    path: WindowStructurePath
    segments: tuple[StructureSegment, ...]
    speaker_spans: tuple[SpeakerTimelineSpan, ...]
    health: StructureHealthReport
    moss_attempts: int
    pyannote_attempts: int
    errors: tuple[str, ...]
    speaker_mapping: dict[str, str]
    speaker_diagnostics: tuple[SpeakerMatchDiagnostic, ...]
    overlap_links: int
    speaker_id_switches: int


@dataclass(frozen=True, slots=True)
class StructurePipelineResult:
    segments: tuple[StructureSegment, ...]
    windows: tuple[WindowStructureOutcome, ...]
    dedup_diagnostics: tuple[DedupDiagnostic, ...]
    transcript_chunks: tuple[TranscriptChunk, ...]

    @property
    def speaker_id_switches(self) -> int:
        return sum(item.speaker_id_switches for item in self.windows)

    @property
    def overlap_links(self) -> int:
        return sum(item.overlap_links for item in self.windows)

    @property
    def speaker_id_switch_rate(self) -> float:
        return self.speaker_id_switches / self.overlap_links if self.overlap_links else 0.0

    def diagnostic_payload(self) -> dict[str, object]:
        """JSON-ready data for the later WebUI speaker/timeline diagnostic view."""

        return {
            "window_count": len(self.windows),
            "final_segment_count": len(self.segments),
            "transcript_chunk_count": len(self.transcript_chunks),
            "deduplicated_segment_count": len(self.dedup_diagnostics),
            "overlap_links": self.overlap_links,
            "speaker_id_switches": self.speaker_id_switches,
            "speaker_id_switch_rate": self.speaker_id_switch_rate,
            "windows": [
                {
                    "ordinal": item.window.ordinal,
                    "start_sample": item.window.span.start_sample,
                    "end_sample": item.window.span.end_sample,
                    "path": item.path.value,
                    "moss_attempts": item.moss_attempts,
                    "pyannote_attempts": item.pyannote_attempts,
                    "health": {
                        "needs_fallback": item.health.needs_fallback,
                        "anomalies": [value.value for value in item.health.anomalies],
                        "metrics": item.health.metrics,
                    },
                    "errors": list(item.errors),
                    "speaker_mapping": item.speaker_mapping,
                    "speaker_matches": [value.as_dict() for value in item.speaker_diagnostics],
                }
                for item in self.windows
            ],
            "dedup": [item.as_dict() for item in self.dedup_diagnostics],
        }


class StructurePipeline:
    """Process each window independently and stitch only after durable evidence validation."""

    def __init__(
        self,
        config: ClassroomConfig,
        moss_call: RPCInvoker,
        pyannote_call: RPCInvoker,
        *,
        repository: StructureRepository | None = None,
        max_window_attempts: int = 2,
        speaker_tracker: GlobalSpeakerTracker | None = None,
    ) -> None:
        if max_window_attempts < 1:
            raise ValueError("a structure window needs at least one attempt")
        self.config = config
        self.moss_call = moss_call
        self.pyannote_call = pyannote_call
        self.repository = repository
        self.max_window_attempts = max_window_attempts
        self.speaker_tracker = speaker_tracker or GlobalSpeakerTracker()

    async def process(
        self,
        *,
        job_id: str,
        audio_path: Path,
        windows: tuple[StructureWindow, ...],
        speech_spans: tuple[AudioSpan, ...],
        language: str = "auto",
        hotwords: tuple[str, ...] = (),
        boundary_cues: tuple[BoundaryCue, ...] = (),
    ) -> StructurePipelineResult:
        outcomes: list[WindowStructureOutcome] = []
        previous_stitched: tuple[StructureSegment, ...] = ()
        for window in windows:
            report_activity(
                "structure_window",
                window_ordinal=window.ordinal + 1,
                window_total=len(windows),
                windows_completed=len(outcomes),
                start_sample=window.span.start_sample,
                end_sample=window.span.end_sample,
            )
            moss, health, moss_attempts, moss_errors = await self._run_moss(
                job_id, audio_path, window, speech_spans, language, hotwords
            )
            relevant_speech = tuple(span for span in speech_spans if span.overlaps(window.span))
            should_run_pyannote = bool(relevant_speech or (moss and moss.segments))
            report_activity("speaker_analysis", fallback=moss is None or health.needs_fallback)
            pyannote, pyannote_attempts, pyannote_errors = (
                await self._run_pyannote(job_id, audio_path, window)
                if should_run_pyannote
                else (None, 0, ())
            )
            errors = (*moss_errors, *pyannote_errors)
            if moss is not None and not health.needs_fallback and moss.segments:
                path = WindowStructurePath.MOSS
                base_segments = moss.segments
            elif pyannote is not None:
                path = WindowStructurePath.PYANNOTE_FALLBACK
                base_segments = apply_pyannote_fallback(
                    moss.segments if moss is not None else (), pyannote
                )
            elif moss is not None and moss.segments:
                path = WindowStructurePath.MOSS_COARSE
                base_segments = tuple(
                    item.with_provenance(
                        fallback_unavailable=True,
                        retained_anomalous_coarse_structure=True,
                    )
                    for item in moss.segments
                )
            elif relevant_speech:
                path = WindowStructurePath.COARSE_VAD
                base_segments = _coarse_vad_segments(window, relevant_speech, errors)
            else:
                path = WindowStructurePath.SILENCE
                base_segments = ()

            pyannote_to_structure = (
                _identity_diarization_mapping(pyannote)
                if path is WindowStructurePath.PYANNOTE_FALLBACK and pyannote is not None
                else _align_diarization_to_structure(pyannote, base_segments)
                if pyannote is not None
                else {}
            )
            observations = (
                _remap_embeddings(pyannote.embeddings, pyannote_to_structure)
                if pyannote is not None
                else ()
            )
            if pyannote is not None:
                base_segments = _mark_true_overlaps(base_segments, pyannote)
            constraints = infer_overlap_constraints(previous_stitched, base_segments)
            stitched = self.speaker_tracker.stitch_window(
                window.ordinal,
                base_segments,
                observations,
                constraints,
                tuple(sorted(set(pyannote_to_structure.values()))),
                timeline_evidence={
                    mapped: tuple(
                        item.span
                        for item in (*pyannote.regular_spans, *pyannote.exclusive_spans)
                        if pyannote_to_structure.get(item.speaker_local) == mapped
                    )
                    for mapped in set(pyannote_to_structure.values())
                }
                if pyannote is not None
                else {},
            )
            speaker_spans = (
                speaker_timeline_from_diarization(pyannote, pyannote_to_structure, stitched.mapping)
                if pyannote is not None
                else speaker_timeline_from_segments(stitched.segments)
            )
            outcome = WindowStructureOutcome(
                window=window,
                path=path,
                segments=stitched.segments,
                speaker_spans=speaker_spans,
                health=health,
                moss_attempts=moss_attempts,
                pyannote_attempts=pyannote_attempts,
                errors=errors,
                speaker_mapping=stitched.mapping,
                speaker_diagnostics=stitched.diagnostics,
                overlap_links=stitched.overlap_links,
                speaker_id_switches=stitched.speaker_id_switches,
            )
            outcomes.append(outcome)
            report_activity(
                "structure_window_completed", windows_completed=len(outcomes), path=path.value
            )
            previous_stitched = stitched.segments

        deduplicated, dedup_diagnostics = deduplicate_structure_segments(
            tuple(segment for outcome in outcomes for segment in outcome.segments)
        )
        final_by_window = {
            window.ordinal: tuple(
                segment for segment in deduplicated if segment.window_ordinal == window.ordinal
            )
            for window in windows
        }
        final_outcomes = tuple(
            replace(outcome, segments=final_by_window[outcome.window.ordinal])
            for outcome in outcomes
        )
        if self.repository is not None:
            for outcome in final_outcomes:
                self.repository.replace_window(
                    job_id,
                    outcome.window.ordinal,
                    outcome.segments,
                    outcome.speaker_spans,
                )
        transcript_chunks = _build_natural_transcript_chunks(
            speech_spans, deduplicated, boundary_cues
        )
        return StructurePipelineResult(
            deduplicated,
            final_outcomes,
            dedup_diagnostics,
            transcript_chunks,
        )

    async def _run_moss(
        self,
        job_id: str,
        audio_path: Path,
        window: StructureWindow,
        speech_spans: tuple[AudioSpan, ...],
        language: str,
        hotwords: tuple[str, ...],
    ) -> tuple[StructureResult | None, StructureHealthReport, int, tuple[str, ...]]:
        errors: list[str] = []
        latest_result: StructureResult | None = None
        latest_health = StructureHealthReport.worker_failure("MOSS was not attempted")
        for attempt in range(1, self.max_window_attempts + 1):
            report_activity(
                "structure_attempt",
                fallback=False,
                model_attempt=attempt,
                retry=attempt > 1,
                reason="; ".join(errors[-1:]),
            )
            request = build_moss_structure_request(
                request_id=f"{job_id}:structure:{window.ordinal}:moss:{attempt}",
                job_id=job_id,
                audio_path=audio_path,
                window=window,
                config=self.config,
                language=language,
                hotwords=hotwords,
            )
            try:
                latest_result = parse_moss_structure_response(await self.moss_call(request), window)
                latest_health = inspect_structure_result(latest_result, speech_spans, self.config)
                if not latest_health.needs_fallback:
                    return latest_result, latest_health, attempt, tuple(errors)
                errors.append(
                    "MOSS anomaly: " + ",".join(item.value for item in latest_health.anomalies)
                )
            except Exception as exc:
                errors.append(f"MOSS attempt {attempt}: {type(exc).__name__}: {exc}")
                latest_health = StructureHealthReport.worker_failure(str(exc))
        return latest_result, latest_health, self.max_window_attempts, tuple(errors)

    async def _run_pyannote(
        self, job_id: str, audio_path: Path, window: StructureWindow
    ) -> tuple[PyannoteResult | None, int, tuple[str, ...]]:
        errors: list[str] = []
        for attempt in range(1, self.max_window_attempts + 1):
            report_activity(
                "speaker_analysis",
                model_attempt=attempt,
                retry=attempt > 1,
                reason="; ".join(errors[-1:]),
            )
            request = build_pyannote_request(
                request_id=f"{job_id}:structure:{window.ordinal}:pyannote:{attempt}",
                job_id=job_id,
                audio_path=audio_path,
                window=window,
                config=self.config,
            )
            try:
                return (
                    parse_pyannote_response(await self.pyannote_call(request), window),
                    attempt,
                    tuple(errors),
                )
            except Exception as exc:
                errors.append(f"pyannote attempt {attempt}: {type(exc).__name__}: {exc}")
        return None, self.max_window_attempts, tuple(errors)


def _coarse_vad_segments(
    window: StructureWindow,
    speech_spans: tuple[AudioSpan, ...],
    errors: tuple[str, ...],
) -> tuple[StructureSegment, ...]:
    segments: list[StructureSegment] = []
    for index, span in enumerate(speech_spans):
        start = max(span.start_sample, window.span.start_sample)
        end = min(span.end_sample, window.span.end_sample)
        if end <= start:
            continue
        segments.append(
            StructureSegment(
                window_ordinal=window.ordinal,
                span=AudioSpan(start, end),
                speaker_local=f"UNCERTAIN_{window.ordinal:02d}_{index:03d}",
                text="",
                acoustic_events=(),
                source_model="coarse_vad",
                source_revision="0" * 40,
                selection_score=0.2,
                fallback=True,
                provenance={
                    "source_model": "coarse_vad",
                    "source_revision": "0" * 40,
                    "window_ordinal": window.ordinal,
                    "source_segment_index": index,
                    "absolute_samples": True,
                    "text_role": STRUCTURE_TEXT_ROLE,
                    "adopted_as_final": False,
                    "worker_errors": list(errors),
                    "retryable": True,
                },
            )
        )
    return tuple(segments)


def _identity_diarization_mapping(result: PyannoteResult) -> dict[str, str]:
    return {
        item.speaker_local: item.speaker_local
        for item in (*result.regular_spans, *result.exclusive_spans)
    }


def _align_diarization_to_structure(
    result: PyannoteResult | None,
    segments: tuple[StructureSegment, ...],
) -> dict[str, str]:
    if result is None:
        return {}
    scores: list[tuple[int, str, str]] = []
    for diarized in result.exclusive_spans:
        for structured in segments:
            if structured.speaker_local is None:
                continue
            overlap = _intersection_samples(diarized.span, structured.span)
            if overlap:
                scores.append((overlap, diarized.speaker_local, structured.speaker_local))
    mapping: dict[str, str] = {}
    used_structure: set[str] = set()
    for _score, pyannote_label, structure_label in sorted(scores, reverse=True):
        if pyannote_label in mapping or structure_label in used_structure:
            continue
        mapping[pyannote_label] = structure_label
        used_structure.add(structure_label)
    pyannote_labels = {
        item.speaker_local for item in (*result.regular_spans, *result.exclusive_spans)
    } | {item.speaker_local for item in result.embeddings}
    for label in sorted(pyannote_labels - mapping.keys()):
        mapping[label] = f"PYANNOTE::{label}"
    return mapping


def _remap_embeddings(
    embeddings: tuple[EmbeddingObservation, ...], mapping: dict[str, str]
) -> tuple[EmbeddingObservation, ...]:
    return tuple(
        replace(item, speaker_local=mapping[item.speaker_local])
        for item in embeddings
        if item.speaker_local in mapping
    )


def _mark_true_overlaps(
    segments: tuple[StructureSegment, ...], result: PyannoteResult
) -> tuple[StructureSegment, ...]:
    marked: list[StructureSegment] = []
    for segment in segments:
        speakers = sorted(
            {
                speaker
                for overlap in result.true_overlaps
                if _intersection_samples(segment.span, overlap.span)
                >= min(segment.span.duration_samples, SAMPLE_RATE // 4)
                for speaker in overlap.speaker_local_ids
            }
        )
        marked.append(
            replace(
                segment,
                speaker_local=None if speakers else segment.speaker_local,
                overlap=bool(speakers),
                exclusive=not speakers,
                provenance={
                    **segment.provenance,
                    "true_overlap_speakers": speakers,
                    "overlap_detection_source": result.source_model if speakers else None,
                },
            )
        )
    return tuple(marked)


def _intersection_samples(left: AudioSpan, right: AudioSpan) -> int:
    return max(
        0, min(left.end_sample, right.end_sample) - max(left.start_sample, right.start_sample)
    )


def _build_natural_transcript_chunks(
    speech_spans: tuple[AudioSpan, ...],
    segments: tuple[StructureSegment, ...],
    boundary_cues: tuple[BoundaryCue, ...],
) -> tuple[TranscriptChunk, ...]:
    """Refine phase-4 body chunks with structural speaker and coarse-sentence cues."""

    if not speech_spans:
        return ()
    ordered_speech = tuple(sorted(speech_spans, key=lambda item: item.start_sample))
    speech_extent = AudioSpan(
        ordered_speech[0].start_sample,
        max(item.end_sample for item in ordered_speech),
    )
    derived = list(boundary_cues)
    for left_speech, right_speech in pairwise(ordered_speech):
        gap = right_speech.start_sample - left_speech.end_sample
        if gap >= 300 * SAMPLE_RATE // 1000:
            derived.append(
                BoundaryCue(
                    left_speech.end_sample + gap // 2,
                    BoundaryKind.NATURAL_PAUSE
                    if gap <= 800 * SAMPLE_RATE // 1000
                    else BoundaryKind.LONG_PAUSE,
                )
            )
    ordered_segments = tuple(
        sorted(segments, key=lambda item: (item.span.start_sample, item.span.end_sample))
    )
    for left_segment, right_segment in pairwise(ordered_segments):
        left_speaker = left_segment.speaker_global
        right_speaker = right_segment.speaker_global
        if left_speaker != right_speaker and (
            left_speaker is not None or right_speaker is not None
        ):
            derived.append(
                BoundaryCue(right_segment.span.start_sample, BoundaryKind.SPEAKER_CHANGE)
            )
    for segment in ordered_segments:
        if re.search("[.!?\u3002\uff01\uff1f]\\s*$", segment.text):
            derived.append(BoundaryCue(segment.span.end_sample, BoundaryKind.SENTENCE_END, 0.8))
    unique = {
        (cue.sample, cue.kind): cue
        for cue in derived
        if speech_extent.start_sample < cue.sample < speech_extent.end_sample
    }
    return make_transcript_chunks(speech_extent, tuple(unique.values()))
