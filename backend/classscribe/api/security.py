"""Authorization-token and loopback enforcement for the local ASGI app."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import status
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from classscribe.errors import ErrorCode
from classscribe.security import TokenStore, is_loopback_host


def _is_allowed_host_header(value: str) -> bool:
    """Reject non-loopback and malformed Host values, including DNS rebinding hosts."""

    if not value or value.endswith(":") or any(character.isspace() for character in value):
        return False
    if any(character in value for character in "/?#@"):
        return False
    try:
        parsed = urlsplit(f"//{value}")
        _ = parsed.port  # Validate a supplied port without requiring the configured port here.
    except ValueError:
        return False
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


class LocalSecurityMiddleware:
    """Pure ASGI middleware, avoiding request-body buffering and cookie auth."""

    def __init__(
        self, app: ASGIApp, *, api_token: str | None, csrf_token: str | None = None
    ) -> None:
        self.app = app
        self.api_token = api_token
        self.csrf_token = csrf_token or api_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_host = client[0] if client else ""
        if not is_loopback_host(client_host):
            await self._error(
                status.HTTP_403_FORBIDDEN,
                ErrorCode.NON_LOOPBACK_CLIENT,
                "ClassScribe accepts loopback clients only",
            )(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not _is_allowed_host_header(headers.get("host", "")):
            await self._error(
                status.HTTP_403_FORBIDDEN,
                ErrorCode.NON_LOOPBACK_CLIENT,
                "ClassScribe accepts loopback Host headers only",
            )(scope, receive, send)
            return
        if scope["path"].startswith("/api/"):
            authorization = headers.get("authorization", "")
            scheme, _, supplied = authorization.partition(" ")
            if scheme.lower() != "bearer" or not supplied:
                await self._error(
                    status.HTTP_401_UNAUTHORIZED,
                    ErrorCode.AUTH_REQUIRED,
                    "a bearer token is required",
                )(scope, receive, send)
                return
            if self.api_token is None or not TokenStore.verify(self.api_token, supplied):
                await self._error(
                    status.HTTP_403_FORBIDDEN,
                    ErrorCode.AUTH_INVALID,
                    "the bearer token is invalid",
                )(scope, receive, send)
                return
            if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
                csrf = headers.get("x-classscribe-csrf-token", "")
                if self.csrf_token is None or not TokenStore.verify(self.csrf_token, csrf):
                    await self._error(
                        status.HTTP_403_FORBIDDEN,
                        ErrorCode.AUTH_INVALID,
                        "a valid X-ClassScribe-CSRF-Token header is required for writes",
                    )(scope, receive, send)
                    return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)

    @staticmethod
    def _error(http_status: int, code: ErrorCode, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=http_status,
            content={"error": {"code": code.value, "detail": detail}},
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )
