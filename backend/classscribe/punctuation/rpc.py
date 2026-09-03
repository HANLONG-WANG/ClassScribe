"""Registry-bound FireRedPunc request and strict response parsing."""

from __future__ import annotations

from classscribe_protocol import Priority, RPCRequest, RPCResponse

from classscribe.models.registry import ModelEntry
from classscribe.punctuation.guard import require_character_invariance


def build_punctuation_request(
    *,
    request_id: str,
    job_id: str,
    entry: ModelEntry,
    text: str,
    language: str,
    deadline_ms: int = 120_000,
) -> RPCRequest:
    if entry.id != "firered_punc" or "punctuation" not in entry.tasks:
        raise ValueError("punctuation requests require the registered FireRedPunc entry")
    if language not in {"zh", "en"} or language not in entry.languages or not text.strip():
        raise ValueError("FireRedPunc requires non-empty zh/en text")
    return RPCRequest(
        request_id=request_id,
        job_id=job_id,
        deadline_ms=deadline_ms,
        priority=Priority.BACKGROUND,
        method="punctuate",
        params={
            "text": text,
            "language": language,
            "manual_language": True,
            "punctuation_contract": "strict-punctuation-v1",
        },
    )


def parse_punctuation_response(
    response: RPCResponse, request: RPCRequest, entry: ModelEntry
) -> str:
    if not response.ok:
        raise ValueError(f"punctuation request failed: {response.error_code}")
    if response.model_id != entry.id or response.model_revision != entry.revision:
        raise ValueError("punctuation response model identity differs from its registry route")
    source = str(request.params["text"])
    if response.raw_text != source:
        raise ValueError("punctuation worker did not echo the exact source text")
    require_character_invariance(source, response.normalized_text)
    return response.normalized_text
