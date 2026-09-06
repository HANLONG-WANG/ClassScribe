from __future__ import annotations

import hashlib
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import HuggingFaceDownloader, ManifestFile
from classscribe.models.download import _PinnedRedirectHandler


class Response(BytesIO):
    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class Opener:
    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = bodies
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: float) -> Response:
        assert timeout == 3
        self.requests.append(request)
        return Response(self.bodies.pop(0))


def manifest_file(path: str, content: bytes) -> ManifestFile:
    return ManifestFile(path, hashlib.sha256(content).hexdigest(), len(content))


def test_downloader_fetches_only_manifest_paths_at_pinned_revision(tmp_path: Path) -> None:
    weights, config = b"weights", b"{}"
    downloader = HuggingFaceDownloader(token="private", timeout_seconds=3)
    opener = Opener([weights, config])
    downloader._opener = opener  # type: ignore[assignment]
    receipt = downloader.download(
        repository="Owner/Model",
        revision="a" * 40,
        destination=tmp_path,
        files=(
            manifest_file("weights/model.bin", weights),
            manifest_file("config.json", config),
        ),
    )
    assert (tmp_path / "weights/model.bin").read_bytes() == weights
    assert receipt.downloaded_bytes == len(weights) + len(config)
    assert receipt.source == f"https://huggingface.co/Owner/Model@{'a' * 40}"
    assert opener.requests[0].full_url.endswith(f"/{'a' * 40}/weights/model.bin")
    assert opener.requests[0].unredirected_hdrs["Authorization"] == "Bearer private"


def test_downloader_rejects_oversize_and_unsafe_repository(tmp_path: Path) -> None:
    downloader = HuggingFaceDownloader(timeout_seconds=3)
    downloader._opener = Opener([b"too large"])  # type: ignore[assignment]
    with pytest.raises(ClassScribeError) as oversized:
        downloader.download(
            repository="Owner/Model",
            revision="b" * 40,
            destination=tmp_path,
            files=(manifest_file("model.bin", b"tiny"),),
        )
    assert oversized.value.code is ErrorCode.MODEL_INTEGRITY_FAILED
    assert not (tmp_path / "model.bin").exists()
    with pytest.raises(ClassScribeError):
        downloader.download(
            repository="https://example.invalid/model",
            revision="b" * 40,
            destination=tmp_path,
            files=(manifest_file("model.bin", b"tiny"),),
        )


def test_downloader_rejects_symlinked_manifest_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "payload"
    destination.mkdir()
    (destination / "weights").symlink_to(outside, target_is_directory=True)
    downloader = HuggingFaceDownloader(timeout_seconds=3)
    opener = Opener([b"model"])
    downloader._opener = opener  # type: ignore[assignment]

    with pytest.raises(ClassScribeError) as raised:
        downloader.download(
            repository="Owner/Model",
            revision="b" * 40,
            destination=destination,
            files=(manifest_file("weights/model.bin", b"model"),),
        )

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILED
    assert not opener.requests
    assert not list(outside.iterdir())


def test_redirect_handler_rejects_off_provider_and_strips_cross_host_auth() -> None:
    handler = _PinnedRedirectHandler()
    request = Request("https://huggingface.co/Owner/Model/resolve/revision/model.bin")
    request.add_header("Authorization", "Bearer private")
    headers = HTTPMessage()
    with pytest.raises(ClassScribeError):
        handler.redirect_request(
            request,
            BytesIO(),
            302,
            "Found",
            headers,
            "https://attacker.invalid/model.bin",
        )
    redirected = handler.redirect_request(
        request,
        BytesIO(),
        302,
        "Found",
        headers,
        "https://cas-bridge.xethub.hf.co/object",
    )
    assert redirected is not None
    all_headers: dict[str, Any] = dict(redirected.header_items())
    assert "Authorization" not in all_headers
