"""Durable online attempts and the policy for user-authorized retries."""
# ruff: noqa: RUF001

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from classscribe.db.models import OnlineRequestAttempt
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import AppPaths


def latest_attempt(session: Session, job_id: str) -> OnlineRequestAttempt | None:
    return session.scalar(
        select(OnlineRequestAttempt)
        .where(OnlineRequestAttempt.job_id == job_id)
        .order_by(OnlineRequestAttempt.attempt_number.desc())
        .limit(1)
    )


def response_file(paths: AppPaths, job_id: str, number: int) -> Path:
    # Retain the original location for pre-migration jobs and their first attempt.
    parts: tuple[str, ...] = ("jobs", job_id, "online")
    if number > 1:
        parts += ("attempts", str(number))
    return paths.data_path(*parts, "response.raw.json")


def attempt_diagnostics(paths: AppPaths, attempt: OnlineRequestAttempt) -> dict[str, Any]:
    if attempt.diagnostics_json:
        return dict(attempt.diagnostics_json)
    if attempt.status != "failed":
        return {}
    path = response_file(paths, attempt.job_id, attempt.attempt_number).with_name("error.json")
    if path.is_symlink():
        return {}
    try:
        with path.open("rb") as source:
            content = source.read(512 * 1024 + 1)
        if len(content) <= 512 * 1024:
            diagnostics = json.loads(content)
            if isinstance(diagnostics, dict):
                return diagnostics
    except (OSError, ValueError):
        pass
    return {}


def retry_policy(paths: AppPaths, attempt: OnlineRequestAttempt) -> dict[str, Any]:
    saved = response_file(paths, attempt.job_id, attempt.attempt_number).is_file()
    recoverable = saved and attempt.status in {"sending", "uncertain", "responded", "imported"}
    resend = not recoverable and attempt.status != "prepared"
    diagnostics = attempt_diagnostics(paths, attempt)
    retry_at = None
    status = diagnostics.get("http_status")
    if resend and isinstance(status, int) and (status == 429 or status >= 500):
        seconds = diagnostics.get("retry_after_seconds")
        if not isinstance(seconds, int) or seconds < 0:
            seconds = min(60, 5 * 2 ** min(attempt.attempt_number - 1, 4))
        retry_at = attempt.updated_at.replace(tzinfo=UTC) + timedelta(seconds=seconds)
    remaining = max(0, math.ceil((retry_at - datetime.now(UTC)).total_seconds())) if retry_at else 0
    return {
        "attempt_id": attempt.id,
        "requires_confirmation": resend,
        "result_uncertain": resend
        and attempt.status in {"sending", "uncertain", "responded", "imported"},
        "retry_at": retry_at.isoformat() if retry_at else None,
        "retry_after_seconds": remaining,
        "recovers_saved_response": recoverable,
    }


def authorize_retry(
    session: Session,
    paths: AppPaths,
    job_id: str,
    *,
    confirm_resend: bool,
    expected_attempt_id: str | None,
) -> None:
    """Called under the job-control write lock, never by recovery or dispatch."""
    attempt = latest_attempt(session, job_id)
    if attempt is None:
        return
    policy = retry_policy(paths, attempt)
    if expected_attempt_id is not None and expected_attempt_id != attempt.id:
        raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "请求记录已变化，请刷新任务后重试。")
    if not policy["requires_confirmation"]:
        return
    if not confirm_resend or expected_attempt_id != attempt.id:
        raise ClassScribeError(
            ErrorCode.JOB_STATE_CONFLICT,
            "重新发送 MAI 请求可能再次计费，请确认当前请求记录后重试。",
        )
    if policy["retry_after_seconds"]:
        raise ClassScribeError(
            ErrorCode.JOB_STATE_CONFLICT,
            f"上游暂时不可用或限流，请等待 {policy['retry_after_seconds']} 秒后重试。",
        )
    # Preserve legacy diagnostics before authorizing a new, separate send intent.
    attempt.diagnostics_json = attempt_diagnostics(paths, attempt)
    session.add(
        OnlineRequestAttempt(
            job_id=job_id,
            attempt_number=attempt.attempt_number + 1,
            request_fingerprint=attempt.request_fingerprint,
            audio_sha256=attempt.audio_sha256,
            status="prepared",
        )
    )
