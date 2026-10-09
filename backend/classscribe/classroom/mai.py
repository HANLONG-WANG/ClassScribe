"""Explicit, single-attempt Azure MAI transport and lossless result adaptation."""
# ruff: noqa: RUF001

from __future__ import annotations

import base64
import http.client
import json
import math
import os
import re
import socket
import ssl
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import getproxies, proxy_bypass
from uuid import uuid4

from pydantic import ValidationError

from classscribe.audio.limits import MAI_MAX_UPLOAD_BYTES
from classscribe.classroom.mai_options import MaiTranscriptionOptions
from classscribe.errors import ClassScribeError, ErrorCode, public_error_detail
from classscribe.readability import lexical_positions, sentence_spans
from classscribe.security import RestrictedCredentialEnvironment

API_VERSION = "2025-10-15"
MODEL = "MAI-Transcribe-2"
MAX_BYTES = MAI_MAX_UPLOAD_BYTES
MAX_ERROR_BODY_BYTES = 64 * 1024


def mai_error(detail: str) -> ClassScribeError:
    return ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, detail)


def normalize_endpoint(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or not re.fullmatch(
            r"[a-z0-9-]+\.(?:api\.cognitive\.microsoft\.com|cognitiveservices\.azure\.com)", host
        )
        or parsed.path.rstrip("/") not in ("", "/speechtotext/transcriptions:transcribe")
    ):
        raise mai_error("请输入 Azure Speech HTTPS 资源地址。")
    return f"https://{host}/speechtotext/transcriptions:transcribe?api-version={API_VERSION}"


class MaiCredentials:
    """Private local credentials with optional persistence and environment fallback."""

    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._path = path
        self._value: tuple[str, str] | None = None

    def configure(self, endpoint: str, key: str) -> None:
        endpoint = normalize_endpoint(endpoint).split("?")[0]
        key = key.strip()
        if not key or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise mai_error("请输入有效的 Azure Speech Key。")
        with self._lock:
            if self._path is not None:
                try:
                    RestrictedCredentialEnvironment(self._path).write(
                        {
                            "AZURE_SPEECH_ENDPOINT": endpoint.split("/speechtotext")[0],
                            "AZURE_SPEECH_KEY": key,
                        }
                    )
                except (OSError, ValueError):
                    raise mai_error("无法保存本地 MAI 凭据，请检查配置目录权限。") from None
            self._value = endpoint, key

    def clear(self) -> None:
        with self._lock:
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    raise mai_error("无法清除本地 MAI 凭据，请检查配置目录权限。") from None
            self._value = None

    def get(self) -> tuple[str, str]:
        with self._lock:
            value = self._value
            if self._path is not None:
                try:
                    saved = RestrictedCredentialEnvironment(self._path).load()
                except FileNotFoundError:
                    value = None
                except (OSError, ValueError):
                    raise mai_error("无法读取本地 MAI 凭据，请重新保存凭据。") from None
                else:
                    value = (
                        saved.get("AZURE_SPEECH_ENDPOINT", ""),
                        saved.get("AZURE_SPEECH_KEY", ""),
                    )
        endpoint, key = (
            value
            if value is not None
            else (
                os.environ.get("AZURE_SPEECH_ENDPOINT", ""),
                os.environ.get("AZURE_SPEECH_KEY", "").strip(),
            )
        )
        if not endpoint or not key:
            raise mai_error("请先配置 Azure Speech Endpoint 和 Key。")
        endpoint = normalize_endpoint(endpoint)
        if any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise mai_error("Azure Speech Key 格式无效。")
        return endpoint, key

    def status(self) -> dict[str, Any]:
        try:
            endpoint, _ = self.get()
            return {"configured": True, "endpoint": endpoint.split("/speechtotext")[0]}
        except (ClassScribeError, ValueError):
            return {"configured": False, "endpoint": ""}


def request_definition(options: Mapping[str, Any], terms: list[str]) -> dict[str, Any]:
    raw_options = options.get("mai_options")
    legacy = raw_options is None
    if legacy:
        language = options.get("language")
        raw_options = {
            "diarization": options.get("speaker_count", "auto") != "1",
            "locale": language if language in {"ja", "zh", "en"} else None,
        }
    try:
        settings = MaiTranscriptionOptions.model_validate(raw_options)
    except ValidationError:
        raise mai_error("MAI 转写选项无效，请检查设置。") from None
    definition: dict[str, Any] = {
        "enhancedMode": {
            "enabled": True,
            "model": MODEL,
            "modelOptions": {
                "transcribeStyle": settings.transcribe_style,
                "timestamps": settings.timestamps,
            },
        },
        "diarization": {"enabled": settings.diarization},
    }
    if settings.locale is not None:
        definition["locales"] = [settings.locale]
    if not legacy:
        definition["profanityFilterMode"] = settings.profanity_filter_mode
    phrases = list(
        dict.fromkeys(term.strip() for term in [*terms, *settings.phrases] if term.strip())
    )
    if len(phrases) > 500 or any(len(term) > 200 for term in phrases):
        raise mai_error("MAI 术语最多 500 条，每条最多 200 字符。")
    if phrases:
        definition["phraseList"] = {"phrases": phrases}
        if settings.phrase_biasing_weight is not None:
            definition["phraseList"]["biasingWeight"] = settings.phrase_biasing_weight
    return definition


def redact_mai_error(body: str, secrets: tuple[str, ...] = ()) -> str:
    """Preserve diagnostic text while removing supplied and labelled credentials."""
    for secret in secrets:
        if secret:
            body = body.replace(secret, "[REDACTED]")
    body = re.sub(
        r'(?i)("(?:api[_-]?key|subscription[_-]?key|ocp-apim-subscription-key|'
        r'authorization|password|secret|token|credentials)"\s*:\s*)"(?:\\.|[^"\\])*"',
        r'\1"[REDACTED]"',
        body,
    )
    body = re.sub(
        r"(?i)\b((?:api[_-]?key|subscription[_-]?key|ocp-apim-subscription-key|"
        r"password|secret|token)\s*[:=]\s*)([^\s,;\"}\]]+)",
        r"\1[REDACTED]",
        body,
    )
    return public_error_detail(body)[:MAX_ERROR_BODY_BYTES]


class MaiHttpError(ClassScribeError):
    """A received HTTP failure with bounded, credential-redacted service diagnostics."""

    def __init__(
        self,
        status: int,
        request_id: str | None = None,
        retry_after: str | None = None,
        *,
        body: str = "",
        body_truncated: bool = False,
        body_read_error: str | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.status = status
        self.request_id = (
            request_id if request_id and re.fullmatch(r"[A-Za-z0-9._-]{1,256}", request_id) else ""
        )
        self.retry_after = (
            int(retry_after) if retry_after and re.fullmatch(r"[0-9]{1,6}", retry_after) else None
        )
        self.response_body = redact_mai_error(body, secrets)
        self.service_error_code: str | None = None
        self.service_error_message = self.response_body.strip()[:8192] or None
        # MAI sometimes wraps the Azure JSON error in a plain-text prefix.
        attempts = 0
        for position, character in enumerate(body[:MAX_ERROR_BODY_BYTES]):
            if character != "{":
                continue
            attempts += 1
            if attempts > 16:
                break
            try:
                payload, _ = json.JSONDecoder().raw_decode(body[position:MAX_ERROR_BODY_BYTES])
            except (ValueError, RecursionError):
                continue
            if isinstance(payload, dict):
                error = payload.get("error", payload)
                if isinstance(error, dict):
                    code, message = error.get("code"), error.get("message")
                    if isinstance(code, str):
                        self.service_error_code = redact_mai_error(code, secrets)[:256]
                    if isinstance(message, str):
                        self.service_error_message = redact_mai_error(message, secrets)[:8192]
            break
        self.diagnostics: dict[str, Any] = {
            "http_status": status,
            "service_error_code": self.service_error_code,
            "service_error_message": self.service_error_message,
            "request_id": self.request_id or None,
            "retry_after_seconds": self.retry_after,
            "response_body": self.response_body,
            "response_truncated": body_truncated,
            "response_read_error": body_read_error,
        }
        if status >= 500:
            reason = "Azure/MAI 上游服务暂时不可用或繁忙，请稍后重新提交。"
        elif status == 429:
            reason = "Azure 请求受到限流，请稍后重新提交或降低并行量。"
        elif status in {401, 403}:
            reason = "请检查 Azure 资源凭据及访问权限。"
        else:
            reason = "请检查 Azure 资源及请求参数。"
        detail = f"Azure HTTP {status}；{reason}未自动重发。"
        if self.service_error_code or self.service_error_message:
            detail += (
                f"上游错误 {self.service_error_code or '未知'}：{self.service_error_message or ''}"
            )
        if self.retry_after is not None:
            detail += f" 服务建议等待 {self.retry_after} 秒。"
        if self.request_id:
            detail += f" 请求 ID：{self.request_id}。"
        super().__init__(ErrorCode.JOB_STATE_CONFLICT, detail)


class MaiClient:
    """Bounded streaming upload, verified TLS, no redirect and no implicit retry."""

    def transcribe(
        self,
        endpoint: str,
        key: str,
        audio: Path,
        definition: dict[str, Any],
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[str, int, int], None],
        timeout: float = 900,
    ) -> tuple[bytes, str]:
        size = audio.stat().st_size
        if not 0 < size <= MAX_BYTES:
            raise mai_error("MAI 上传音频必须非空且不超过 240 MB，请缩短片段。")
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        context = ssl.create_default_context()
        proxy = None if proxy_bypass(host) else getproxies().get("https")
        if proxy:
            address = urlsplit(proxy if "://" in proxy else "http://" + proxy)
            if address.scheme != "http" or not address.hostname:
                raise mai_error("HTTPS_PROXY 需要使用 HTTP CONNECT 代理。")
            connection = http.client.HTTPSConnection(
                address.hostname, address.port or 8080, timeout=20, context=context
            )
            headers = {}
            if address.username:
                auth = f"{unquote(address.username)}:{unquote(address.password or '')}"
                headers["Proxy-Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
            connection.set_tunnel(host, 443, headers)
        else:
            connection = http.client.HTTPSConnection(host, timeout=20, context=context)
        boundary = "classscribe_" + uuid4().hex
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="definition"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode() + json.dumps(definition, ensure_ascii=False).encode()
        head += (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; "
            'name="audio"; filename="clip.flac"\r\n'
            "Content-Type: audio/flac\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        done = threading.Event()
        stopped = threading.Event()

        def check() -> None:
            if stopped.is_set() or cancelled():
                raise mai_error("请求已停止；云端可能仍处理并计费。不会自动重发。")

        def watch() -> None:
            while not done.wait(0.1):
                if cancelled():
                    stopped.set()
                    if connection.sock is not None:
                        with suppress(OSError):
                            connection.sock.shutdown(socket.SHUT_RDWR)
                    connection.close()
                    return

        monitor = threading.Thread(target=watch, daemon=True)
        monitor.start()
        try:
            check()
            connection.connect()
            check()
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            connection.putrequest("POST", parsed.path + "?" + parsed.query)
            connection.putheader("Ocp-Apim-Subscription-Key", key)
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(len(head) + size + len(tail)))
            connection.endheaders()
            connection.send(head)
            sent = 0
            with audio.open("rb") as source:
                while block := source.read(256 * 1024):
                    check()
                    connection.send(block)
                    sent += len(block)
                    progress("mai_upload", sent, size)
            connection.send(tail)
            progress("mai_wait", 0, 0)
            response = connection.getresponse()
            if response.status != 200:
                # Bound diagnostic reads in size and time; retain the HTTP status even
                # when the error body is incomplete or the connection breaks afterward.
                body = b""
                read_error = None
                try:
                    if connection.sock is not None:
                        connection.sock.settimeout(min(timeout, 5))
                    body = response.read(MAX_ERROR_BODY_BYTES + 1)
                except http.client.IncompleteRead as exc:
                    body = exc.partial
                    read_error = type(exc).__name__
                except (OSError, http.client.HTTPException) as exc:
                    read_error = type(exc).__name__
                raise MaiHttpError(
                    response.status,
                    response.getheader("apim-request-id") or response.getheader("x-ms-request-id"),
                    response.getheader("Retry-After"),
                    body=body[:MAX_ERROR_BODY_BYTES].decode("utf-8", errors="replace"),
                    body_truncated=len(body) > MAX_ERROR_BODY_BYTES,
                    body_read_error=read_error,
                    secrets=(key,),
                )
            chunks: list[bytes] = []
            count = 0
            while block := response.read(64 * 1024):
                check()
                count += len(block)
                if count > 80_000_000:
                    raise mai_error("MAI 响应超过 80 MB。未自动重发。")
                chunks.append(block)
            check()
            return b"".join(chunks), (response.getheader("apim-request-id") or "")[:256]
        except ClassScribeError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            check()
            raise mai_error("MAI 网络请求失败，结果未知；云端可能计费。未自动重发。") from None
        finally:
            done.set()
            connection.close()
            monitor.join(timeout=1)


@dataclass(frozen=True)
class MaiPhrase:
    text: str
    start: int
    end: int
    language: str
    speaker: str | None
    timed: bool
    words: tuple[tuple[str, int, int], ...]


def split_timed_phrase(phrase: MaiPhrase) -> tuple[MaiPhrase, ...]:
    """Split original text only at boundaries supported by complete word evidence."""
    positions = lexical_positions(phrase.text)
    content = "".join(character for character, _ in positions)
    cursor = raw_cursor = 0
    previous_end = -1
    aligned: list[tuple[int, int, int, int]] = []

    def fallback() -> tuple[MaiPhrase, ...]:
        return (
            MaiPhrase(
                phrase.text,
                phrase.start,
                phrase.end,
                phrase.language,
                phrase.speaker,
                phrase.timed,
                (),
            ),
        )

    for token, start, end in phrase.words:
        if (
            not token.strip()
            or start < previous_end
            or start >= end
            or (phrase.timed and (start < phrase.start or end > phrase.end))
        ):
            return fallback()
        previous_end = end
        token_content = "".join(character for character, _ in lexical_positions(token))
        if not token_content:
            # Preserve separately timed punctuation when it occurs in the original gap.
            gap_end = positions[cursor][1] if cursor < len(positions) else len(phrase.text)
            raw_start = phrase.text.find(token.strip(), raw_cursor, gap_end)
            if raw_start >= 0:
                raw_end = raw_start + len(token.strip())
                aligned.append((raw_start, raw_end, start, end))
                raw_cursor = raw_end
            continue
        if content[cursor : cursor + len(token_content)] != token_content:
            return fallback()
        raw_start = positions[cursor][1]
        cursor += len(token_content)
        raw_end = positions[cursor - 1][1] + 1
        aligned.append((raw_start, raw_end, start, end))
        raw_cursor = raw_end
    if cursor != len(positions) or not aligned:
        return fallback()

    sentence_ends = {end for _, end in sentence_spans(phrase.text)}
    groups: list[MaiPhrase] = []
    text_start = word_start = 0
    for index, (_, raw_end, _, _) in enumerate(aligned):
        next_start = aligned[index + 1][0] if index + 1 < len(aligned) else len(phrase.text)
        # A boundary inside one timed token cannot be assigned a new timestamp.
        ends = [end for end in sentence_ends if raw_end <= end <= next_start]
        if not ends:
            continue
        text_end = max(ends)
        words = tuple(
            (
                phrase.text[
                    text_start if word_index == word_start else aligned[word_index][0] : aligned[
                        word_index + 1
                    ][0]
                    if word_index < index
                    else text_end
                ].strip(),
                aligned[word_index][2],
                aligned[word_index][3],
            )
            for word_index in range(word_start, index + 1)
        )
        groups.append(
            MaiPhrase(
                phrase.text[text_start:text_end].strip(),
                aligned[word_start][2],
                aligned[index][3],
                phrase.language,
                phrase.speaker,
                True,
                words,
            )
        )
        text_start = text_end
        word_start = index + 1
    return tuple(groups) if text_start == len(phrase.text) else fallback()


def parse_mai_response(payload: bytes, duration: int, language: str) -> tuple[MaiPhrase, ...]:
    try:
        raw = json.loads(payload)
    except (ValueError, UnicodeError):
        raise mai_error("MAI 返回了无效 JSON；原始响应已保留。") from None
    if not isinstance(raw, dict) or "error" in raw:
        raise mai_error("MAI 响应结构无效；原始响应已保留。")

    def interval(item: dict[str, Any]) -> tuple[int, int] | None:
        start, length = item.get("offsetMilliseconds"), item.get("durationMilliseconds")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for v in (start, length)
        ):
            return None
        assert isinstance(start, (int, float)) and isinstance(length, (int, float))
        left, right = round(start * 16), min(duration, round((start + length) * 16))
        return (left, right) if 0 <= left < right <= duration else None

    phrases = raw.get("phrases", [])
    if not isinstance(phrases, list):
        raise mai_error("MAI phrases 格式无效；原始响应已保留。")
    result: list[MaiPhrase] = []
    for phrase in phrases:
        if not isinstance(phrase, dict) or not isinstance(phrase.get("text"), str):
            continue
        text = phrase["text"].strip()
        if not text:
            continue
        span = interval(phrase)
        words = phrase.get("words", [])
        tokens: list[tuple[str, int, int]] = []
        if isinstance(words, list):
            for word in words:
                if (
                    isinstance(word, dict)
                    and isinstance(word.get("text"), str)
                    and (bounds := interval(word))
                ):
                    tokens.append((word["text"], *bounds))
        locale = str(phrase.get("locale", language)).split("-")[0]
        if locale not in {"ja", "zh", "en"}:
            locale = language if language in {"ja", "zh", "en"} else "auto_mixed"
        speaker = phrase.get("speaker")
        result.extend(
            split_timed_phrase(
                MaiPhrase(
                    text,
                    *(span or (0, 0)),
                    locale,
                    str(speaker) if isinstance(speaker, (str, int)) else None,
                    span is not None,
                    tuple(tokens),
                )
            )
        )
    if not result:
        combined = raw.get("combinedPhrases", [])
        text = (
            "\n".join(
                p["text"]
                for p in combined
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            )
            if isinstance(combined, list)
            else ""
        )
        if text.strip():
            result.append(MaiPhrase(text.strip(), 0, 0, language, None, False, ()))
        elif "phrases" not in raw and "combinedPhrases" not in raw:
            raise mai_error("MAI 响应缺少转写字段；原始响应已保留。")
    return tuple(result)
