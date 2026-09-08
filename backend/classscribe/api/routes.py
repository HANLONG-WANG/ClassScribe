"""Complete `/api/v1` route surface for the local classroom workbench."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from classscribe.api.schemas import (
    ApplyRanking,
    BenchmarkCreate,
    CandidateAdoption,
    ExportCreate,
    GlossaryCreate,
    GlossaryTermsUpdate,
    HistoryAction,
    JobCreate,
    ModelInstallConfirmation,
    ModelInstallRequest,
    ModelRevisionRequest,
    ProfileUpdate,
    SegmentMerge,
    SegmentPatch,
    SegmentSplit,
    SettingsUpdate,
)
from classscribe.api.service import ClassScribeService
from classscribe.db.models import JobStatus
from classscribe.terminology import TermSource


async def uploaded_filename(
    source_name: Annotated[str, Header(alias="X-ClassScribe-Filename")],
    encoding: Annotated[
        Literal["utf-8-percent"] | None, Header(alias="X-ClassScribe-Filename-Encoding")
    ] = None,
) -> str:
    if encoding is None:
        return source_name
    try:
        return unquote(source_name, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid UTF-8 filename") from exc


def create_api_router(service: ClassScribeService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/recordings", status_code=status.HTTP_201_CREATED)
    async def create_recording(
        request: Request,
        source_name: str = Depends(uploaded_filename),
        duration_samples: int = Header(alias="X-ClassScribe-Duration-Samples"),
        channels: int = Header(alias="X-ClassScribe-Channels"),
        sample_rate: int = Header(alias="X-ClassScribe-Sample-Rate"),
    ) -> dict[str, object]:
        return service.create_recording(
            source_name=source_name,
            content=await request.body(),
            duration_samples=duration_samples,
            channels=channels,
            sample_rate=sample_rate,
        )

    @router.get("/recordings")
    async def list_recordings() -> list[dict[str, object]]:
        return service.recordings()

    @router.get("/recordings/{recording_id}")
    async def get_recording(recording_id: str) -> dict[str, object]:
        return service.recording(recording_id)

    @router.get("/recordings/{recording_id}/media")
    async def get_recording_media(recording_id: str) -> Response:
        path, source_name = service.recording_file(recording_id)
        return FileResponse(path, filename=source_name)

    @router.post("/jobs", status_code=status.HTTP_201_CREATED)
    async def create_job(value: JobCreate) -> dict[str, object]:
        return service.create_job(value)

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, object]:
        return service.job(job_id)

    @router.get("/jobs/{job_id}/events")
    async def job_events(
        job_id: str, last_event_id: int = Header(default=0, alias="Last-Event-ID")
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            sequence = last_event_id
            retained = service.pipeline.broker.after(job_id) if service.pipeline else ()
            if sequence > (retained[-1].sequence if retained else 0):
                sequence = 0
                reset = json.dumps(
                    {
                        "sequence": 0,
                        "job_id": job_id,
                        "kind": "stream_reset",
                        "occurred_at": "",
                        "payload": {},
                    }
                )
                yield f"event: stream_reset\ndata: {reset}\n\n"
            while True:
                events = service.pipeline.broker.after(job_id, sequence) if service.pipeline else ()
                for event in events:
                    sequence = event.sequence
                    data = json.dumps(
                        {
                            "sequence": event.sequence,
                            "job_id": event.job_id,
                            "kind": event.kind,
                            "occurred_at": event.occurred_at,
                            "payload": event.payload,
                        },
                        ensure_ascii=False,
                    )
                    yield f"id: {event.sequence}\nevent: {event.kind}\ndata: {data}\n\n"
                snapshot = service.job(job_id)
                if (
                    snapshot["status"]
                    in {
                        JobStatus.COMPLETED.value,
                        JobStatus.CANCELLED.value,
                        JobStatus.FAILED.value,
                    }
                    and not events
                ):
                    break
                if not events:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.post("/jobs/{job_id}/pause")
    async def pause_job(job_id: str) -> dict[str, object]:
        return service.pause_job(job_id)

    @router.post("/jobs/{job_id}/resume")
    async def resume_job(job_id: str) -> dict[str, object]:
        return service.resume_job(job_id)

    @router.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, object]:
        return service.cancel_job(job_id)

    @router.post("/jobs/{job_id}/retry")
    async def retry_job(job_id: str) -> dict[str, object]:
        return service.retry_job(job_id)

    @router.post("/jobs/{job_id}/retry-segment/{segment_id}")
    async def retry_segment(job_id: str, segment_id: str) -> dict[str, object]:
        return service.retry_segment(job_id, segment_id)

    @router.get("/jobs/{job_id}/transcript")
    async def transcript(
        job_id: str, low_confidence_only: bool = Query(default=False)
    ) -> dict[str, object]:
        return service.transcript(job_id, low_confidence_only=low_confidence_only)

    @router.get("/segments/{segment_id}")
    async def segment(segment_id: str) -> dict[str, object]:
        return service.segment(segment_id)

    @router.patch("/segments/{segment_id}")
    async def patch_segment(segment_id: str, value: SegmentPatch) -> dict[str, object]:
        return service.patch_segment(segment_id, value)

    @router.post("/segments/{segment_id}/undo")
    async def undo_segment(segment_id: str, value: HistoryAction) -> dict[str, object]:
        return service.undo_segment(segment_id, value.version)

    @router.post("/segments/{segment_id}/redo")
    async def redo_segment(segment_id: str, value: HistoryAction) -> dict[str, object]:
        return service.redo_segment(segment_id, value.version)

    @router.get("/segments/{segment_id}/candidates")
    async def candidates(segment_id: str) -> list[dict[str, object]]:
        return service.candidates(segment_id)

    @router.post("/segments/{segment_id}/adopt-candidate")
    async def adopt_candidate(segment_id: str, value: CandidateAdoption) -> dict[str, object]:
        return service.adopt_candidate(segment_id, value)

    @router.post("/segments/{segment_id}/rerun")
    async def rerun_segment(segment_id: str) -> dict[str, object]:
        segment = service.segment(segment_id)
        return service.retry_segment(str(segment["job_id"]), segment_id)

    @router.post("/segments/{segment_id}/split")
    async def split_segment(segment_id: str, value: SegmentSplit) -> dict[str, object]:
        return service.split_segment(segment_id, value)

    @router.post("/segments/{segment_id}/merge")
    async def merge_segments(segment_id: str, value: SegmentMerge) -> dict[str, object]:
        return service.merge_segments(segment_id, value)

    @router.get("/models")
    async def models() -> list[dict[str, object]]:
        return service.models()

    @router.get("/models/{model_id}/manifest")
    async def model_manifest(model_id: str) -> dict[str, object]:
        return service.model_manifest(model_id)

    @router.post("/models/{model_id}/install", status_code=status.HTTP_202_ACCEPTED)
    async def install_model(model_id: str, _value: ModelInstallRequest) -> dict[str, object]:
        return service.request_bundled_model_install(model_id)

    @router.post("/models/{model_id}/install/confirm")
    async def confirm_model_install(
        model_id: str, value: ModelInstallConfirmation
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            service.confirm_model_install,
            model_id,
            confirmation_token=value.confirmation_token,
            health_recording_id=value.health_recording_id,
            health_language=value.health_language,
            health_transcript=value.health_transcript,
            terms_accepted=value.terms_accepted,
        )

    @router.post("/models/{model_id}/verify")
    async def verify_model(model_id: str) -> dict[str, object]:
        return service.verify_model(model_id)

    @router.delete("/models/{model_id}")
    async def delete_model(model_id: str, revision: str = Query()) -> dict[str, object]:
        return service.delete_model(model_id, revision)

    @router.post("/models/{model_id}/rollback")
    async def rollback_model(model_id: str, value: ModelRevisionRequest) -> dict[str, object]:
        return service.rollback_model(model_id, value.revision)

    @router.get("/profiles")
    async def profiles() -> list[dict[str, object]]:
        return service.profiles()

    @router.put("/profiles/{language}/{scenario}")
    async def update_profile(
        language: str, scenario: str, value: ProfileUpdate
    ) -> dict[str, object]:
        return service.update_profile(language, scenario, value)

    @router.post("/profiles/{language}/{scenario}/rollback")
    async def rollback_profile(language: str, scenario: str) -> dict[str, object]:
        return service.rollback_profile(language, scenario)

    @router.get("/settings")
    async def settings() -> dict[str, object]:
        return service.settings()

    @router.put("/settings")
    async def update_settings(value: SettingsUpdate) -> dict[str, object]:
        return service.update_settings(value.values)

    @router.get("/glossaries")
    async def glossaries() -> list[dict[str, object]]:
        return service.glossaries()

    @router.post("/glossaries", status_code=status.HTTP_201_CREATED)
    async def create_glossary(value: GlossaryCreate) -> dict[str, object]:
        return service.create_glossary(value)

    @router.delete("/glossaries/{glossary_id}")
    async def delete_glossary(glossary_id: str) -> dict[str, object]:
        return service.delete_glossary(glossary_id)

    @router.post("/glossaries/{glossary_id}/documents", status_code=status.HTTP_201_CREATED)
    async def add_glossary_document(
        glossary_id: str,
        request: Request,
        source_name: Annotated[str, Depends(uploaded_filename)],
        source_kind: Annotated[TermSource, Header(alias="X-ClassScribe-Material-Kind")],
        language: Annotated[str, Header(alias="X-ClassScribe-Language")],
    ) -> dict[str, object]:
        return service.add_glossary_document(
            glossary_id,
            source_name=source_name,
            source_kind=source_kind,
            language=language,
            content=await request.body(),
        )

    @router.put("/glossaries/{glossary_id}/terms")
    async def update_terms(glossary_id: str, value: GlossaryTermsUpdate) -> dict[str, object]:
        return service.update_terms(glossary_id, value)

    @router.delete("/glossaries/{glossary_id}/terms/{term_id}")
    async def delete_term(glossary_id: str, term_id: str) -> dict[str, object]:
        return service.delete_term(glossary_id, term_id)

    @router.post("/jobs/{job_id}/exports", status_code=status.HTTP_201_CREATED)
    async def create_export(job_id: str, value: ExportCreate) -> dict[str, object]:
        return service.create_export(job_id, value)

    @router.get("/exports/{export_id}")
    async def get_export(export_id: str) -> Response:
        path, metadata = service.export_file(export_id)
        return FileResponse(
            path,
            media_type=str(metadata["content_type"]),
            filename=str(metadata["file_name"]),
        )

    @router.post("/benchmarks", status_code=status.HTTP_202_ACCEPTED)
    async def create_benchmark(value: BenchmarkCreate) -> dict[str, object]:
        return service.create_benchmark(value)

    @router.get("/benchmarks/{benchmark_id}")
    async def benchmark(benchmark_id: str) -> dict[str, object]:
        return service.benchmark(benchmark_id)

    @router.get("/benchmarks/{benchmark_id}/results")
    async def benchmark_results(benchmark_id: str) -> dict[str, object]:
        return service.benchmark_results(benchmark_id)

    @router.post("/benchmarks/{benchmark_id}/apply-ranking")
    async def apply_ranking(benchmark_id: str, value: ApplyRanking) -> dict[str, object]:
        return service.apply_ranking(benchmark_id, value)

    return router
