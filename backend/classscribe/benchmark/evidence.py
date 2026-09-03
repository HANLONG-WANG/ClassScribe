"""Strict, non-self-certifying evidence contract for the Phase 12 test matrix."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REQUIRED_TEST_EVIDENCE = (
    "unit_and_property",
    "worker_contracts",
    "real_model_zh_ja_en",
    "pipeline_90m",
    "ibus_5m",
    "gpu_preemption",
    "oom_recovery",
    "service_restart",
    "invalid_input",
    "fixed_regressions",
)
WORKER_IDS = (
    "moss_td",
    "firered",
    "granite",
    "qwen",
    "ark",
    "moss_en",
    "nemotron",
    "pyannote",
    "funasr_experimental",
    "voxtral",
    "vibevoice",
)
WORKER_CASES = (
    "capability_schema",
    "cancellation",
    "deadline",
    "empty_audio",
    "silence",
    "extremely_short_audio",
    "invalid_language",
    "overlong_audio",
    "unicode_special_punctuation",
    "gpu_release_after_exit",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REAL_KINDS = {
    "worker_contracts",
    "real_model_zh_ja_en",
    "pipeline_90m",
    "ibus_5m",
    "gpu_preemption",
    "oom_recovery",
}


def validate_test_evidence(value: Mapping[str, Any]) -> tuple[dict[str, bool], dict[str, str]]:
    checks: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for key in REQUIRED_TEST_EVIDENCE:
        record = value.get(key)
        reason = _base_record_reason(record, require_real=key in _REAL_KINDS)
        if reason is None and key == "worker_contracts":
            reason = _worker_contract_reason(record)
        elif reason is None and key == "real_model_zh_ja_en":
            reason = _language_model_reason(record)
        checks[key] = reason is None
        reasons[key] = reason or "passed"
    return checks, reasons


def _base_record_reason(value: object, *, require_real: bool) -> str | None:
    if not isinstance(value, Mapping):
        return "evidence must be a structured record"
    if value.get("status") != "passed":
        return "status is not passed"
    kind = value.get("evidence_kind")
    allowed = (
        {"real_hardware", "real_model"}
        if require_real
        else {"automated", "real_hardware", "real_model"}
    )
    if kind not in allowed:
        return "evidence kind is not acceptable for this check"
    if not isinstance(value.get("command"), str) or not str(value["command"]).strip():
        return "executed command is missing"
    if not isinstance(value.get("completed_at"), str) or "T" not in str(value["completed_at"]):
        return "completion timestamp is missing"
    artifact = value.get("artifact_sha256")
    if not isinstance(artifact, str) or not _SHA256_RE.fullmatch(artifact):
        return "evidence artifact SHA-256 is missing or invalid"
    return None


def _worker_contract_reason(value: object) -> str | None:
    assert isinstance(value, Mapping)
    details = value.get("workers")
    if not isinstance(details, list):
        return "worker evidence list is missing"
    by_id = {
        str(item.get("worker_id")): item
        for item in details
        if isinstance(item, Mapping) and item.get("worker_id")
    }
    if set(by_id) != set(WORKER_IDS):
        return "worker evidence does not cover the exact worker set"
    for worker_id in WORKER_IDS:
        item = by_id[worker_id]
        revision = item.get("model_revision")
        cases = item.get("cases")
        baseline = item.get("baseline_vram_mb")
        post_exit = item.get("post_exit_vram_mb")
        if not isinstance(revision, str) or not _COMMIT_RE.fullmatch(revision):
            return f"{worker_id} has no immutable model revision"
        if not isinstance(cases, Mapping) or set(cases) != set(WORKER_CASES):
            return f"{worker_id} does not cover the exact contract case set"
        if any(cases[case] != "passed" for case in WORKER_CASES):
            return f"{worker_id} has a non-passing contract case"
        if not _finite_nonnegative(baseline) or not _finite_nonnegative(post_exit):
            return f"{worker_id} has invalid VRAM release observations"
        assert isinstance(baseline, (int, float)) and isinstance(post_exit, (int, float))
        if float(post_exit) > float(baseline) + 128:
            return f"{worker_id} retained more than 128 MiB after process exit"
    return None


def _language_model_reason(value: object) -> str | None:
    assert isinstance(value, Mapping)
    languages = value.get("languages")
    if not isinstance(languages, Mapping) or set(languages) != {"zh", "ja", "en"}:
        return "real-model evidence must cover exactly zh, ja, and en"
    for language in ("zh", "ja", "en"):
        item = languages[language]
        if not isinstance(item, Mapping):
            return f"{language} real-model evidence is invalid"
        revision = item.get("model_revision")
        if (
            not isinstance(item.get("model_id"), str)
            or not item["model_id"]
            or not isinstance(revision, str)
            or not _COMMIT_RE.fullmatch(revision)
        ):
            return f"{language} real-model identity is incomplete"
    return None


def _finite_nonnegative(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    number = float(value)
    return number >= 0 and number == number and number != float("inf")
