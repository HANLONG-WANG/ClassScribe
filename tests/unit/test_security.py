from __future__ import annotations

import asyncio
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from classscribe.api.app import create_app
from classscribe.diagnostics import DiagnosticSnapshot
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import AppPaths, PathSecurityError
from classscribe.security import (
    RestrictedCredentialEnvironment,
    SecureFileLocator,
    TokenStore,
    UploadLimits,
    parse_uuid,
)
from classscribe.timeline import SAMPLE_RATE
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class ASGIResponse:
    status: int
    headers: dict[str, str]
    body: dict[str, Any]


async def asgi_request(
    app: ASGIApp,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    client_host: str = "127.0.0.1",
) -> ASGIResponse:
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in (headers or {}).items()
            ],
            "client": (client_host, 50_000),
            "server": ("127.0.0.1", 8765),
        },
    )
    request_sent = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await asyncio.wait_for(app(scope, cast(Receive, receive), cast(Send, send)), timeout=2)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body_bytes = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1") for key, value in start["headers"]
    }
    return ASGIResponse(start["status"], response_headers, json.loads(body_bytes or b"{}"))


def test_api_token_is_256_bit_stable_and_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "config" / "api-token"
    store = TokenStore(path)
    token = store.load_or_create()
    assert len(token) >= 43
    assert store.load_or_create() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert TokenStore.verify(token, token)
    assert not TokenStore.verify(token, token + "x")


def test_insecure_or_symlink_token_is_rejected(tmp_path: Path) -> None:
    token_path = tmp_path / "api-token"
    token_path.write_text("x" * 43, encoding="utf-8")
    token_path.chmod(0o644)
    with pytest.raises(PathSecurityError, match="0600"):
        TokenStore(token_path).load()
    target = tmp_path / "actual-token"
    target.write_text("x" * 43, encoding="utf-8")
    target.chmod(0o600)
    token_path.unlink()
    token_path.symlink_to(target)
    with pytest.raises(PathSecurityError, match="symlink"):
        TokenStore(token_path).load()


def test_download_credentials_use_separate_restricted_environment_file(tmp_path: Path) -> None:
    path = tmp_path / "model-download.env"
    store = RestrictedCredentialEnvironment(path)
    store.write({"HF_TOKEN": "hf_private_value", "MODEL_MIRROR_TOKEN": "private"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load() == {
        "HF_TOKEN": "hf_private_value",
        "MODEL_MIRROR_TOKEN": "private",
    }
    with pytest.raises(PathSecurityError):
        store.write({"bad-name": "value"})
    path.chmod(0o644)
    with pytest.raises(PathSecurityError, match="0600"):
        store.load()


def diagnostic_fixture() -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        generated_at="2026-09-03T00:00:00+00:00",
        system={"distribution": "Fedora", "kernel": "test"},
        gpu={"status": "unavailable"},
        components=(),
    )


def test_local_api_requires_bearer_token_for_all_api_routes() -> None:
    token = "s" * 43
    app = create_app(api_token=token, diagnostic_provider=diagnostic_fixture)

    async def scenario() -> None:
        health = await asgi_request(app, "GET", "/healthz")
        assert health.status == 200
        assert health.headers["cache-control"] == "no-store"

        missing = await asgi_request(app, "POST", "/api/v1/nonexistent")
        assert missing.status == 401
        assert missing.body["error"]["code"] == ErrorCode.AUTH_REQUIRED.value

        invalid = await asgi_request(
            app,
            "GET",
            "/api/v1/diagnostics",
            headers={"Authorization": "Bearer wrong"},
        )
        assert invalid.status == 403
        assert invalid.body["error"]["code"] == ErrorCode.AUTH_INVALID.value

        accepted = await asgi_request(
            app,
            "GET",
            "/api/v1/diagnostics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert accepted.status == 200
        assert accepted.body["system"]["distribution"] == "Fedora"

    asyncio.run(scenario())


def test_non_loopback_client_is_rejected_even_for_health() -> None:
    app = create_app(api_token="s" * 43, diagnostic_provider=diagnostic_fixture)

    async def scenario() -> None:
        response = await asgi_request(app, "GET", "/healthz", client_host="192.0.2.1")
        assert response.status == 403
        assert response.body["error"]["code"] == ErrorCode.NON_LOOPBACK_CLIENT.value

    asyncio.run(scenario())


def test_upload_limits_cover_size_duration_format_and_decode_resources() -> None:
    limits = UploadLimits(max_bytes=100)
    limits.validate(
        source_name="lecture.WAV",
        size_bytes=100,
        duration_samples=90 * 60 * SAMPLE_RATE,
        channels=1,
        sample_rate=48_000,
    )
    assert limits.ffmpeg_guard_args() == (
        "-nostdin",
        "-threads",
        "2",
        "-t",
        "5400",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
    )
    cases = (
        ({"size_bytes": 101}, ErrorCode.UPLOAD_TOO_LARGE),
        ({"duration_samples": 90 * 60 * SAMPLE_RATE + 1}, ErrorCode.AUDIO_TOO_LONG),
        ({"source_name": "archive.zip"}, ErrorCode.UNSUPPORTED_MEDIA),
        ({"channels": 9}, ErrorCode.DECODE_LIMIT_EXCEEDED),
        ({"sample_rate": 384_000}, ErrorCode.DECODE_LIMIT_EXCEEDED),
    )
    base = {
        "source_name": "lecture.wav",
        "size_bytes": 50,
        "duration_samples": SAMPLE_RATE,
        "channels": 1,
        "sample_rate": 16_000,
    }
    for override, code in cases:
        arguments: Any = base | override
        with pytest.raises(ClassScribeError) as raised:
            limits.validate(**arguments)
        assert raised.value.code is code


def test_uuid_storage_ignores_upload_name_and_worker_paths_are_allowlisted(tmp_path: Path) -> None:
    paths = AppPaths.from_environment({}, home=tmp_path)
    paths.ensure()
    locator = SecureFileLocator(paths)
    job_id, job_paths = locator.allocate_job()
    assert str(UUID(job_id)) == job_id
    upload = locator.source_upload_path(job_id)
    assert upload.parent == job_paths[1]
    assert upload.suffix == ".upload"
    upload.write_bytes(b"audio")
    assert locator.approve_worker_path(upload) == upload

    with pytest.raises(ClassScribeError) as outside:
        locator.approve_worker_path(Path("/etc/passwd"))
    assert outside.value.code is ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT

    symlink = paths.cache / "tmp" / "linked.upload"
    symlink.symlink_to(upload)
    with pytest.raises(ClassScribeError):
        locator.approve_worker_path(symlink)


@pytest.mark.parametrize(
    "value",
    ["not-a-uuid", "00000000000000000000000000000000", "/etc/passwd", "../escape"],
)
def test_illegal_file_ids_are_rejected(value: str) -> None:
    with pytest.raises(ClassScribeError) as raised:
        parse_uuid(value)
    assert raised.value.code is ErrorCode.INVALID_FILE_ID
