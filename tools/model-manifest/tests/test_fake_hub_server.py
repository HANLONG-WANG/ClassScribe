from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from huggingface_hub import HfApi

from classscribe_manifest_tool.discovery import (
    DiscoveredFile,
    GatedRepositoryAccessError,
    RevisionResolutionError,
    discover_repository_tree,
)
from classscribe_manifest_tool.generator import (
    download_selected_snapshot,
    hash_payload_files,
    scan_payload,
    validate_payload_matches_selection,
)
from classscribe_manifest_tool.selection import ModelFileSelection

REVISION = "a" * 40
TOKEN = "hf_local_server_secret"


@dataclass
class FakeHubState:
    resolved_revision: str = REVISION
    gated: bool = False
    requests: list[tuple[str, str | None, str]] = field(default_factory=list)
    files: dict[str, bytes] = field(
        default_factory=lambda: {
            "cross.bin": b"cross-host lfs bytes",
            "lfs.bin": b"same-host lfs bytes",
            "long.bin": b"too-long",
            "short.bin": b"few",
        }
    )


@dataclass(frozen=True)
class FakeHubServer:
    origin: str
    cross_origin: str
    state: FakeHubState

    def downloader(self) -> Callable[..., object]:
        origin = self.origin

        def download(repo_id: str, **kwargs: Any) -> str:
            local_dir = Path(kwargs["local_dir"])
            revision = kwargs["revision"]
            headers = kwargs["headers"]
            with httpx.Client(follow_redirects=True, trust_env=False) as client:
                for path in kwargs["allow_patterns"]:
                    response = client.get(
                        f"{origin}/{repo_id}/resolve/{revision}/{path}",
                        headers=headers,
                    )
                    response.raise_for_status()
                    destination = local_dir / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(response.content)
            return str(local_dir)

        return download


@pytest.fixture
def fake_hub_server() -> Iterator[FakeHubServer]:
    state = FakeHubState()
    origins: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            state.requests.append(
                (self.path, self.headers.get("Authorization"), self.headers.get("Host", ""))
            )
            path = urlsplit(self.path).path
            if path.startswith("/api/models/owner/model/revision/"):
                if state.gated:
                    self._respond(
                        403,
                        b"gated",
                        {
                            "Content-Type": "text/plain",
                            "X-Error-Code": "GatedRepo",
                            "X-Error-Message": "Access to model owner/model is restricted",
                        },
                    )
                    return
                self._json(
                    {
                        "id": "owner/model",
                        "modelId": "owner/model",
                        "sha": state.resolved_revision,
                        "siblings": [],
                    }
                )
                return
            if path.startswith("/api/models/owner/model/tree/"):
                self._json(
                    [
                        {
                            "type": "file",
                            "path": "config.json",
                            "size": 6,
                            "oid": "git-config-oid",
                        }
                    ]
                )
                return
            if "/resolve/" in path:
                filename = path.rsplit("/", 1)[-1]
                if filename == "lfs.bin":
                    self._redirect(f"{origins['origin']}/storage/lfs.bin")
                    return
                if filename == "cross.bin":
                    self._redirect(f"{origins['cross']}/storage/cross.bin")
                    return
                self._file(filename)
                return
            if path.startswith("/storage/"):
                self._file(path.rsplit("/", 1)[-1])
                return
            self._respond(404, b"missing", {"Content-Type": "text/plain"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, value: object) -> None:
            self._respond(
                200,
                json.dumps(value, sort_keys=True).encode(),
                {"Content-Type": "application/json"},
            )

        def _redirect(self, location: str) -> None:
            self._respond(302, b"", {"Location": location})

        def _file(self, filename: str) -> None:
            content = state.files.get(filename)
            if content is None:
                self._respond(404, b"missing", {"Content-Type": "text/plain"})
                return
            self._respond(200, content, {"Content-Type": "application/octet-stream"})

        def _respond(
            self, status: int, content: bytes, headers: Mapping[str, str]
        ) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if content:
                self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    origins["origin"] = f"http://127.0.0.1:{port}"
    origins["cross"] = f"http://localhost:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield FakeHubServer(origins["origin"], origins["cross"], state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _api(server: FakeHubServer) -> HfApi:
    return HfApi(
        endpoint=server.origin,
        token=False,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def _selection(path: str) -> ModelFileSelection:
    return ModelFileSelection(include=(path,), kinds={path: "model"}, exclude=())


def _download(server: FakeHubServer, tmp_path: Path, path: str) -> Path:
    workspace = tmp_path / f"workspace-{path}"
    workspace.mkdir(mode=0o700)
    return download_selected_snapshot(
        "owner/model",
        REVISION,
        _selection(path),
        workspace,
        token=TOKEN,
        downloader=server.downloader(),
    )


def test_fake_hub_resolves_and_lists_only_the_fixed_revision(
    fake_hub_server: FakeHubServer,
) -> None:
    discovery = discover_repository_tree(
        "owner/model", REVISION, token=TOKEN, api=_api(fake_hub_server)
    )

    assert discovery.revision == REVISION
    assert [(item.path, item.size) for item in discovery.files] == [("config.json", 6)]
    api_paths = [path for path, _authorization, _host in fake_hub_server.state.requests]
    assert f"/api/models/owner/model/revision/{REVISION}" in api_paths
    assert any(path.startswith(f"/api/models/owner/model/tree/{REVISION}") for path in api_paths)


def test_fake_hub_revision_drift_is_rejected(fake_hub_server: FakeHubServer) -> None:
    fake_hub_server.state.resolved_revision = "b" * 40

    with pytest.raises(RevisionResolutionError, match="did not resolve"):
        discover_repository_tree(
            "owner/model", REVISION, token=TOKEN, api=_api(fake_hub_server)
        )


def test_fake_hub_gated_403_is_rejected_without_token_leak(
    fake_hub_server: FakeHubServer,
) -> None:
    fake_hub_server.state.gated = True

    with pytest.raises(GatedRepositoryAccessError) as failure:
        discover_repository_tree(
            "owner/model", REVISION, token=TOKEN, api=_api(fake_hub_server)
        )

    assert TOKEN not in str(failure.value)


def test_fake_hub_lfs_redirect_preserves_auth_on_same_host(
    fake_hub_server: FakeHubServer, tmp_path: Path
) -> None:
    payload = _download(fake_hub_server, tmp_path, "lfs.bin")

    assert (payload / "lfs.bin").read_bytes() == fake_hub_server.state.files["lfs.bin"]
    storage_request = next(
        item for item in fake_hub_server.state.requests if item[0] == "/storage/lfs.bin"
    )
    assert storage_request[1] == f"Bearer {TOKEN}"


def test_fake_hub_cross_host_redirect_strips_authorization(
    fake_hub_server: FakeHubServer, tmp_path: Path
) -> None:
    payload = _download(fake_hub_server, tmp_path, "cross.bin")

    assert (payload / "cross.bin").read_bytes() == fake_hub_server.state.files["cross.bin"]
    storage_request = next(
        item for item in fake_hub_server.state.requests if item[0] == "/storage/cross.bin"
    )
    assert storage_request[2].startswith("localhost:")
    assert storage_request[1] is None


def test_fake_hub_short_write_fails_discovery_size_contract(
    fake_hub_server: FakeHubServer, tmp_path: Path
) -> None:
    payload = _download(fake_hub_server, tmp_path, "short.bin")
    selection = _selection("short.bin")
    scanned = scan_payload(payload, expected_sizes={"short.bin": 5})
    hashed = hash_payload_files(payload, scanned)
    discovered = (
        DiscoveredFile("short.bin", 5, "lfs", "pointer", "f" * 64, None),
    )

    with pytest.raises(ValueError, match="sizes differ from discovery"):
        validate_payload_matches_selection(selection, discovered, hashed)


def test_fake_hub_overlong_payload_is_rejected_before_hashing(
    fake_hub_server: FakeHubServer, tmp_path: Path
) -> None:
    payload = _download(fake_hub_server, tmp_path, "long.bin")

    with pytest.raises(ValueError, match="exceeds declared size"):
        scan_payload(payload, expected_sizes={"long.bin": 5})
