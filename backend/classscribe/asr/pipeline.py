"""Registry-driven, sequential body-ASR orchestration for natural transcript chunks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from classscribe_protocol import RPCRequest, RPCResponse

from classscribe.asr.models import (
    ASRCandidateEvidence,
    ASRContractError,
    CandidateRole,
    PronunciationHint,
    build_asr_request,
    parse_asr_response,
)
from classscribe.audio.segmentation import TranscriptChunk
from classscribe.models.registry import ModelEntry, ModelRegistry

ModelRPCInvoker = Callable[[ModelEntry, RPCRequest], Awaitable[RPCResponse]]


@dataclass(frozen=True, slots=True)
class PlannedCandidate:
    entry: ModelEntry
    role: CandidateRole
    rank: int
    dispatch_body_asr: bool


@dataclass(frozen=True, slots=True)
class CandidateRunOutcome:
    planned: PlannedCandidate
    evidence: ASRCandidateEvidence | None
    error: str | None


class BodyASRPlanner:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def candidates(
        self,
        language: str,
        *,
        include_explicit_experts: bool = False,
    ) -> tuple[PlannedCandidate, ...]:
        profile = f"classroom.{language}"
        entries = self.registry.candidates(
            profile,
            language=language,
            task="asr",
            mode="batch",
            include_disabled=include_explicit_experts,
        )
        planned: list[PlannedCandidate] = []
        review_rank = 0
        for registry_rank, entry in enumerate(entries):
            structural = "diarization" in entry.tasks and "timestamps" in entry.tasks
            if structural:
                role = CandidateRole.STRUCTURE
            elif entry.experimental:
                role = CandidateRole.EXPERT
            else:
                role = (
                    CandidateRole.PRIMARY
                    if review_rank == 0
                    else CandidateRole.SECONDARY
                    if review_rank == 1
                    else CandidateRole.TERTIARY
                )
                review_rank += 1
            planned.append(
                PlannedCandidate(
                    entry=entry,
                    role=role,
                    rank=registry_rank,
                    dispatch_body_asr=not structural,
                )
            )
        if not any(item.role is CandidateRole.PRIMARY for item in planned):
            raise ASRContractError(f"profile has no enabled primary body ASR: {profile}")
        return tuple(planned)

    def japanese_experimental_variant(self) -> PlannedCandidate:
        """Expose the plan's JA flavor without inventing a nonexistent checkpoint identity."""

        entry = self.registry.model("qwen3_asr_1_7b")
        return PlannedCandidate(
            entry=entry,
            role=CandidateRole.EXPERIMENTAL,
            rank=90,
            dispatch_body_asr=True,
        )


class BodyASRPipeline:
    """Run requested candidates one at a time; no different-length batch is representable."""

    def __init__(self, registry: ModelRegistry, invoke: ModelRPCInvoker) -> None:
        self.planner = BodyASRPlanner(registry)
        self.invoke = invoke

    async def transcribe_primary(
        self,
        *,
        job_id: str,
        audio_path: Path,
        chunk: TranscriptChunk,
        language: str,
        hints: Sequence[PronunciationHint] = (),
    ) -> ASRCandidateEvidence:
        primary = next(
            item for item in self.planner.candidates(language) if item.role is CandidateRole.PRIMARY
        )
        outcomes = await self.transcribe_candidates(
            job_id=job_id,
            audio_path=audio_path,
            chunk=chunk,
            language=language,
            candidates=(primary,),
            hints=hints,
        )
        evidence = outcomes[0].evidence
        if evidence is None:
            raise ASRContractError(outcomes[0].error or "primary body ASR failed")
        return evidence

    async def transcribe_candidates(
        self,
        *,
        job_id: str,
        audio_path: Path,
        chunk: TranscriptChunk,
        language: str,
        candidates: Sequence[PlannedCandidate],
        hints: Sequence[PronunciationHint] = (),
        explicitly_enabled_experimental: bool = False,
        continue_on_error: bool = True,
    ) -> tuple[CandidateRunOutcome, ...]:
        if any(not item.dispatch_body_asr for item in candidates):
            raise ASRContractError("MOSS structure evidence is not a body-ASR dispatch")
        ids = [item.entry.id for item in candidates]
        if len(ids) != len(set(ids)):
            raise ASRContractError("candidate run contains a duplicate model")
        outcomes: list[CandidateRunOutcome] = []
        for index, planned in enumerate(candidates):
            if planned.entry.installation.state != "installed":
                raise ASRContractError(f"candidate is not installed: {planned.entry.id}")
            request = build_asr_request(
                request_id=f"{job_id}:body:{chunk.ordinal}:{index}:{planned.entry.id}",
                job_id=job_id,
                audio_path=audio_path,
                chunk=chunk,
                entry=planned.entry,
                language=language,
                role=planned.role,
                hints=hints,
                explicitly_enabled_experimental=explicitly_enabled_experimental,
            )
            try:
                evidence = parse_asr_response(
                    await self.invoke(planned.entry, request),
                    request,
                    planned.entry,
                    chunk,
                    planned.role,
                )
            except Exception as exc:
                outcomes.append(
                    CandidateRunOutcome(
                        planned,
                        None,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
                if not continue_on_error:
                    break
            else:
                outcomes.append(CandidateRunOutcome(planned, evidence, None))
        return tuple(outcomes)
