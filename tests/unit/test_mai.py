from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from classscribe.classroom.mai import (
    MaiClient,
    MaiCredentials,
    normalize_endpoint,
    parse_mai_response,
    request_definition,
)
from classscribe.errors import ClassScribeError


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://eastus.api.cognitive.microsoft.com",
        "https://evil.example",
        "https://a.cognitiveservices.azure.com@evil.example",
        "https://a.cognitiveservices.azure.com/?key=secret",
    ],
)
def test_rejects_non_speech_endpoint(endpoint: str) -> None:
    with pytest.raises(ClassScribeError):
        normalize_endpoint(endpoint)


def test_credentials_status_never_contains_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    credentials = MaiCredentials()
    credentials.configure("https://eastus.api.cognitive.microsoft.com", "secret-marker")
    assert credentials.status()["configured"]
    assert "secret-marker" not in json.dumps(credentials.status())
    credentials.clear()
    assert not credentials.status()["configured"]


def test_credentials_persist_replace_and_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    path = tmp_path / "config" / "azure-mai.env"
    endpoint = "https://eastus.api.cognitive.microsoft.com"
    credentials = MaiCredentials(path)
    credentials.configure(endpoint, "first-test-key")
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    restarted = MaiCredentials(path)
    assert restarted.get() == (normalize_endpoint(endpoint), "first-test-key")
    credentials.configure(endpoint, "replacement-test-key")
    assert restarted.get()[1] == "replacement-test-key"
    assert "first-test-key" not in path.read_text()
    with pytest.raises(ClassScribeError):
        credentials.configure(endpoint, "invalid key")
    assert restarted.get()[1] == "replacement-test-key"
    assert "replacement-test-key" not in json.dumps(restarted.status())
    restarted.clear()
    assert not path.exists()
    assert not credentials.status()["configured"]
    assert not MaiCredentials(path).status()["configured"]


def test_cleared_credentials_keep_environment_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = "https://eastus.api.cognitive.microsoft.com"
    monkeypatch.setenv("AZURE_SPEECH_ENDPOINT", endpoint)
    monkeypatch.setenv("AZURE_SPEECH_KEY", "environment-test-key")
    credentials = MaiCredentials(tmp_path / "azure-mai.env")
    assert credentials.get()[1] == "environment-test-key"
    credentials.configure(endpoint, "saved-test-key")
    assert credentials.get()[1] == "saved-test-key"
    credentials.clear()
    assert credentials.get()[1] == "environment-test-key"


def test_credential_file_errors_are_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    endpoint = "https://eastus.api.cognitive.microsoft.com"
    path = tmp_path / "azure-mai.env"
    credentials = MaiCredentials(path)
    credentials.configure(endpoint, "private-test-marker")
    path.chmod(0o644)
    with pytest.raises(ClassScribeError, match="无法读取本地 MAI 凭据") as error:
        credentials.get()
    assert "private-test-marker" not in str(error.value)
    assert not credentials.status()["configured"]
    credentials.configure(endpoint, "replacement-test-marker")
    assert path.stat().st_mode & 0o777 == 0o600
    assert credentials.get()[1] == "replacement-test-marker"
    path.unlink()
    path.mkdir()
    with pytest.raises(ClassScribeError, match="无法保存本地 MAI 凭据") as error:
        credentials.configure(endpoint, "private-test-marker")
    assert "private-test-marker" not in str(error.value)


def test_definition_is_explicit_mai_with_verbatim_words() -> None:
    definition = request_definition({"language": "ja", "speaker_count": "1"}, ["GPU", "GPU"])
    assert definition["locales"] == ["ja"]
    assert definition["enhancedMode"]["model"] == "MAI-Transcribe-2"
    assert definition["enhancedMode"]["modelOptions"] == {
        "timestamps": "word",
        "transcribeStyle": "verbatim",
    }
    assert definition["phraseList"] == {"phrases": ["GPU"]}
    assert definition["diarization"] == {"enabled": False}
    assert "locales" not in request_definition({"language": "auto_mixed"}, [])


def test_partial_word_timing_never_drops_phrase_text() -> None:
    phrase = {
        "text": "今日は晴れです。",
        "offsetMilliseconds": 123,
        "durationMilliseconds": 900,
        "speaker": 0,
        "words": [
            {"text": "今日は", "offsetMilliseconds": 123, "durationMilliseconds": 200},
            {"text": "晴れです。"},
        ],
    }
    result = parse_mai_response(json.dumps({"phrases": [phrase]}).encode(), 16000, "ja")
    assert result[0].text == "今日は晴れです。"
    assert result[0].start == 1968
    assert result[0].end == 16000
    assert result[0].speaker == "0"
    assert not result[0].words


def test_complete_word_evidence_splits_without_rebuilding_text() -> None:
    phrase = {
        "text": "Hello world.  Next sentence!",
        "words": [
            {"text": text, "offsetMilliseconds": start, "durationMilliseconds": 100}
            for text, start in [("Hello", 0), ("world.", 200), ("Next", 400), ("sentence!", 600)]
        ],
    }
    result = parse_mai_response(json.dumps({"phrases": [phrase]}).encode(), 16000, "en")
    assert [item.text for item in result] == ["Hello world.", "Next sentence!"]
    assert [(item.start, item.end) for item in result] == [(0, 4800), (6400, 11200)]
    assert all(item.timed for item in result)


def test_untimed_text_exports_but_not_as_subtitles() -> None:
    from classscribe.exports import ExportFormat, ExportLayer, ExportSegment, render_export
    from classscribe.timeline import AudioSpan

    segment = ExportSegment(
        "s",
        AudioSpan(0, 0),
        "auto_mixed",
        "全文",
        "全文",
        "全文",
        None,
        (),
        timing_quality="invalid",
    )
    assert "全文" in render_export(
        (segment,), output_format=ExportFormat.JSON, layer=ExportLayer.FAITHFUL
    )
    assert (
        render_export((segment,), output_format=ExportFormat.SRT, layer=ExportLayer.FAITHFUL) == ""
    )


def test_missing_and_invalid_timing_is_not_invented() -> None:
    result = parse_mai_response(
        json.dumps(
            {
                "phrases": [
                    {"text": "untimed", "offsetMilliseconds": True, "durationMilliseconds": 2}
                ]
            }
        ).encode(),
        16000,
        "en",
    )
    assert not result[0].timed
    assert result[0].start == result[0].end == 0
    assert (
        parse_mai_response(b'{"combinedPhrases":[{"text":"full text"}]}', 16000, "en")[0].text
        == "full text"
    )


def test_transport_streams_once_and_hides_error_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.classroom import mai

    audio = tmp_path / "private-filename.wav"
    audio.write_bytes(b"x" * 600000)
    connections: list[Any] = []

    class Connection:
        sock = None

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.sent: list[bytes] = []
            self.headers: dict[str, str] = {}
            connections.append(self)

        def connect(self) -> None:
            pass

        def putrequest(self, *args: Any) -> None:
            pass

        def putheader(self, name: str, value: str) -> None:
            self.headers[name] = value

        def endheaders(self) -> None:
            pass

        def send(self, value: bytes) -> None:
            self.sent.append(value)

        def getresponse(self) -> Any:
            return type(
                "Response",
                (),
                {
                    "status": 429,
                    "getheader": lambda *args: None,
                    "read": lambda *args: (
                        b'{"error":{"code":"TooManyRequests","message":"limit hit secret-marker"}}'
                    ),
                },
            )()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(mai, "getproxies", lambda: {})
    with pytest.raises(ClassScribeError, match="429") as error:
        MaiClient().transcribe(
            normalize_endpoint("https://eastus.api.cognitive.microsoft.com"),
            "secret-marker",
            audio,
            {},
            cancelled=lambda: False,
            progress=lambda *args: None,
        )
    assert len(connections) == 1
    assert max(map(len, connections[0].sent)) <= 256 * 1024
    assert b"private-filename" not in b"".join(connections[0].sent)
    assert "secret-marker" not in str(error.value)
    assert isinstance(error.value, mai.MaiHttpError)
    assert error.value.service_error_code == "TooManyRequests"
    assert error.value.service_error_message is not None
    assert "limit hit" in error.value.service_error_message


@pytest.mark.parametrize(
    "status,cancel",
    [(200, False), (302, False), (401, False), (429, False), (503, False), (200, True)],
)
def test_real_multipart_transport_is_single_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, cancel: bool
) -> None:
    from classscribe.classroom import mai

    bodies: list[bytes] = []
    cancelled = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
            if cancel:
                cancelled.set()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Location", "/must-not-follow")
            self.send_header("apim-request-id", "test-request")
            self.send_header("Retry-After", "30")
            self.end_headers()
            self.wfile.write(b'{"phrases":[]}' if status == 200 else b"secret-marker")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    monkeypatch.setattr(mai, "getproxies", lambda: {})
    monkeypatch.setattr(
        http.client,
        "HTTPSConnection",
        lambda *args, **kwargs: http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=2
        ),
    )
    audio = tmp_path / "private.flac"
    audio.write_bytes(b"fLaC-example")
    try:

        def call() -> tuple[bytes, str]:
            return MaiClient().transcribe(
                normalize_endpoint("https://eastus.api.cognitive.microsoft.com"),
                "secret-marker",
                audio,
                request_definition({"language": "ja"}, []),
                cancelled=cancelled.is_set,
                progress=lambda *args: None,
            )

        if status == 200 and not cancel:
            assert call() == (b'{"phrases":[]}', "test-request")
        else:
            with pytest.raises(ClassScribeError) as error:
                call()
            assert "secret-marker" not in str(error.value)
            if status == 503:
                assert isinstance(error.value, mai.MaiHttpError)
                assert error.value.request_id == "test-request"
                assert error.value.retry_after == 30
                assert "上游服务" in error.value.detail
        assert len(bodies) == 1
        assert b'filename="clip.flac"' in bodies[0]
        assert b'"model": "MAI-Transcribe-2"' in bodies[0]
        assert b"fLaC-example" in bodies[0]
        assert b"private.flac" not in bodies[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("prefix", ["", "MAI service returned an error: ServiceUnavailable - "])
def test_upstream_error_code_message_and_body_are_preserved_without_credentials(
    prefix: str,
) -> None:
    from classscribe.classroom.mai import MaiHttpError

    body = prefix + json.dumps(
        {
            "error": {
                "code": "diarization_unavailable",
                "message": "Speaker diarization service returned error code 400 private-key",
                "innerError": {"code": "InvalidParameter", "message": "diagnostic details"},
            },
            "apiKey": "another-private-key",
            "authorization": "Bearer third-private-key",
        }
    )
    error = MaiHttpError(503, "trace-id", "30", body=body, secrets=("private-key",))
    assert error.service_error_code == "diarization_unavailable"
    assert error.service_error_message is not None
    assert "error code 400" in error.service_error_message
    assert "diagnostic details" in error.response_body
    assert "private-key" not in json.dumps(error.diagnostics)
    assert "private-key" not in error.detail
    assert error.diagnostics["http_status"] == 503
    assert error.diagnostics["request_id"] == "trace-id"
    assert error.diagnostics["retry_after_seconds"] == 30


@pytest.mark.parametrize("failure", ["truncated", "timeout", "incomplete"])
def test_http_failure_keeps_status_and_marks_incomplete_diagnostic_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from classscribe.classroom import mai

    requested: list[int] = []

    class Response:
        status = 503

        def getheader(self, name: str) -> str | None:
            return "trace-id" if name == "apim-request-id" else None

        def read(self, amount: int) -> bytes:
            requested.append(amount)
            if failure == "timeout":
                raise TimeoutError("private-key")
            if failure == "incomplete":
                raise http.client.IncompleteRead(b"partial upstream error", 100)
            return b"x" * amount

    class Connection:
        sock = None

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def connect(self) -> None:
            pass

        def putrequest(self, *args: Any) -> None:
            pass

        def putheader(self, *args: Any) -> None:
            pass

        def endheaders(self) -> None:
            pass

        def send(self, *args: Any) -> None:
            pass

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(mai, "getproxies", lambda: {})
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    audio = tmp_path / "upload.flac"
    audio.write_bytes(b"fLaC-example")
    with pytest.raises(mai.MaiHttpError) as captured:
        MaiClient().transcribe(
            normalize_endpoint("https://eastus.api.cognitive.microsoft.com"),
            "private-key",
            audio,
            {},
            cancelled=lambda: False,
            progress=lambda *args: None,
        )
    error = captured.value
    assert requested == [mai.MAX_ERROR_BODY_BYTES + 1]
    assert error.status == 503
    assert error.request_id == "trace-id"
    assert "private-key" not in json.dumps(error.diagnostics)
    if failure == "truncated":
        assert error.diagnostics["response_truncated"] is True
        assert len(error.response_body) == mai.MAX_ERROR_BODY_BYTES
    else:
        assert error.diagnostics["response_read_error"] == (
            "TimeoutError" if failure == "timeout" else "IncompleteRead"
        )
        if failure == "incomplete":
            assert error.response_body == "partial upstream error"


def test_mai_size_limit_rejects_before_opening_a_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classscribe.classroom import mai

    audio = tmp_path / "too-large.flac"
    with audio.open("wb") as output:
        output.truncate(mai.MAX_BYTES + 1)
    monkeypatch.setattr(http.client, "HTTPSConnection", lambda *args, **kwargs: pytest.fail("sent"))
    with pytest.raises(ClassScribeError, match="240 MB"):
        MaiClient().transcribe(
            normalize_endpoint("https://eastus.api.cognitive.microsoft.com"),
            "key",
            audio,
            {},
            cancelled=lambda: False,
            progress=lambda *args: None,
        )
