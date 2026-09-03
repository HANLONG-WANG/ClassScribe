"""Explicit, revision-pinned Hugging Face model downloads."""

from __future__ import annotations

import os
import ssl
from collections.abc import Iterable
from http.client import HTTPMessage
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models.manager import DownloadReceipt, ManifestFile

_REPOSITORY_PART = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_CHUNK_SIZE = 1024 * 1024


def _allowed_download_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    normalized = hostname.rstrip(".").lower()
    return normalized == "huggingface.co" or normalized.endswith((".huggingface.co", ".hf.co"))


class _PinnedRedirectHandler(HTTPRedirectHandler):
    """Reject off-provider redirects and never forward a bearer token cross-host."""

    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        target = urlsplit(newurl)
        if target.scheme != "https" or not _allowed_download_host(target.hostname):
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED,
                "model download redirected outside the approved HTTPS provider",
            )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        if urlsplit(req.full_url).hostname != target.hostname:
            redirected.remove_header("Authorization")
        return redirected


class HuggingFaceDownloader:
    """Download only manifest-listed files from one full immutable revision."""

    def __init__(self, *, token: str | None = None, timeout_seconds: float = 120.0) -> None:
        if token is not None and (not token or any(item in token for item in "\r\n\0")):
            raise ValueError("download token is malformed")
        if timeout_seconds <= 0:
            raise ValueError("download timeout must be positive")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(_PinnedRedirectHandler())

    def download(
        self,
        *,
        repository: str,
        revision: str,
        destination: Path,
        files: Iterable[ManifestFile],
    ) -> DownloadReceipt:
        owner, name = _repository_parts(repository)
        if len(revision) != 40 or any(
            character not in "0123456789abcdef" for character in revision
        ):
            raise ClassScribeError(
                ErrorCode.MODEL_REVISION_NOT_PINNED,
                "download requires a full lowercase commit revision",
            )
        if destination.is_symlink() or not destination.is_dir():
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED, "download destination must be a real directory"
            )
        downloaded = 0
        for spec in files:
            target = destination.joinpath(*Path(spec.path).parts)
            if any(parent.is_symlink() for parent in _parents_below(target.parent, destination)):
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED, "manifest target traverses a symlink"
                )
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.parent.chmod(0o700)
            url = (
                f"https://huggingface.co/{quote(owner)}/{quote(name)}/resolve/"
                f"{revision}/{quote(spec.path, safe='/')}"
            )
            request = Request(url, headers={"User-Agent": "ClassScribe/0.1 model-installer"})
            if self._token is not None:
                request.add_unredirected_header("Authorization", f"Bearer {self._token}")
            try:
                response = self._opener.open(request, timeout=self._timeout_seconds)
                with response, target.open("xb") as output:
                    os.chmod(target, 0o600)
                    file_bytes = _copy_bounded(response, output, maximum=spec.size_bytes)
            except ClassScribeError:
                target.unlink(missing_ok=True)
                raise
            except (HTTPError, URLError, OSError) as exc:
                target.unlink(missing_ok=True)
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED,
                    f"pinned model file download failed for {spec.path}: {type(exc).__name__}",
                ) from exc
            if file_bytes != spec.size_bytes:
                target.unlink(missing_ok=True)
                raise ClassScribeError(
                    ErrorCode.MODEL_INTEGRITY_FAILED,
                    f"downloaded size differs from manifest for {spec.path}",
                )
            downloaded += file_bytes
        return DownloadReceipt(
            resolved_revision=revision,
            source=f"https://huggingface.co/{owner}/{name}@{revision}",
            downloaded_bytes=downloaded,
        )


def _repository_parts(repository: str) -> tuple[str, str]:
    parts = repository.split("/")
    if len(parts) != 2 or any(
        not part or len(part) > 96 or any(character not in _REPOSITORY_PART for character in part)
        for part in parts
    ):
        raise ClassScribeError(
            ErrorCode.MODEL_INTEGRITY_FAILED,
            "Hugging Face repository must be a safe owner/name identifier",
        )
    return parts[0], parts[1]


def _parents_below(path: Path, root: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    current = path
    while current != root:
        if not current.is_relative_to(root):
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED, "manifest target escaped download root"
            )
        result.append(current)
        current = current.parent
    return tuple(result)


def _copy_bounded(source: BinaryIO, target: BinaryIO, *, maximum: int) -> int:
    total = 0
    while True:
        chunk = source.read(min(_CHUNK_SIZE, maximum - total + 1))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise TypeError("download response returned non-byte content")
        total += len(chunk)
        if total > maximum:
            raise ClassScribeError(
                ErrorCode.MODEL_INTEGRITY_FAILED, "download exceeded the manifest byte limit"
            )
        target.write(chunk)
    return total


def default_tls_context() -> ssl.SSLContext:
    """Expose the platform trust-store contract for diagnostics and tests."""

    return ssl.create_default_context()
