"""Registry-bound Qwen forced-alignment RPC contract."""

from __future__ import annotations

from pathlib import Path

from classscribe_protocol import Priority, RPCRequest, RPCResponse

from classscribe.alignment.gate import AlignmentGateDecision
from classscribe.alignment.models import AlignedToken, TimingEvidence, TimingSource
from classscribe.models.registry import ModelEntry
from classscribe.timeline import SAMPLE_RATE, AudioSpan


def build_alignment_request(
    *,
    request_id: str,
    job_id: str,
    audio_path: Path,
    entry: ModelEntry,
    canonical_span: AudioSpan,
    final_text: str,
    language: str,
    gate: AlignmentGateDecision,
    deadline_ms: int = 180_000,
) -> RPCRequest:
    if entry.id != "qwen3_forced_aligner_0_6b" or "alignment" not in entry.tasks:
        raise ValueError("forced alignment requires the registered Qwen aligner")
    if not gate.allowed:
        raise ValueError("forced alignment gate did not pass")
    return RPCRequest(
        request_id=request_id,
        job_id=job_id,
        deadline_ms=deadline_ms,
        priority=Priority.BACKGROUND,
        method="align",
        params={
            "audio_path": str(audio_path.resolve()),
            "start_sample": canonical_span.start_sample,
            "end_sample": canonical_span.end_sample,
            "sample_rate": SAMPLE_RATE,
            "text": final_text,
            "language": language,
            "manual_language": True,
            "alignment_contract": "final-align-v1",
            "safe_max_seconds": gate.safe_max_seconds,
            "quality_gate": dict(gate.quality_gate),
        },
    )


def parse_alignment_response(
    response: RPCResponse,
    request: RPCRequest,
    entry: ModelEntry,
    canonical_span: AudioSpan,
) -> TimingEvidence:
    if not response.ok:
        raise ValueError(f"alignment request failed: {response.error_code}")
    if response.model_id != entry.id or response.model_revision != entry.revision:
        raise ValueError("alignment response model identity differs from its registry route")
    final_text = str(request.params["text"])
    if response.raw_text != final_text or response.normalized_text != final_text:
        raise ValueError("forced aligner changed the final text")
    if len(response.segments) != 1:
        raise ValueError("forced aligner must return exactly one canonical segment")
    segment = response.segments[0]
    if (
        segment.get("start_sample") != canonical_span.start_sample
        or segment.get("end_sample") != canonical_span.end_sample
    ):
        raise ValueError("forced alignment segment escaped its canonical range")
    words = segment.get("words")
    if not isinstance(words, list):
        raise ValueError("forced alignment response is missing word timings")
    tokens = tuple(
        AlignedToken(
            str(item["text"]),
            AudioSpan(int(item["start_sample"]), int(item["end_sample"])),
        )
        for item in words
        if isinstance(item, dict)
    )
    return TimingEvidence(
        TimingSource.QWEN_FORCED,
        canonical_span,
        final_text,
        tokens,
        reliable=True,
        text_unchanged=True,
        alignment_cost=_optional_float(response.metrics.get("alignment_cost")),
        vad_gap_error_ms=_optional_float(response.metrics.get("vad_gap_error_ms")),
    )


def _optional_float(value: int | float | str | bool | None) -> float | None:
    return None if value is None else float(value)
