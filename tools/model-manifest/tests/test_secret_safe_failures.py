from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from classscribe_manifest_tool.network import HuggingFaceHttpClient, RemoteRequestError
from classscribe_manifest_tool.workspace import private_temporary_workspace


def test_authenticated_fixture_success_does_not_expose_token() -> None:
    token = "hf_success_secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {token}"
        return httpx.Response(200, json={"siblings": ["config.json"]})

    with HuggingFaceHttpClient(transport=httpx.MockTransport(handler)) as client:
        result = client.get_json("https://huggingface.test/api/models/owner/model", token=token)

    assert result == {"siblings": ["config.json"]}
    assert token not in repr(result)


def test_403_fixture_failure_does_not_expose_token() -> None:
    token = "hf_forbidden_secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"denied {token}", request=request)

    with (
        HuggingFaceHttpClient(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RemoteRequestError) as failure,
    ):
        client.get_json("https://huggingface.test/api/models/gated/model", token=token)

    assert token not in str(failure.value)


def test_cross_host_redirect_fixture_drops_authorization_header() -> None:
    token = "hf_redirect_secret"
    seen_destination_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "huggingface.test":
            assert request.headers["Authorization"] == f"Bearer {token}"
            return httpx.Response(
                302,
                headers={"Location": "https://cdn.test/tree.json"},
                request=request,
            )
        seen_destination_headers.append(request.headers)
        return httpx.Response(200, json={"siblings": []}, request=request)

    with HuggingFaceHttpClient(transport=httpx.MockTransport(handler)) as client:
        result = client.get_json("https://huggingface.test/api/models/owner/model", token=token)

    assert result == {"siblings": []}
    assert len(seen_destination_headers) == 1
    assert "Authorization" not in seen_destination_headers[0]


def test_transport_exception_fixture_does_not_expose_token() -> None:
    token = "hf_exception_secret"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed using {token}", request=request)

    with (
        HuggingFaceHttpClient(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RemoteRequestError) as failure,
    ):
        client.get_json("https://huggingface.test/api/models/owner/model", token=token)

    assert token not in str(failure.value)


def test_operation_exception_fixture_does_not_expose_token(tmp_path: Path) -> None:
    token = "hf_operation_secret"

    with (
        pytest.raises(RuntimeError) as failure,
        private_temporary_workspace(token=token, parent=tmp_path),
    ):
        raise RuntimeError(f"operation included {token}")

    assert token not in str(failure.value)
    assert list(tmp_path.iterdir()) == []


def test_cleanup_failure_fixture_does_not_expose_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "hf_cleanup_secret"

    def fail_cleanup(_path: Path) -> None:
        raise OSError(f"cleanup included {token}")

    monkeypatch.setattr("classscribe_manifest_tool.workspace.shutil.rmtree", fail_cleanup)
    with (
        pytest.raises(RuntimeError) as failure,
        private_temporary_workspace(token=token, parent=tmp_path) as workspace,
    ):
        assert token not in str(workspace)

    assert token not in str(failure.value)
