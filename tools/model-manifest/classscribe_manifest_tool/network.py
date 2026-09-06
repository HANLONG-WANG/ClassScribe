"""Credential-safe HTTP boundary for Hugging Face release operations."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from classscribe_manifest_tool.credentials import (
    assert_hf_token_absent,
    authorization_headers,
    redact_for_diagnostics,
)


class RemoteRequestError(RuntimeError):
    """A sanitized upstream request failure suitable for diagnostics."""


class HuggingFaceHttpClient:
    """Small JSON client that never places credentials in URLs or errors."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        )

    def __enter__(self) -> HuggingFaceHttpClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._client.close()

    def get_json(self, url: str, *, token: str | None) -> Any:
        """GET one HTTPS JSON resource with sanitized failure semantics."""
        assert_hf_token_absent(url, token=token, destination="request URL")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Hugging Face request URL must be credential-free HTTPS")
        try:
            response = self._client.get(url, headers=authorization_headers(token))
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            diagnostic = redact_for_diagnostics(error, token=token)
            raise RemoteRequestError(f"Hugging Face request failed: {diagnostic}") from None

