"""Concrete, fail-closed production handlers for every classroom checkpoint."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import uuid4

from classscribe_protocol import Priority, RPCRequest, RPCResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from classscribe.alignment import (
    AlignedToken,
    AlignmentGateInput,
    TimingEvidence,
    TimingSource,
    build_alignment_request,
    evaluate_alignment_gate,
    parse_alignment_response,
    select_canonical_timing,
)
from classscribe.alignment.validation import validate_timing
from classscribe.asr.models import (
    ASRCandidateEvidence,
    ASRTokenEvidence,
    CandidateRole,
    PronunciationHint,
    build_asr_request,
    is_structure_model,
    parse_asr_response,
)
from classscribe.audio.lid import ROUTABLE_LANGUAGES, LanguageRouter, LIDObservation
from classscribe.audio.media import AudioMaster, FFmpegMediaPipeline, ImportedMedia, file_sha256
from classscribe.audio.qc import PCMQualityAnalyzer
from classscribe.audio.segmentation import TranscriptChunk, make_structure_windows
from classscribe.audio.vad import SpeechRegionResult
from classscribe.benchmark import CalibrationArtifact
from classscribe.config import AppConfig
from classscribe.consensus import (
    ConfusionNetwork,
    ConsensusCandidate,
    ConsensusInputs,
    ReliabilityProfile,
)
from classscribe.contracts import LanguageMode, ModelSelectionMode
from classscribe.db.models import (
    ASRCandidate,
    BenchmarkRun,
    BenchmarkStatus,
    DecisionEvent,
    ExportArtifact,
    GlossaryTerm,
    Job,
    JobCheckpoint,
    LanguageSpan,
    ProfileSetting,
    Recording,
    ReviewStatus,
    SpeakerSpan,
    SpeechRegion,
    StructureSegmentRecord,
    TimingQuality,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.exports import (
    ExportFormat,
    ExportLayer,
    ExportSegment,
    ExportToken,
    ExportView,
    render_export,
)
from classscribe.models import ModelEntry, ModelManager, ModelRegistry, SandboxedModelInvoker
from classscribe.paths import AppPaths
from classscribe.punctuation import (
    AcousticBoundary,
    build_punctuation_request,
    parse_punctuation_response,
    punctuate_chinese,
    punctuate_english,
    punctuate_japanese,
    strip_punctuation_and_spacing,
)
from classscribe.quality import (
    ComparisonContext,
    QualityContext,
    QualityFeatureExtractor,
    QualityIssue,
    ReviewRouter,
    compare_language_candidates,
    merge_retry_pieces,
)
from classscribe.quality.text import surface_tokens
from classscribe.recovery import atomic_write_text
from classscribe.structure.models import task_speaker_config
from classscribe.structure.pipeline import StructurePipeline
from classscribe.terminology import (
    ConfirmationStatus,
    ConfirmedSegment,
    CorrectionEvidence,
    CourseTerm,
    TermSource,
    TermUsage,
    TextLayers,
    apply_terminology,
    select_rolling_context,
)
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from classscribe.worker_sandbox import WorkerSandbox

_CONTENT_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


class ProductionStageRunner:
    """Execute the documented architecture using only installed pinned revisions."""

    def __init__(
        self,
        paths: AppPaths,
        config: AppConfig,
        registry: ModelRegistry,
        manager: ModelManager,
        invoke: SandboxedModelInvoker,
    ) -> None:
        self.paths = paths
        self.config = config
        self.registry = registry
        self.manager = manager
        self.invoke = invoke
        self.media = FFmpegMediaPipeline()
        self.quality = PCMQualityAnalyzer()
        self._handlers = {
            "upload_validate": self._upload_validate,
            "normalize_audio_master": self._normalize_audio_master,
            "audio_qc": self._audio_qc,
            "vad": self._vad,
            "lid": self._lid,
            "moss_structure": self._moss_structure,
            "primary_asr": self._primary_asr,
            "quality_and_review": self._quality_and_review,
            "terminology": self._terminology,
            "punctuation": self._punctuation,
            "forced_alignment": self._forced_alignment,
            "final_validation": self._final_validation,
            "automatic_exports": self._automatic_exports,
        }

    def preflight(self, parameters: Mapping[str, Any], session: Session | None = None) -> None:
        missing_tools = [
            name for name in ("ffmpeg", "ffprobe", "uv", "bwrap") if shutil.which(name) is None
        ]
        if missing_tools:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT,
                f"production runtime tools are missing: {', '.join(missing_tools)}",
            )
        WorkerSandbox.detect()
        language = str(parameters.get("language", ""))
        required = {"firered_vad", "moss_td_0_9b"}
        if language == LanguageMode.AUTO_MIXED.value:
            required.add("firered_lid")
            for code in ("zh", "ja", "en"):
                required.add(self._primary_entry(parameters, code, session).id)
        else:
            required.add(self._primary_entry(parameters, language, session).id)
        unavailable = [model_id for model_id in sorted(required) if not self._installed(model_id)]
        if unavailable:
            raise ClassScribeError(
                ErrorCode.MODEL_NOT_FULLY_INSTALLED,
                "install and health-check required model revisions first: "
                + ", ".join(unavailable),
            )

    def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        handler = self._handlers.get(checkpoint.checkpoint_key)
        if handler is None:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT,
                f"unknown production checkpoint: {checkpoint.checkpoint_key}",
            )
        handler(session, job, checkpoint)

    def _upload_validate(self, session: Session, job: Job, _checkpoint: JobCheckpoint) -> None:
        recording = session.get_one(Recording, job.recording_id)
        source = self.media.validate_source(Path(recording.source_path))
        if file_sha256(source) != recording.source_sha256:
            raise ClassScribeError(ErrorCode.MODEL_INTEGRITY_FAILED, "recording hash changed")
        metadata = self.media.probe(source)
        recording.audio_qc_json = {
            **recording.audio_qc_json,
            "source_probe": {
                "format": metadata.format_name,
                "codec": metadata.codec_name,
                "sample_rate": metadata.sample_rate,
                "channels": metadata.channels,
            },
        }

    def _normalize_audio_master(
        self, session: Session, job: Job, _checkpoint: JobCheckpoint
    ) -> None:
        recording = session.get_one(Recording, job.recording_id)
        master_path = self._master_path(recording.id)
        if master_path.is_file() and not master_path.is_symlink():
            master = self.media._inspect_master(master_path)
        else:
            master = self.media.normalize(Path(recording.source_path), master_path)
        recording.duration_samples = master.duration_samples
        recording.sample_rate = SAMPLE_RATE
        recording.channels = 1
        recording.audio_qc_json = {
            **recording.audio_qc_json,
            "master": {"path_role": "canonical_master", "sha256": file_sha256(master_path)},
        }

    def _audio_qc(self, session: Session, job: Job, _checkpoint: JobCheckpoint) -> None:
        recording = session.get_one(Recording, job.recording_id)
        source = Path(recording.source_path)
        metadata = self.media.probe(source)
        imported = ImportedMedia(
            recording.source_name,
            source,
            recording.source_sha256,
            source.stat().st_size,
            metadata,
        )
        master_path = self._master_path(recording.id)
        master = AudioMaster(
            master_path,
            file_sha256(master_path),
            self.media._inspect_master(master_path).duration_samples,
        )
        descriptor, name = tempfile.mkstemp(
            prefix=".channel-qc-", suffix=".wav", dir=master_path.parent
        )
        os.close(descriptor)
        qc_path = Path(name)
        try:
            self.media.make_multichannel_qc_wav(source, qc_path)
            report = self.quality.build_report(imported, master, qc_path)
        finally:
            qc_path.unlink(missing_ok=True)
        recording.audio_qc_json = report.as_dict()

    def _vad(self, session: Session, job: Job, _checkpoint: JobCheckpoint) -> None:
        recording = session.get_one(Recording, job.recording_id)
        entry = self.registry.model("firered_vad")
        request = RPCRequest(
            f"{job.id}:vad",
            job.id,
            600_000,
            Priority.BACKGROUND,
            "vad",
            {
                "audio_path": str(self._master_path(recording.id)),
                "start_sample": 0,
                "end_sample": recording.duration_samples,
                "sample_rate": SAMPLE_RATE,
            },
        )
        response = asyncio.run(self.invoke(entry, request))
        if not response.ok:
            raise RuntimeError(f"VAD failed: {response.error_code}: {response.error_detail}")
        regions = tuple(_speech_region(item) for item in response.segments)
        _validate_spans(tuple(item.span for item in regions), recording.duration_samples)
        session.execute(delete(SpeechRegion).where(SpeechRegion.job_id == job.id))
        session.add_all(
            SpeechRegion(
                job_id=job.id,
                start_sample=item.span.start_sample,
                end_sample=item.span.end_sample,
                vad_score=item.vad_score,
                acoustic_class=item.acoustic_class,
                source=item.source,
            )
            for item in regions
        )
        speech_samples = _union_samples(tuple(item.span for item in regions))
        recording.audio_qc_json = {
            **recording.audio_qc_json,
            "speech_ratio": speech_samples / max(recording.duration_samples, 1),
            "silence_ratio": 1 - speech_samples / max(recording.duration_samples, 1),
        }

    def _lid(self, session: Session, job: Job, _checkpoint: JobCheckpoint) -> None:
        recording = session.get_one(Recording, job.recording_id)
        total = recording.duration_samples
        if job.language_mode is LanguageMode.AUTO_MIXED:
            entry = self.registry.model("firered_lid")
            observations: list[LIDObservation] = []
            from classscribe.audio.lid import sliding_lid_windows

            for ordinal, span in enumerate(sliding_lid_windows(total)):
                response = asyncio.run(
                    self.invoke(
                        entry,
                        RPCRequest(
                            f"{job.id}:lid:{ordinal}",
                            job.id,
                            120_000,
                            Priority.BACKGROUND,
                            "lid",
                            {
                                "audio_path": str(self._master_path(recording.id)),
                                "start_sample": span.start_sample,
                                "end_sample": span.end_sample,
                                "sample_rate": SAMPLE_RATE,
                            },
                        ),
                    )
                )
                if not response.ok or response.language not in {"zh", "ja", "en"}:
                    raise RuntimeError("LID worker returned no supported language")
                supplied = response.result.get("language_probabilities")
                reported = (
                    supplied.get(response.language) if isinstance(supplied, Mapping) else None
                )
                segment_confidence = (
                    response.segments[0].get("confidence_raw") if response.segments else 0.0
                )
                probability = _bounded_probability(
                    reported if reported is not None else segment_confidence
                )
                probabilities = {
                    language: probability if language.value == response.language else 0.0
                    for language in ROUTABLE_LANGUAGES
                }
                observations.append(LIDObservation(span, probabilities))
            silence_points = _silence_points(
                tuple(
                    AudioSpan(item.start_sample, item.end_sample)
                    for item in session.scalars(
                        select(SpeechRegion)
                        .where(SpeechRegion.job_id == job.id)
                        .order_by(SpeechRegion.start_sample)
                    )
                )
            )
            spans = LanguageRouter().route(
                job.language_mode,
                total,
                observations=observations,
                silence_points=silence_points,
            )
        else:
            spans = LanguageRouter().route(job.language_mode, total)
        session.execute(delete(LanguageSpan).where(LanguageSpan.job_id == job.id))
        session.add_all(
            LanguageSpan(
                job_id=job.id,
                start_sample=item.span.start_sample,
                end_sample=item.span.end_sample,
                language=item.language,
                confidence_raw=item.confidence_raw,
                decision_json=dict(item.decision),
            )
            for item in spans
        )

    def _moss_structure(self, session: Session, job: Job, _checkpoint: JobCheckpoint) -> None:
        recording = session.get_one(Recording, job.recording_id)
        master = self._master_path(recording.id)
        speech = tuple(
            AudioSpan(item.start_sample, item.end_sample)
            for item in session.scalars(
                select(SpeechRegion)
                .where(SpeechRegion.job_id == job.id)
                .order_by(SpeechRegion.start_sample)
            )
        )
        moss = self.registry.model("moss_td_0_9b")
        pyannote = self.registry.model("pyannote_community_1")
        hotwords = tuple(
            item.canonical
            for item in session.scalars(
                select(GlossaryTerm).where(
                    GlossaryTerm.glossary_id == job.options_json.get("glossary_id")
                )
            )
            if item.user_confirmed
        )

        async def moss_call(request: RPCRequest) -> RPCResponse:
            return await self.invoke(moss, request)

        async def pyannote_call(request: RPCRequest) -> RPCResponse:
            return await self.invoke(pyannote, request)

        result = asyncio.run(
            StructurePipeline(
                task_speaker_config(self.config.classroom, job.options_json.get("speaker_count")),
                moss_call,
                pyannote_call,
            ).process(
                job_id=job.id,
                audio_path=master,
                windows=make_structure_windows(
                    recording.duration_samples,
                    window_samples=self.config.classroom.structure_window_seconds * SAMPLE_RATE,
                    overlap_samples=self.config.classroom.structure_overlap_seconds * SAMPLE_RATE,
                ),
                speech_spans=speech,
                language=(
                    "auto"
                    if job.language_mode is LanguageMode.AUTO_MIXED
                    else job.language_mode.value
                ),
                hotwords=hotwords,
            )
        )
        session.execute(
            delete(StructureSegmentRecord).where(StructureSegmentRecord.job_id == job.id)
        )
        session.execute(delete(SpeakerSpan).where(SpeakerSpan.job_id == job.id))
        session.add_all(
            StructureSegmentRecord(
                job_id=job.id,
                window_ordinal=item.window_ordinal,
                start_sample=item.span.start_sample,
                end_sample=item.span.end_sample,
                speaker_global_id=item.speaker_global,
                speaker_local_id=item.speaker_local,
                coarse_text=item.text,
                acoustic_events_json=list(item.acoustic_events),
                overlap=item.overlap,
                exclusive=item.exclusive,
                fallback=item.fallback,
                source_model=item.source_model,
                source_revision=item.source_revision,
                confidence_raw=item.confidence_raw,
                selection_score=item.selection_score,
                text_role=item.text_role,
                adopted_as_final=False,
                provenance_json=dict(item.provenance),
            )
            for item in result.segments
        )
        session.add_all(
            SpeakerSpan(
                job_id=job.id,
                window_ordinal=item.window.ordinal,
                start_sample=span.span.start_sample,
                end_sample=span.span.end_sample,
                speaker_global_id=span.speaker_global,
                speaker_local_id=span.speaker_local,
                overlap=span.overlap,
                source_model=span.source_model,
                source_revision=span.source_revision,
                confidence=span.confidence,
                provenance_json=dict(span.provenance),
            )
            for item in result.windows
            for span in item.speaker_spans
        )
        languages = tuple(
            session.scalars(
                select(LanguageSpan)
                .where(LanguageSpan.job_id == job.id)
                .order_by(LanguageSpan.start_sample)
            )
        )
        for chunk in result.transcript_chunks:
            language = _dominant_language(chunk.core_span, languages)
            speaker = _dominant_speaker(chunk.core_span, result.segments)
            existing = session.scalar(
                select(TranscriptSegment).where(
                    TranscriptSegment.job_id == job.id,
                    TranscriptSegment.start_sample == chunk.core_span.start_sample,
                    TranscriptSegment.end_sample == chunk.core_span.end_sample,
                    TranscriptSegment.is_active.is_(True),
                )
            )
            if existing is None:
                session.add(
                    TranscriptSegment(
                        job_id=job.id,
                        start_sample=chunk.core_span.start_sample,
                        end_sample=chunk.core_span.end_sample,
                        speaker_id=speaker,
                        language=language,
                        raw_text="",
                        faithful_text="",
                        smart_corrected_text="",
                        timing_quality=TimingQuality.STRUCTURE,
                    )
                )

    def _primary_asr(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        segment = self._segment(session, checkpoint)
        entry = self._primary_entry(job.options_json, segment.language.value, session)
        evidence = self._transcribe(session, job, segment, entry, CandidateRole.PRIMARY)
        self._store_candidate(session, segment, evidence)
        segment.raw_text = evidence.raw_text

    def _quality_and_review(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        segment = self._segment(session, checkpoint)
        candidates = list(
            session.scalars(
                select(ASRCandidate)
                .where(ASRCandidate.segment_id == segment.id, ASRCandidate.deleted_at.is_(None))
                .order_by(ASRCandidate.created_at, ASRCandidate.id)
            )
        )
        if not candidates:
            raise RuntimeError("quality stage has no primary ASR candidate")
        voiced = self._voiced_spans(session, job.id, self._span(segment))
        context = self._quality_context(session, job, segment, voiced)
        extractor = QualityFeatureExtractor()
        router = ReviewRouter(
            second_model_threshold=self.config.quality.second_model_threshold,
            third_model_threshold=self.config.quality.third_model_threshold,
        )
        reviewed: list[ConsensusCandidate] = []
        primary = candidates[-1]
        primary_evidence = self._evidence(session, segment, primary)
        primary_report = extractor.inspect(primary.id, primary_evidence, context)
        reviewed.append(ConsensusCandidate(primary.id, primary_evidence, primary_report))
        secondary_decision = router.secondary(primary_report)
        tertiary_decision = None
        strict = (
            job.options_json.get("accuracy_mode") == "strict_single"
            or job.options_json.get("model_selection")
            == ModelSelectionMode.STRICT_SINGLE_MODEL.value
        )
        fallbacks = (
            ()
            if strict
            else self._fallback_entries(
                job.options_json, segment.language.value, primary.model_id, session
            )
        )
        if secondary_decision.run_secondary and fallbacks:
            if secondary_decision.retry is not None:
                pieces = tuple(
                    self._transcribe(
                        session,
                        job,
                        segment,
                        fallbacks[0],
                        CandidateRole.SECONDARY,
                        span=span,
                    )
                    for span in secondary_decision.retry.retry_spans
                )
                secondary_evidence = merge_retry_pieces(
                    secondary_decision.retry,
                    pieces,
                    original_core=self._span(segment),
                )
            else:
                secondary_evidence = self._transcribe(
                    session, job, segment, fallbacks[0], CandidateRole.SECONDARY
                )
            secondary = self._store_candidate(session, segment, secondary_evidence)
            secondary_report = extractor.inspect(secondary.id, secondary_evidence, context)
            reviewed.append(ConsensusCandidate(secondary.id, secondary_evidence, secondary_report))
            comparison = compare_language_candidates(
                primary.normalized_text,
                secondary.normalized_text,
                segment.language.value,
                self._comparison_context(session, job),
            )
            tertiary_decision = router.tertiary(primary_report, secondary_report, comparison)
            if tertiary_decision.run_tertiary and len(fallbacks) > 1:
                tertiary_evidence = self._transcribe(
                    session, job, segment, fallbacks[1], CandidateRole.TERTIARY
                )
                tertiary = self._store_candidate(session, segment, tertiary_evidence)
                tertiary_report = extractor.inspect(tertiary.id, tertiary_evidence, context)
                reviewed.append(ConsensusCandidate(tertiary.id, tertiary_evidence, tertiary_report))
        for item in reviewed:
            row = session.get_one(ASRCandidate, item.candidate_id)
            row.quality_features_json = {
                **row.quality_features_json,
                **item.quality.as_dict(),
            }
            row.is_valid = item.quality.valid_for_consensus
            session.add(
                DecisionEvent(
                    segment_id=segment.id,
                    event_type="candidate_quality_gated",
                    actor_type="automatic",
                    actor_id=None,
                    input_json={"candidate_id": row.id, "model_id": row.model_id},
                    output_json=item.quality.as_dict(),
                    rule_version=item.quality.rule_version,
                )
            )
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="automatic_model_review_routed",
                actor_type="automatic",
                actor_id=None,
                input_json={"strict_single": strict},
                output_json={
                    "run_secondary": secondary_decision.run_secondary and bool(fallbacks),
                    "secondary_triggers": [item.value for item in secondary_decision.triggers],
                    "run_tertiary": bool(tertiary_decision and tertiary_decision.run_tertiary),
                    "tertiary_triggers": (
                        [item.value for item in tertiary_decision.triggers]
                        if tertiary_decision
                        else []
                    ),
                },
                rule_version="production-review-routing-v1",
            )
        )
        reliability, calibrated_tokens = self._consensus_calibration(
            session, segment.language.value, tuple(reviewed)
        )
        result = ConfusionNetwork().resolve(
            tuple(reviewed),
            canonical_span=self._span(segment),
            language=segment.language.value,
            inputs=ConsensusInputs(
                reliability,
                calibrated_tokens,
                voiced,
                glossary_terms=frozenset(self._confirmed_term_names(session, job)),
            ),
        )
        session.execute(
            delete(TokenSpan).where(
                TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None)
            )
        )
        session.add_all(
            TokenSpan(
                candidate_id=None,
                segment_id=segment.id,
                start_sample=token.span.start_sample,
                end_sample=token.span.end_sample,
                token=token.text,
                normalized_token=token.text.casefold(),
                confidence=token.support_score,
                provenance_json={**token.provenance, "is_final_consensus_token": True},
            )
            for token in result.tokens
        )
        adopted_ids = {
            str(source["candidate_id"])
            for token in result.tokens
            for source in token.provenance.get("candidate_sources", [])
            if isinstance(source, dict) and "candidate_id" in source
        }
        for candidate_row in session.scalars(
            select(ASRCandidate).where(
                ASRCandidate.segment_id == segment.id,
                ASRCandidate.deleted_at.is_(None),
            )
        ):
            candidate_row.is_adopted = candidate_row.id in adopted_ids
        segment.faithful_text = result.text
        segment.smart_corrected_text = result.text
        segment.auto_final_source = result.strategy
        segment.quality_score = result.consensus_support_score
        segment.timing_quality = TimingQuality.ALIGNED
        segment.review_status = (
            ReviewStatus.NEEDS_REVIEW if result.low_confidence else ReviewStatus.AUTO
        )
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="automatic_consensus_adopted",
                actor_type="automatic",
                actor_id=None,
                input_json={"candidate_ids": [item.candidate_id for item in reviewed]},
                output_json={
                    "faithful_text": result.text,
                    "strategy": result.strategy,
                    "support": result.consensus_support_score,
                    "low_confidence": result.low_confidence,
                    "token_provenance_complete": True,
                },
                rule_version=result.rule_version,
            )
        )

    def _terminology(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        segment = self._segment(session, checkpoint)
        terms = self._course_terms(session, job, segment.language.value)
        emitted = {
            term.canonical: CorrectionEvidence(
                frozenset(
                    form
                    for form in (term.canonical, *term.aliases)
                    if form and form.casefold() in segment.faithful_text.casefold()
                )
            )
            for term in terms
        }
        result = apply_terminology(
            TextLayers(
                segment.raw_text,
                segment.faithful_text,
                segment.smart_corrected_text,
                segment.user_text,
            ),
            terms,
            emitted,
            language=segment.language.value,
        )
        before = segment.smart_corrected_text
        segment.smart_corrected_text = result.layers.smart_corrected_text
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="deterministic_terminology_corrected",
                actor_type="automatic",
                actor_id=None,
                input_json={"faithful_text": segment.faithful_text, "smart_before": before},
                output_json={
                    "smart_corrected_text": segment.smart_corrected_text,
                    "faithful_text_unchanged": True,
                    "diffs": [
                        {
                            "start": item.start,
                            "end": item.end,
                            "before": item.before,
                            "after": item.after,
                            "rule_id": item.rule_id,
                            "source": item.source,
                        }
                        for item in result.diffs
                    ],
                },
                rule_version=result.rule_version,
            )
        )

    def _punctuation(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        segment = self._segment(session, checkpoint)
        language = segment.language.value
        faithful_source = segment.faithful_text
        smart_source = segment.smart_corrected_text
        proposal_faithful = self._punctuation_proposal(job, faithful_source, language)
        proposal_smart = self._punctuation_proposal(job, smart_source, language)
        acoustic = self._acoustic_boundaries(session, segment, faithful_source)
        if language == "zh":
            faithful = punctuate_chinese(
                faithful_source,
                firered_proposal=proposal_faithful,
                acoustic_boundaries=acoustic,
            )
            smart = punctuate_chinese(
                smart_source,
                firered_proposal=proposal_smart,
                acoustic_boundaries=self._acoustic_boundaries(session, segment, smart_source),
            )
        elif language == "ja":
            faithful = punctuate_japanese(
                faithful_source,
                granite_proposal=faithful_source,
                acoustic_boundaries=acoustic,
            )
            smart = punctuate_japanese(
                smart_source,
                granite_proposal=smart_source,
                acoustic_boundaries=self._acoustic_boundaries(session, segment, smart_source),
            )
        else:
            faithful = punctuate_english(
                faithful_source,
                native_text=faithful_source,
                firered_proposal=proposal_faithful,
            )
            smart = punctuate_english(
                smart_source,
                native_text=smart_source,
                firered_proposal=proposal_smart,
            )
        segment.faithful_text = faithful.text
        segment.smart_corrected_text = smart.text
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="strict_punctuation_applied",
                actor_type="automatic",
                actor_id=None,
                input_json={
                    "faithful_text": faithful_source,
                    "smart_corrected_text": smart_source,
                },
                output_json={
                    "faithful_text": faithful.text,
                    "smart_corrected_text": smart.text,
                    "faithful_source": faithful.source,
                    "smart_source": smart.source,
                    "character_sequence_preserved": True,
                    "user_text_unchanged": True,
                },
                rule_version=faithful.rule_version,
            )
        )

    def _native_timing(
        self,
        session: Session,
        segment: TranscriptSegment,
        text: str,
        voiced: tuple[AudioSpan, ...],
    ) -> TimingEvidence | None:
        for candidate in session.scalars(
            select(ASRCandidate)
            .where(
                ASRCandidate.segment_id == segment.id,
                ASRCandidate.is_valid.is_(True),
                ASRCandidate.deleted_at.is_(None),
            )
            .order_by(ASRCandidate.is_adopted.desc(), ASRCandidate.created_at.desc())
        ):
            rows = tuple(
                session.scalars(
                    select(TokenSpan)
                    .where(
                        TokenSpan.candidate_id == candidate.id,
                    )
                    .order_by(TokenSpan.start_sample, TokenSpan.id)
                )
            )
            if not rows or any(not row.provenance_json.get("native_model_time") for row in rows):
                continue
            if surface_tokens(candidate.normalized_text, segment.language.value) != surface_tokens(
                text, segment.language.value
            ):
                continue
            if any(row.end_sample <= row.start_sample for row in rows):
                continue
            evidence = TimingEvidence(
                TimingSource.NATIVE_WORD,
                self._span(segment),
                text,
                tuple(
                    AlignedToken(row.token, AudioSpan(row.start_sample, row.end_sample))
                    for row in rows
                ),
                reliable=True,
                text_unchanged=True,
            )
            if validate_timing(evidence, voiced).valid:
                return evidence
        return None

    def _structure_timing(
        self,
        session: Session,
        segment: TranscriptSegment,
        text: str,
    ) -> TimingEvidence | None:
        rows = tuple(
            session.scalars(
                select(StructureSegmentRecord)
                .where(
                    StructureSegmentRecord.job_id == segment.job_id,
                    StructureSegmentRecord.start_sample >= segment.start_sample,
                    StructureSegmentRecord.end_sample <= segment.end_sample,
                    StructureSegmentRecord.source_model == "moss_td_0_9b",
                    StructureSegmentRecord.fallback.is_(False),
                    StructureSegmentRecord.overlap.is_(False),
                    StructureSegmentRecord.selection_score >= 0.7,
                )
                .order_by(StructureSegmentRecord.start_sample, StructureSegmentRecord.id)
            )
        )
        if not rows or any(not row.coarse_text for row in rows):
            return None
        if surface_tokens(
            " ".join(row.coarse_text for row in rows), segment.language.value
        ) != surface_tokens(text, segment.language.value):
            return None
        return TimingEvidence(
            TimingSource.MOSS_STRUCTURE,
            self._span(segment),
            text,
            tuple(
                AlignedToken(row.coarse_text, AudioSpan(row.start_sample, row.end_sample))
                for row in rows
            ),
            reliable=True,
            text_unchanged=True,
        )

    def _forced_alignment(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
        segment = self._segment(session, checkpoint)
        canonical = self._span(segment)
        final_text = segment.user_text or segment.smart_corrected_text or segment.faithful_text
        voiced = self._voiced_spans(session, job.id, canonical)
        issues = self._quality_issues(session, segment.id)
        aligner = self.registry.model("qwen3_forced_aligner_0_6b")
        gate = evaluate_alignment_gate(
            AlignmentGateInput(
                canonical,
                final_text,
                segment.language.value,
                segment.language.value,
                _union_samples(voiced),
                1.0 if final_text.strip() else 0.0,
                issues,
            ),
            registry_safe_seconds=aligner.safe_operating_window_seconds,
        )

        def run_forced_aligner() -> TimingEvidence:
            request = build_alignment_request(
                request_id=f"{job.id}:align:{segment.id}",
                job_id=job.id,
                audio_path=self._master_path(job.recording_id),
                entry=aligner,
                canonical_span=canonical,
                final_text=final_text,
                language=segment.language.value,
                gate=gate,
            )
            return parse_alignment_response(
                asyncio.run(self.invoke(aligner, request)), request, aligner, canonical
            )

        coarse = _coarse_timing(final_text, canonical, segment.language.value)
        selected = select_canonical_timing(
            final_text=final_text,
            canonical_span=canonical,
            voiced_spans=voiced,
            native=self._native_timing(session, segment, final_text, voiced),
            moss_structure=self._structure_timing(session, segment, final_text),
            alignment_gate=gate,
            run_forced_aligner=run_forced_aligner if self._installed(aligner.id) else None,
            vad_coarse=coarse,
        )
        old_tokens = tuple(
            session.scalars(
                select(TokenSpan)
                .where(TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None))
                .order_by(TokenSpan.start_sample, TokenSpan.id)
            )
        )
        sources = {
            (
                str(source.get("candidate_id")),
                str(source.get("source_token_index", "")),
                str(source.get("source_start_sample", "")),
                str(source.get("source_end_sample", "")),
            ): dict(source)
            for token in old_tokens
            for source in token.provenance_json.get("candidate_sources", [])
            if isinstance(source, dict) and source.get("candidate_id")
        }
        reliability_sources = {
            str(token.provenance_json["reliability_source"])
            for token in old_tokens
            if token.provenance_json.get("reliability_source")
        }
        locally_calibrated = any(
            token.provenance_json.get("reliability_locally_calibrated") is True
            for token in old_tokens
        )
        token_confidence_calibrated = any(
            token.provenance_json.get("token_confidence_calibrated") is True for token in old_tokens
        )
        session.execute(
            delete(TokenSpan).where(
                TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None)
            )
        )
        session.add_all(
            TokenSpan(
                candidate_id=None,
                segment_id=segment.id,
                start_sample=token.span.start_sample,
                end_sample=token.span.end_sample,
                token=token.text,
                normalized_token=token.text.casefold(),
                confidence=None,
                provenance_json={
                    "source_type": "final_text_alignment",
                    "candidate_sources": list(sources.values()),
                    "reliability_source": (
                        sorted(reliability_sources)[0]
                        if len(reliability_sources) == 1
                        else (
                            "mixed:" + ",".join(sorted(reliability_sources))
                            if reliability_sources
                            else "unavailable"
                        )
                    ),
                    "reliability_locally_calibrated": locally_calibrated,
                    "token_confidence_calibrated": token_confidence_calibrated,
                    "timing_source": selected.source.value,
                    "coarse_timing": selected.coarse_timing,
                    "absolute_samples": True,
                },
            )
            for token in selected.tokens
        )
        segment.timing_quality = (
            TimingQuality.STRUCTURE if selected.coarse_timing else TimingQuality.ALIGNED
        )
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type=(
                    "alignment_fallback_recorded"
                    if selected.coarse_timing
                    else "canonical_timing_adopted"
                ),
                actor_type="automatic",
                actor_id=None,
                input_json={"final_text": final_text, "gate": gate.quality_gate},
                output_json={
                    "source": selected.source.value,
                    "token_count": len(selected.tokens),
                    "speech_coverage": selected.speech_coverage,
                    "coarse_timing": selected.coarse_timing,
                    "fallback_reasons": list(selected.fallback_reasons),
                    "text_unchanged": True,
                },
                rule_version=selected.rule_version,
            )
        )

    def _final_validation(self, session: Session, _job: Job, checkpoint: JobCheckpoint) -> None:
        segment = self._segment(session, checkpoint)
        tokens = tuple(
            session.scalars(
                select(TokenSpan)
                .where(TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None))
                .order_by(TokenSpan.start_sample, TokenSpan.id)
            )
        )
        if not tokens:
            raise ClassScribeError(ErrorCode.CANDIDATE_TIMELINE_INVALID, "final text has no timing")
        previous = segment.start_sample
        for token in tokens:
            if (
                token.start_sample < previous
                or token.end_sample <= token.start_sample
                or token.end_sample > segment.end_sample
            ):
                raise ClassScribeError(
                    ErrorCode.CANDIDATE_TIMELINE_INVALID, "final token timeline is invalid"
                )
            if not token.provenance_json.get("absolute_samples"):
                raise ClassScribeError(
                    ErrorCode.CANDIDATE_TIMELINE_INVALID, "final token lacks provenance"
                )
            previous = token.end_sample
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="final_segment_validated",
                actor_type="automatic",
                actor_id=None,
                input_json={"token_count": len(tokens)},
                output_json={"timeline_valid": True, "provenance_present": True},
                rule_version="production-final-validation-v1",
            )
        )

    def _automatic_exports(self, session: Session, job: Job, _checkpoint: JobCheckpoint) -> None:
        segments = self._export_segments(session, job.id)
        raw_outputs = job.options_json.get("outputs", self.config.classroom.auto_export)
        outputs = tuple(
            ExportFormat("md" if str(item) == "markdown" else str(item)) for item in raw_outputs
        )
        if not job.options_json.get("include_speakers", True):
            segments = tuple(replace(segment, speaker=None) for segment in segments)
        layers = tuple(
            layer
            for layer, enabled in (
                (ExportLayer.FAITHFUL, job.options_json.get("include_faithful", True)),
                (ExportLayer.SMART, job.options_json.get("include_smart", True)),
            )
            if enabled
        )
        for layer in layers:
            for output_format in outputs:
                content = render_export(
                    segments,
                    output_format=output_format,
                    layer=layer,
                    view=ExportView.SENTENCES,
                )
                artifact_id = str(uuid4())
                suffix = "md" if output_format is ExportFormat.MARKDOWN else output_format.value
                file_name = f"classscribe-{job.id}-{layer.value}.{suffix}"
                relative = Path("jobs", job.id, "exports", f"{artifact_id}.{suffix}")
                target = self.paths.data_path(*relative.parts)
                atomic_write_text(target, content)
                session.add(
                    ExportArtifact(
                        id=artifact_id,
                        job_id=job.id,
                        output_format=output_format.value,
                        text_layer=layer.value,
                        view=ExportView.SENTENCES.value,
                        file_name=file_name,
                        relative_path=relative.as_posix(),
                        content_type=_CONTENT_TYPES[output_format.value],
                        sha256=hashlib.sha256(content.encode()).hexdigest(),
                        size_bytes=len(content.encode()),
                    )
                )

    def _transcribe(
        self,
        session: Session,
        job: Job,
        segment: TranscriptSegment,
        entry: ModelEntry,
        role: CandidateRole,
        *,
        span: AudioSpan | None = None,
    ) -> ASRCandidateEvidence:
        requested = span or self._span(segment)
        chunk = TranscriptChunk(
            0,
            requested,
            requested,
            "quality_retry_piece" if span is not None else "persisted_natural_segment",
            False,
            0,
            0,
        )
        hints, rolling_context = self._asr_context(session, job, segment)
        request = build_asr_request(
            request_id=f"{job.id}:body:{segment.id}:{entry.id}:{role.value}",
            job_id=job.id,
            audio_path=self._master_path(job.recording_id),
            chunk=chunk,
            entry=entry,
            language=segment.language.value,
            role=role,
            hints=hints,
            rolling_context=rolling_context,
        )
        return parse_asr_response(
            asyncio.run(self.invoke(entry, request)), request, entry, chunk, role
        )

    def _store_candidate(
        self, session: Session, segment: TranscriptSegment, evidence: ASRCandidateEvidence
    ) -> ASRCandidate:
        previous = session.scalars(
            select(ASRCandidate)
            .where(
                ASRCandidate.segment_id == segment.id,
                ASRCandidate.model_id == evidence.model_id,
                ASRCandidate.deleted_at.is_(None),
            )
            .order_by(ASRCandidate.created_at.desc())
        ).first()
        candidate = ASRCandidate(
            segment_id=segment.id,
            model_id=evidence.model_id,
            model_revision=evidence.model_revision,
            raw_text=evidence.raw_text,
            normalized_text=evidence.normalized_text,
            confidence_raw=evidence.confidence_raw,
            confidence_calibrated=None,
            quality_features_json={
                "stage": "body_asr_unscored",
                "candidate_role": evidence.role.value,
                "audio_start_sample": evidence.audio_span.start_sample,
                "audio_end_sample": evidence.audio_span.end_sample,
                "core_start_sample": evidence.core_span.start_sample,
                "core_end_sample": evidence.core_span.end_sample,
                "provenance": dict(evidence.provenance),
            },
            warnings_json=[
                {"code": "model_warning", "detail": warning} for warning in evidence.warnings
            ],
            decode_config_json=dict(evidence.decode),
            inference_metrics_json=dict(evidence.metrics),
            is_valid=True,
            is_adopted=False,
            supersedes_candidate_id=previous.id if previous is not None else None,
        )
        session.add(candidate)
        session.flush()
        session.add_all(
            TokenSpan(
                candidate_id=candidate.id,
                segment_id=segment.id,
                start_sample=token.span.start_sample,
                end_sample=token.span.end_sample,
                token=token.text,
                normalized_token=token.text.casefold(),
                confidence=token.confidence_raw,
                provenance_json={
                    **evidence.provenance,
                    "source_token_index": token.source_index,
                    "native_model_time": True,
                },
            )
            for token in evidence.tokens
        )
        return candidate

    def _evidence(
        self, session: Session, segment: TranscriptSegment, candidate: ASRCandidate
    ) -> ASRCandidateEvidence:
        features = candidate.quality_features_json
        tokens = tuple(
            ASRTokenEvidence(
                item.token,
                AudioSpan(item.start_sample, item.end_sample),
                item.confidence,
                index,
            )
            for index, item in enumerate(
                session.scalars(
                    select(TokenSpan)
                    .where(TokenSpan.candidate_id == candidate.id)
                    .order_by(TokenSpan.start_sample, TokenSpan.id)
                )
            )
        )
        return ASRCandidateEvidence(
            candidate.model_id,
            candidate.model_revision,
            CandidateRole(str(features.get("candidate_role", CandidateRole.PRIMARY.value))),
            segment.language.value,
            segment.language.value,
            AudioSpan(
                int(features.get("audio_start_sample", segment.start_sample)),
                int(features.get("audio_end_sample", segment.end_sample)),
            ),
            self._span(segment),
            candidate.raw_text,
            candidate.normalized_text,
            candidate.confidence_raw,
            tokens,
            dict(candidate.decode_config_json),
            dict(candidate.inference_metrics_json),
            tuple(str(item.get("detail", "")) for item in candidate.warnings_json),
            dict(features.get("provenance", {})),
        )

    def _quality_context(
        self,
        session: Session,
        job: Job,
        segment: TranscriptSegment,
        voiced: tuple[AudioSpan, ...],
    ) -> QualityContext:
        recording = session.get_one(Recording, job.recording_id)
        qc = recording.audio_qc_json
        channels = qc.get("channels", [])
        selected = int(qc.get("selected_channel", 0))
        channel = (
            channels[selected]
            if isinstance(channels, list)
            and 0 <= selected < len(channels)
            and isinstance(channels[selected], dict)
            else {}
        )
        structure_text = " ".join(
            item.coarse_text
            for item in session.scalars(
                select(StructureSegmentRecord)
                .where(
                    StructureSegmentRecord.job_id == job.id,
                    StructureSegmentRecord.start_sample < segment.end_sample,
                    StructureSegmentRecord.end_sample > segment.start_sample,
                )
                .order_by(StructureSegmentRecord.start_sample)
            )
            if item.coarse_text.strip()
        )
        terms = self._course_terms(session, job, segment.language.value)
        normalized_raw = segment.raw_text.casefold()
        suspected_term_error = any(
            canonical.casefold() not in normalized_raw
            and any(alias.casefold() in normalized_raw for alias in term.aliases)
            for term in terms
            for canonical in (term.canonical,)
        )
        voiced_samples = _union_samples(voiced)
        return QualityContext(
            requested_span=self._span(segment),
            voiced_spans=voiced,
            vad_speech_ratio=voiced_samples / max(segment.end_sample - segment.start_sample, 1),
            snr_db=_optional_float(channel.get("snr_db_approx")),
            clipping_ratio=_bounded_probability(channel.get("clipping_ratio", 0.0)),
            volume_rms=_bounded_probability(channel.get("rms", 0.0)),
            far_field=bool(
                _optional_float(channel.get("snr_db_approx")) is not None
                and float(channel["snr_db_approx"]) < 10
            ),
            structure_text=structure_text,
            suspected_term_error=suspected_term_error,
        )

    def _comparison_context(self, session: Session, job: Job) -> ComparisonContext:
        terms = self._course_terms(session, job, None)
        return ComparisonContext(
            readings={item.canonical: item.reading for item in terms},
            english_phonemes={},
            course_terms=frozenset(item.canonical for item in terms),
        )

    def _course_terms(
        self, session: Session, job: Job, language: str | None
    ) -> tuple[CourseTerm, ...]:
        glossary_id = job.options_json.get("glossary_id")
        if not isinstance(glossary_id, str):
            return ()
        rows = tuple(
            session.scalars(
                select(GlossaryTerm)
                .where(GlossaryTerm.glossary_id == glossary_id)
                .order_by(GlossaryTerm.canonical, GlossaryTerm.id)
            )
        )
        return tuple(
            CourseTerm(
                item.canonical,
                item.reading or item.canonical,
                tuple(item.aliases),
                item.weight,
                TermSource(item.source),
                (
                    ConfirmationStatus.CONFIRMED
                    if item.user_confirmed
                    else ConfirmationStatus.SUGGESTED
                ),
                item.language.value,
            )
            for item in rows
            if language is None or item.language.value == language
        )

    def _confirmed_term_names(self, session: Session, job: Job) -> tuple[str, ...]:
        return tuple(
            item.canonical for item in self._course_terms(session, job, None) if item.user_confirmed
        )

    def _asr_context(
        self,
        session: Session,
        job: Job,
        segment: TranscriptSegment,
    ) -> tuple[tuple[PronunciationHint, ...], tuple[str, ...]]:
        glossary_id = job.options_json.get("glossary_id")
        if not isinstance(glossary_id, str):
            return (), ()
        terms = self._course_terms(session, job, segment.language.value)
        previous = tuple(
            session.scalars(
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.job_id == job.id,
                    TranscriptSegment.is_active.is_(True),
                    TranscriptSegment.end_sample <= segment.start_sample,
                    TranscriptSegment.user_text.is_not(None),
                )
                .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
            )
        )
        history = tuple(
            ConfirmedSegment(
                item.user_text or "",
                item.language.value,
                glossary_id,
                None,
                ordinal,
            )
            for ordinal, item in enumerate(previous)
            if item.user_text
        )
        history_text = " ".join(item.text for item in history).casefold()
        usages = tuple(
            TermUsage(
                term,
                glossary_id,
                sum(
                    history_text.count(form.casefold())
                    for form in (term.canonical, *term.aliases)
                    if form
                ),
            )
            for term in terms
        )
        bundle = select_rolling_context(
            course_id=glossary_id,
            chapter=None,
            language=segment.language.value,
            history=history,
            usages=usages,
        )
        context = _bounded_recent_context(bundle.confirmed_segments)
        return (
            tuple(
                PronunciationHint(term.canonical, term.reading, language=segment.language.value)
                for term in bundle.keywords
            ),
            context,
        )

    @staticmethod
    def _acoustic_boundaries(
        session: Session,
        segment: TranscriptSegment,
        text: str,
    ) -> tuple[AcousticBoundary, ...]:
        characters = len(strip_punctuation_and_spacing(text))
        if not characters or text.rstrip().endswith((".", "!", "?", "。", "\uff01", "\uff1f")):
            return ()
        following = session.scalar(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.job_id == segment.job_id,
                TranscriptSegment.is_active.is_(True),
                TranscriptSegment.start_sample >= segment.end_sample,
                TranscriptSegment.id != segment.id,
            )
            .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
        )
        pause_ms = (
            max(0, (following.start_sample - segment.end_sample) * 1000 // SAMPLE_RATE)
            if following is not None
            else 700
        )
        speaker_changed = bool(
            following is not None
            and following.speaker_id
            and segment.speaker_id
            and following.speaker_id != segment.speaker_id
        )
        return (AcousticBoundary(characters, pause_ms, speaker_changed),)

    def _punctuation_proposal(self, job: Job, text: str, language: str) -> str | None:
        if language not in {"zh", "en"} or not text.strip() or not self._installed("firered_punc"):
            return None
        entry = self.registry.model("firered_punc")
        request = build_punctuation_request(
            request_id=f"{job.id}:punctuation:{uuid4().hex}",
            job_id=job.id,
            entry=entry,
            text=text,
            language=language,
        )
        return parse_punctuation_response(asyncio.run(self.invoke(entry, request)), request, entry)

    def _primary_entry(
        self,
        parameters: Mapping[str, Any],
        language: str,
        session: Session | None = None,
    ) -> ModelEntry:
        if language not in {"zh", "ja", "en"}:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "body ASR language is unresolved")
        candidates: tuple[ModelEntry, ...]
        selected = parameters.get("primary_model_id")
        if isinstance(selected, str) and selected:
            candidates = (self.registry.model(selected),)
        else:
            candidates = tuple(
                self.registry.model(model_id)
                for model_id in self._ordered_model_ids(parameters, language, session)
            )
        compatible = tuple(
            item
            for item in candidates
            if item.enabled
            and not is_structure_model(item)
            and "asr" in item.tasks
            and "batch" in item.modes
            and (language in item.languages or "auto" in item.languages)
            and not (item.worker == "firered" and language == "ja")
        )
        if not compatible:
            raise ClassScribeError(
                ErrorCode.JOB_STATE_CONFLICT, f"no compatible {language} body ASR is configured"
            )
        return next((item for item in compatible if self._installed(item.id)), compatible[0])

    def _fallback_entries(
        self,
        parameters: Mapping[str, Any],
        language: str,
        primary_id: str,
        session: Session | None = None,
    ) -> tuple[ModelEntry, ...]:
        ranking = self._ordered_model_ids(parameters, language, session)
        return tuple(
            entry
            for model_id in ranking
            if model_id != primary_id
            for entry in (self.registry.model(model_id),)
            if entry.enabled
            and not is_structure_model(entry)
            and "asr" in entry.tasks
            and "batch" in entry.modes
            and (language in entry.languages or "auto" in entry.languages)
            and not (entry.worker == "firered" and language == "ja")
            and self._installed(entry.id)
        )

    def _ordered_model_ids(
        self,
        parameters: Mapping[str, Any],
        language: str,
        session: Session | None,
    ) -> tuple[str, ...]:
        bootstrap = self.registry.rankings[f"classroom.{language}"]
        if parameters.get("model_selection") != ModelSelectionMode.AUTO_BEST.value:
            return bootstrap
        profile = self._local_profile(session, language)
        return profile[0] if profile is not None else bootstrap

    def classroom_model_order(self, language: str, session: Session) -> tuple[str, ...]:
        """Expose the effective ranking for UI disclosure without loading or hashing models."""
        return self._ordered_model_ids({"model_selection": "auto_best"}, language, session)

    def _local_profile(
        self, session: Session | None, language: str
    ) -> tuple[tuple[str, ...], BenchmarkRun, Mapping[str, Mapping[str, Any]]] | None:
        if session is None:
            return None
        setting = session.scalar(
            select(ProfileSetting).where(
                ProfileSetting.language == language,
                ProfileSetting.scenario == "classroom",
            )
        )
        if setting is None or setting.benchmark_run_id is None:
            return None
        benchmark = session.get(BenchmarkRun, setting.benchmark_run_id)
        if benchmark is None or benchmark.status is not BenchmarkStatus.COMPLETED:
            return None
        parameters = benchmark.parameters_json
        manifest_sha256 = parameters.get("manifest_sha256")
        if not (
            parameters.get("production_gold") is True
            and parameters.get("real_model_execution") is True
            and parameters.get("synthetic_gold") is False
            and isinstance(manifest_sha256, str)
            and len(manifest_sha256) == 64
        ):
            return None
        raw_models = setting.config_json.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            return None
        model_ids = tuple(item for item in raw_models if isinstance(item, str))
        if len(model_ids) != len(raw_models) or len(model_ids) != len(set(model_ids)):
            return None
        ranking = next(
            (
                item
                for item in benchmark.ranking_json
                if item.get("language") == language
                and item.get("scenario") == "classroom"
                and item.get("passed") is True
                and item.get("models") == list(model_ids)
            ),
            None,
        )
        if ranking is None or not isinstance(ranking.get("candidates"), list):
            return None
        raw_calibrations = parameters.get("calibrations")
        if not isinstance(raw_calibrations, list):
            return None
        calibrations = {
            str(item.get("model_id")): item
            for item in raw_calibrations
            if isinstance(item, dict)
            and item.get("language") == language
            and item.get("scenario") == "classroom"
        }
        ranked_candidates = {
            str(item.get("model_id")): item
            for item in ranking["candidates"]
            if isinstance(item, dict) and item.get("eligible") is True
        }
        for model_id in model_ids:
            try:
                entry = self.registry.model(model_id)
            except KeyError:
                return None
            calibration = calibrations.get(model_id)
            ranked = ranked_candidates.get(model_id)
            if (
                calibration is None
                or ranked is None
                or calibration.get("model_revision") != entry.revision
                or calibration.get("manifest_sha256") != manifest_sha256
                or ranked.get("model_revision") != entry.revision
            ):
                return None
        return model_ids, benchmark, calibrations

    def _consensus_calibration(
        self,
        session: Session,
        language: str,
        candidates: tuple[ConsensusCandidate, ...],
    ) -> tuple[ReliabilityProfile, dict[tuple[str, int], float]]:
        profile = self._local_profile(session, language)
        if profile is None:
            return ReliabilityProfile({}, "bootstrap-neutral", False, "classroom"), {}
        _model_ids, benchmark, raw_artifacts = profile
        ranking = next(
            item
            for item in benchmark.ranking_json
            if item.get("language") == language and item.get("scenario") == "classroom"
        )
        ranked_candidates = {
            str(item.get("model_id")): item
            for item in ranking.get("candidates", [])
            if isinstance(item, dict)
        }
        reliability: dict[str, float] = {}
        calibrated_tokens: dict[tuple[str, int], float] = {}
        for candidate in candidates:
            model_id = candidate.evidence.model_id
            ranked = ranked_candidates.get(model_id, {})
            metrics = ranked.get("metrics")
            body_key = "normalized_wer" if language == "en" else "normalized_cer"
            error_rate = metrics.get(body_key) if isinstance(metrics, Mapping) else None
            if isinstance(error_rate, (int, float)) and not isinstance(error_rate, bool):
                reliability[model_id] = max(0.0, min(1.0, 1.0 - float(error_rate)))
            raw = candidate.evidence.confidence_raw
            artifact_data = raw_artifacts.get(model_id)
            if raw is None or artifact_data is None:
                continue
            try:
                artifact = CalibrationArtifact(**artifact_data)
                probability = artifact.predict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            row = session.get_one(ASRCandidate, candidate.candidate_id)
            row.confidence_calibrated = probability
            for token in candidate.evidence.tokens:
                calibrated_tokens[(candidate.candidate_id, token.source_index)] = probability
        if not reliability:
            return ReliabilityProfile({}, "bootstrap-neutral", False, "classroom"), {}
        return (
            ReliabilityProfile(
                reliability,
                f"local-gold:{benchmark.id}",
                True,
                "classroom",
            ),
            calibrated_tokens,
        )

    def _installed(self, model_id: str) -> bool:
        try:
            self.manager.resolve_for_runtime(model_id)
        except (ClassScribeError, OSError, ValueError):
            return False
        return True

    @staticmethod
    def _segment(session: Session, checkpoint: JobCheckpoint) -> TranscriptSegment:
        if checkpoint.segment_id is None:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "segment checkpoint lacks ID")
        segment = session.get(TranscriptSegment, checkpoint.segment_id)
        if segment is None or not segment.is_active:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "active segment is unavailable")
        return segment

    @staticmethod
    def _span(segment: TranscriptSegment) -> AudioSpan:
        return AudioSpan(segment.start_sample, segment.end_sample)

    @staticmethod
    def _voiced_spans(session: Session, job_id: str, container: AudioSpan) -> tuple[AudioSpan, ...]:
        return tuple(
            AudioSpan(
                max(container.start_sample, item.start_sample),
                min(container.end_sample, item.end_sample),
            )
            for item in session.scalars(
                select(SpeechRegion)
                .where(
                    SpeechRegion.job_id == job_id,
                    SpeechRegion.start_sample < container.end_sample,
                    SpeechRegion.end_sample > container.start_sample,
                )
                .order_by(SpeechRegion.start_sample)
            )
        )

    def _quality_issues(self, session: Session, segment_id: str) -> tuple[QualityIssue, ...]:
        candidate = session.scalars(
            select(ASRCandidate)
            .where(ASRCandidate.segment_id == segment_id, ASRCandidate.is_adopted.is_(True))
            .order_by(ASRCandidate.created_at.desc())
        ).first()
        values = candidate.quality_features_json.get("issues", []) if candidate else []
        return tuple(
            QualityIssue(str(value))
            for value in values
            if str(value) in {item.value for item in QualityIssue}
        )

    def _master_path(self, recording_id: str) -> Path:
        return self.paths.data_path("recordings", recording_id, "derived", "audio_master.wav")

    @staticmethod
    def _export_segments(session: Session, job_id: str) -> tuple[ExportSegment, ...]:
        result: list[ExportSegment] = []
        segments = tuple(
            session.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.job_id == job_id, TranscriptSegment.is_active.is_(True))
                .order_by(TranscriptSegment.start_sample, TranscriptSegment.id)
            )
        )
        for segment in segments:
            tokens = tuple(
                ExportToken(
                    item.token,
                    AudioSpan(item.start_sample, item.end_sample),
                    protected_group=(
                        str(item.provenance_json["protected_group"])
                        if item.provenance_json.get("protected_group")
                        else None
                    ),
                    provenance=dict(item.provenance_json),
                )
                for item in session.scalars(
                    select(TokenSpan)
                    .where(TokenSpan.segment_id == segment.id, TokenSpan.candidate_id.is_(None))
                    .order_by(TokenSpan.start_sample, TokenSpan.id)
                )
            )
            result.append(
                ExportSegment(
                    segment.id,
                    AudioSpan(segment.start_sample, segment.end_sample),
                    segment.language.value,
                    segment.raw_text,
                    segment.faithful_text,
                    segment.smart_corrected_text,
                    segment.user_text,
                    tokens,
                    smart_tokens=tokens,
                    user_tokens=tokens if segment.user_text else (),
                    speaker=segment.speaker_id,
                )
            )
        return tuple(result)


def _speech_region(value: Mapping[str, Any]) -> SpeechRegionResult:
    start = value.get("start_sample")
    end = value.get("end_sample")
    score = value.get("vad_score")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
    ):
        raise ValueError("VAD response contains invalid sample or score fields")
    return SpeechRegionResult(
        AudioSpan(start, end),
        _bounded_probability(score),
        str(value.get("acoustic_class", "speech")),
        str(value.get("source", "firered_vad")),
    )


def _validate_spans(spans: Sequence[AudioSpan], duration: int) -> None:
    previous = 0
    for span in spans:
        if span.start_sample < previous or span.end_sample > duration:
            raise ClassScribeError(ErrorCode.AUDIO_TIMELINE_INVALID, "worker spans are invalid")
        previous = span.end_sample


def _union_samples(spans: Sequence[AudioSpan]) -> int:
    if not spans:
        return 0
    ordered = sorted(spans, key=lambda item: (item.start_sample, item.end_sample))
    total = 0
    start, end = ordered[0].start_sample, ordered[0].end_sample
    for span in ordered[1:]:
        if span.start_sample <= end:
            end = max(end, span.end_sample)
        else:
            total += end - start
            start, end = span.start_sample, span.end_sample
    return total + end - start


def _silence_points(spans: Sequence[AudioSpan]) -> tuple[int, ...]:
    return tuple(
        left.end_sample + (right.start_sample - left.end_sample) // 2
        for left, right in pairwise(spans)
        if right.start_sample > left.end_sample
    )


def _bounded_probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be numeric")
    result = float(value)
    if not 0 <= result <= 1:
        raise ValueError("probability must be within [0, 1]")
    return result


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("optional metric must be numeric")
    return float(value)


def _bounded_recent_context(values: Sequence[str], limit: int = 2400) -> tuple[str, ...]:
    selected: list[str] = []
    remaining = limit
    for value in reversed(values[-3:]):
        text = value.strip()
        if not text or remaining <= 0:
            continue
        selected.append(text[-remaining:])
        remaining -= len(selected[-1])
    return tuple(reversed(selected))


def _dominant_language(span: AudioSpan, rows: Sequence[LanguageSpan]) -> LanguageMode:
    matching = [
        (min(span.end_sample, item.end_sample) - max(span.start_sample, item.start_sample), item)
        for item in rows
        if item.start_sample < span.end_sample and span.start_sample < item.end_sample
    ]
    if not matching:
        raise ClassScribeError(ErrorCode.AUDIO_TIMELINE_INVALID, "segment has no language route")
    return max(matching, key=lambda item: (item[0], item[1].confidence_raw))[1].language


def _dominant_speaker(span: AudioSpan, rows: Sequence[Any]) -> str | None:
    matching = [
        (
            min(span.end_sample, item.span.end_sample)
            - max(span.start_sample, item.span.start_sample),
            item.speaker_global,
        )
        for item in rows
        if item.speaker_global
        and item.span.start_sample < span.end_sample
        and span.start_sample < item.span.end_sample
    ]
    return max(matching, default=(0, None), key=lambda item: item[0])[1]


def _coarse_timing(text: str, span: AudioSpan, language: str = "ja") -> TimingEvidence:
    characters = tuple(re.findall(r"\S+", text)) if language == "en" else tuple(text)
    if not characters or span.duration_samples < len(characters):
        raise ClassScribeError(
            ErrorCode.CANDIDATE_TIMELINE_INVALID,
            "final text cannot be assigned positive coarse token intervals",
        )
    boundaries = [
        span.start_sample + span.duration_samples * index // len(characters)
        for index in range(len(characters) + 1)
    ]
    return TimingEvidence(
        TimingSource.VAD_COARSE,
        span,
        text,
        tuple(
            AlignedToken(character, AudioSpan(boundaries[index], boundaries[index + 1]))
            for index, character in enumerate(characters)
        ),
        reliable=True,
        text_unchanged=True,
    )
