"""Short-transaction persistence for natural chunks and immutable ASR candidates."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.asr.models import ASRCandidateEvidence
from classscribe.audio.segmentation import TranscriptChunk
from classscribe.contracts import LanguageMode
from classscribe.db.models import ASRCandidate, Job, TimingQuality, TokenSpan, TranscriptSegment
from classscribe.errors import ClassScribeError, ErrorCode

_LANGUAGE_MODE = {
    "zh": LanguageMode.CHINESE,
    "ja": LanguageMode.JAPANESE,
    "en": LanguageMode.ENGLISH,
}


class ASRCandidateRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def ensure_natural_segment(
        self,
        job_id: str,
        chunk: TranscriptChunk,
        *,
        language: str,
        speaker_global_id: str | None,
    ) -> str:
        """Idempotently materialize the unique core; audio context remains request provenance."""

        if language not in _LANGUAGE_MODE:
            raise ValueError("natural body segment language must be zh, ja, or en")
        with self._sessions.begin() as session:
            if session.get(Job, job_id) is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "job does not exist")
            segment = session.scalar(
                select(TranscriptSegment).where(
                    TranscriptSegment.job_id == job_id,
                    TranscriptSegment.start_sample == chunk.core_span.start_sample,
                    TranscriptSegment.end_sample == chunk.core_span.end_sample,
                )
            )
            if segment is None:
                segment = TranscriptSegment(
                    job_id=job_id,
                    start_sample=chunk.core_span.start_sample,
                    end_sample=chunk.core_span.end_sample,
                    speaker_id=speaker_global_id,
                    language=_LANGUAGE_MODE[language],
                    raw_text="",
                    faithful_text="",
                    smart_corrected_text="",
                    timing_quality=TimingQuality.STRUCTURE,
                )
                session.add(segment)
                session.flush()
            elif segment.language != _LANGUAGE_MODE[language]:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "natural segment already exists with a different manual language",
                )
            return segment.id

    def add_candidate(self, segment_id: str, evidence: ASRCandidateEvidence) -> str:
        with self._sessions.begin() as session:
            segment = session.get(TranscriptSegment, segment_id)
            if segment is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "segment does not exist")
            if (
                segment.start_sample != evidence.core_span.start_sample
                or segment.end_sample != evidence.core_span.end_sample
            ):
                raise ClassScribeError(
                    ErrorCode.CANDIDATE_TIMELINE_INVALID,
                    "candidate core differs from its transcript segment",
                )
            previous = session.scalars(
                select(ASRCandidate)
                .where(
                    ASRCandidate.segment_id == segment_id,
                    ASRCandidate.model_id == evidence.model_id,
                    ASRCandidate.deleted_at.is_(None),
                )
                .order_by(ASRCandidate.created_at.desc())
            ).first()
            candidate = ASRCandidate(
                segment_id=segment_id,
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
                    "raw_confidence_not_cross_model_probability": True,
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
                    segment_id=segment_id,
                    start_sample=token.span.start_sample,
                    end_sample=token.span.end_sample,
                    token=token.text,
                    normalized_token=_normalize_token(token.text),
                    confidence=token.confidence_raw,
                    provenance_json={
                        **evidence.provenance,
                        "source_token_index": token.source_index,
                        "native_model_time": True,
                    },
                )
                for token in evidence.tokens
            )
            return candidate.id


def _normalize_token(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()
