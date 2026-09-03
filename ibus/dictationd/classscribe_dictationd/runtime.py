"""PipeWire capture and worker-RPC adapters for the dictation controller."""

from __future__ import annotations

import math
import os
import stat
import struct
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from classscribe_protocol import (
    DictationConfig,
    DictationLanguage,
    Priority,
    ResidentWorkerManifest,
    RPCClient,
    RPCRequest,
    RPCResponse,
    load_resident_worker_manifest,
)

from classscribe_dictationd.streaming import DictationChunk, StableLanguageRouter, TimedToken


class EnergyVAD:
    """Bounded streaming energy gate; neural streaming VAD can replace this interface."""

    def __init__(self, threshold: float = 350.0) -> None:
        self.threshold = threshold

    async def open(self, session_id: str) -> None:
        del session_id

    async def voiced(self, pcm_s16le: bytes) -> bool:
        if not pcm_s16le or len(pcm_s16le) % 2:
            return False
        values = struct.unpack(f"<{len(pcm_s16le) // 2}h", pcm_s16le)
        rms = math.sqrt(sum(value * value for value in values) / len(values))
        return rms >= self.threshold

    async def close(self) -> None:
        pass


class RPCFrameVAD:
    """Call a preloaded FireRed streaming-VAD worker for each exact microphone frame."""

    def __init__(self, socket_path: Path) -> None:
        self.client = RPCClient(socket_path)
        self.session_id = ""
        self.stream_id = ""

    async def open(self, session_id: str) -> None:
        self.session_id = session_id
        self.stream_id = str(uuid4())
        await self._call(
            "stream_open",
            {"stream_id": self.stream_id, "sample_rate": 16_000, "channels": 1},
        )

    async def voiced(self, pcm_s16le: bytes) -> bool:
        if not self.stream_id:
            raise RuntimeError("streaming VAD is not open")
        response = await self._call(
            "stream_push", {"stream_id": self.stream_id, "pcm_s16le": pcm_s16le}
        )
        return response.result.get("voiced") is True

    async def close(self) -> None:
        if self.stream_id:
            await self._call("stream_close", {"stream_id": self.stream_id})
        self.stream_id = ""
        self.session_id = ""

    async def _call(self, method: str, params: dict[str, Any]) -> RPCResponse:
        response = await self.client.call(
            RPCRequest(
                request_id=str(uuid4()),
                job_id=f"dictation-vad:{self.session_id}",
                deadline_ms=1000,
                priority=Priority.DICTATION,
                method=method,
                params=params,
            )
        )
        if not response.ok:
            raise RuntimeError(response.error_detail or response.error_code or "VAD failed")
        return response


class ManifestRPCFrameVAD:
    """Resolve the current supervised VAD socket at each idle session boundary."""

    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self._active: RPCFrameVAD | None = None

    async def open(self, session_id: str) -> None:
        manifest = load_resident_worker_manifest(self.manifest_path)
        if not manifest.ready or manifest.vad_socket is None:
            detail = "; ".join(manifest.errors) or "resident VAD is unavailable"
            raise RuntimeError(detail)
        _require_supervised_socket(self.manifest_path, manifest.vad_socket)
        self._active = RPCFrameVAD(manifest.vad_socket)
        await self._active.open(session_id)

    async def voiced(self, pcm_s16le: bytes) -> bool:
        if self._active is None:
            raise RuntimeError("resident VAD session is not open")
        return await self._active.voiced(pcm_s16le)

    async def close(self) -> None:
        if self._active is not None:
            await self._active.close()
        self._active = None


class RPCStreamingRecognizer:
    """Use a preloaded isolated streaming worker; no model libraries enter dictationd."""

    def __init__(
        self,
        socket_path: Path,
        *,
        model_id: str = "nemotron_3_5_asr_streaming_0_6b",
        lid_socket_path: Path | None = None,
        accuracy_socket_path: Path | None = None,
    ) -> None:
        self.client = RPCClient(socket_path)
        self.lid_client = RPCClient(lid_socket_path) if lid_socket_path is not None else None
        self.accuracy_client = (
            RPCClient(accuracy_socket_path) if accuracy_socket_path is not None else None
        )
        self.model_id = model_id
        self.session_id: str | None = None
        self.stream_id: str | None = None
        self.job_id: str | None = None
        self.config = DictationConfig()
        self._overlap_pcm = bytearray()
        self._stream_pcm = bytearray()
        self._stream_base_sample: int | None = None
        self._language = StableLanguageRouter(self.config.language)
        self._last_detected_language: str | None = None
        self._accuracy_language_override: str | None = None
        self._primary_released = False

    async def open(self, session_id: str, config: DictationConfig) -> None:
        if config.model_id not in {"auto_best", self.model_id}:
            raise RuntimeError(f"dictation model is not preloaded: {config.model_id}")
        self.session_id = session_id
        self.job_id = f"dictation:{session_id}"
        self.config = config
        self._language = StableLanguageRouter(config.language)
        self._overlap_pcm.clear()
        self._stream_pcm.clear()
        self._stream_base_sample = None
        self._last_detected_language = None
        self._accuracy_language_override = None
        self._primary_released = False
        await self._open_stream()

    async def push(self, pcm_s16le: bytes, *, absolute_start_sample: int) -> tuple[TimedToken, ...]:
        if self._stream_base_sample is None:
            self._stream_base_sample = absolute_start_sample
        expected = self._stream_base_sample + len(self._stream_pcm) // 2
        if absolute_start_sample != expected:
            raise RuntimeError("dictation PCM samples are not contiguous")
        self._stream_pcm.extend(pcm_s16le)
        response = await self._call(
            "stream_push",
            {
                "stream_id": self._require_stream(),
                "pcm_s16le": pcm_s16le,
                "absolute_start_sample": absolute_start_sample,
                "language": self._active_language(),
            },
            deadline_ms=1500,
        )
        detected = _language_code(response.language)
        if detected is not None:
            self._last_detected_language = detected
        self._overlap_pcm.extend(pcm_s16le)
        overlap_bytes = round(self.config.overlap_seconds * 16_000) * 2
        del self._overlap_pcm[: max(0, len(self._overlap_pcm) - overlap_bytes)]
        return _response_tokens(
            response.normalized_text,
            response.segments,
            absolute_start_sample,
            absolute_start_sample + len(pcm_s16le) // 2,
        )

    async def finalize(
        self,
        chunk: DictationChunk,
        *,
        config: DictationConfig,
        context: tuple[str, ...],
    ) -> tuple[tuple[TimedToken, ...], tuple[str, ...]]:
        if config.confirmation.value == "accuracy" and chunk.reason == "release":
            response = await self._accuracy_decode(chunk, config=config, context=context)
        else:
            response = await self._call(
                "stream_flush",
                {
                    "stream_id": self._require_stream(),
                    "language": self._active_language(),
                    "confirmation": config.confirmation.value,
                    "rolling_context": list(context),
                    "core_start_sample": chunk.core_start_sample,
                    "end_sample": chunk.end_sample,
                },
                deadline_ms=3500,
            )
        normalized_text = _punctuate(response.normalized_text, config.punctuation.value)
        tokens = _apply_token_punctuation(
            _response_tokens(
                normalized_text,
                response.segments,
                chunk.context_start_sample,
                chunk.end_sample,
            ),
            config.punctuation.value,
        )
        raw_candidates = response.result.get("candidates", ())
        candidates = (
            tuple(
                _punctuate(str(item), config.punctuation.value)
                for item in raw_candidates
                if _punctuate(str(item), config.punctuation.value)
            )
            if isinstance(raw_candidates, (list, tuple))
            else ()
        )
        if self.config.language is DictationLanguage.AUTO_MIXED:
            reported = _language_code(response.language)
            primary = (
                {reported: 1.0}
                if reported is not None
                else _probabilities(response.result.get("language_probabilities"))
            )
            firered = await self._firered_probabilities(chunk)
            self._language.observe(
                primary,
                firered or None,
                stable_boundary=True,
                english_terminology=(
                    response.result.get("english_terminology") is True
                    or _japanese_english_terminology(normalized_text)
                ),
            )
        if chunk.reason != "release":
            await self._call(
                "stream_close", {"stream_id": self._require_stream()}, deadline_ms=1000
            )
            await self._open_stream()
            if chunk.reason == "hard_limit" and self._overlap_pcm:
                replay_start = chunk.end_sample - len(self._overlap_pcm) // 2
                self._stream_pcm = bytearray(self._overlap_pcm)
                self._stream_base_sample = replay_start
                frame_bytes = 320 * 2
                for offset in range(0, len(self._overlap_pcm), frame_bytes):
                    frame = bytes(self._overlap_pcm[offset : offset + frame_bytes])
                    await self._call(
                        "stream_push",
                        {
                            "stream_id": self._require_stream(),
                            "pcm_s16le": frame,
                            "absolute_start_sample": replay_start + offset // 2,
                            "language": self._active_language(),
                            "replayed_overlap": True,
                        },
                        deadline_ms=1500,
                    )
            elif chunk.reason == "semantic_endpoint":
                self._overlap_pcm.clear()
                self._stream_pcm.clear()
                self._stream_base_sample = None
        return tokens, candidates

    async def cancel(self) -> None:
        if self.stream_id is not None and not self._primary_released:
            await self._call("stream_close", {"stream_id": self.stream_id}, deadline_ms=1000)
        self.stream_id = None
        self._overlap_pcm.clear()
        self._stream_pcm.clear()
        self._stream_base_sample = None
        self._primary_released = False

    def accuracy_hot_switch_required(self, config: DictationConfig) -> bool:
        return config.confirmation.value == "accuracy" and self.accuracy_client is None

    def expected_accuracy_load_ms(self, config: DictationConfig) -> float | None:
        del config
        return None

    async def accuracy_language(self) -> str:
        if self.config.language is not DictationLanguage.AUTO_MIXED:
            return self.config.language.value
        if self._language.current is not None:
            return self._language.current.value
        if self._last_detected_language is not None:
            return self._last_detected_language
        if self._stream_pcm and self.lid_client is not None:
            base = self._stream_base_sample or 0
            probabilities = await self._firered_probabilities(
                DictationChunk(0, base, base, base + len(self._stream_pcm) // 2, "release")
            )
            if probabilities:
                return max(probabilities.items(), key=lambda item: item[1])[0]
        raise RuntimeError("automatic accuracy confirmation could not resolve a language")

    def install_accuracy_route(self, socket_path: Path, *, primary_released: bool) -> None:
        self.accuracy_client = RPCClient(socket_path)
        self._primary_released = primary_released

    async def _firered_probabilities(self, chunk: DictationChunk) -> dict[str, float]:
        if self.lid_client is None or not self._stream_pcm:
            return {}
        response = await self.lid_client.call(
            RPCRequest(
                request_id=str(uuid4()),
                job_id=self.job_id or "dictation-lid",
                deadline_ms=1500,
                priority=Priority.DICTATION,
                method="lid_pcm",
                params={
                    "pcm_s16le": bytes(self._stream_pcm),
                    "absolute_start_sample": self._stream_base_sample
                    if self._stream_base_sample is not None
                    else chunk.context_start_sample,
                    "sample_rate": 16_000,
                },
            )
        )
        if not response.ok:
            raise RuntimeError(response.error_detail or response.error_code or "LID failed")
        return _probabilities(response.result.get("language_probabilities"))

    async def _accuracy_decode(
        self,
        chunk: DictationChunk,
        *,
        config: DictationConfig,
        context: tuple[str, ...],
    ) -> RPCResponse:
        if self.accuracy_client is None:
            raise RuntimeError("accuracy confirmation worker is not configured")
        if not self._stream_pcm:
            raise RuntimeError("accuracy confirmation received no audio")
        base = self._stream_base_sample
        if base is None:
            base = chunk.context_start_sample
        language = self._accuracy_language_override or await self.accuracy_language()
        return await self._call_with(
            self.accuracy_client,
            "transcribe_pcm",
            {
                "pcm_s16le": bytes(self._stream_pcm),
                "absolute_start_sample": base,
                "core_start_sample": chunk.core_start_sample,
                "end_sample": base + len(self._stream_pcm) // 2,
                "sample_rate": 16_000,
                "channels": 1,
                "language": language,
                "rolling_context": list(context),
            },
            deadline_ms=30_000,
        )

    async def _open_stream(self) -> None:
        self.stream_id = str(uuid4())
        await self._call(
            "stream_open",
            {
                "stream_id": self.stream_id,
                "sample_rate": 16_000,
                "channels": 1,
                "model_id": self.model_id,
                "language": self._active_language(),
                "chunk_ms": self.config.stream_chunk_ms,
            },
            deadline_ms=2000,
        )

    async def _call(self, method: str, params: dict[str, Any], *, deadline_ms: int) -> RPCResponse:
        return await self._call_with(self.client, method, params, deadline_ms=deadline_ms)

    async def _call_with(
        self,
        client: RPCClient,
        method: str,
        params: dict[str, Any],
        *,
        deadline_ms: int,
    ) -> RPCResponse:
        if self.job_id is None:
            raise RuntimeError("recognizer session is not open")
        response = await client.call(
            RPCRequest(
                request_id=str(uuid4()),
                job_id=self.job_id,
                deadline_ms=deadline_ms,
                priority=Priority.DICTATION,
                method=method,
                params=params,
            )
        )
        if not response.ok:
            raise RuntimeError(response.error_detail or response.error_code or "worker failed")
        return response

    def _require_stream(self) -> str:
        if self.stream_id is None:
            raise RuntimeError("recognizer stream is not open")
        return self.stream_id

    def _active_language(self) -> str:
        active = self._language.current
        return active.value if active is not None else self.config.language.value


class RoutedStreamingRecognizer:
    """Select one already-preloaded worker without loading models in dictationd."""

    def __init__(
        self,
        recognizers: Mapping[str, RPCStreamingRecognizer],
        *,
        default_model_id: str | None = None,
    ) -> None:
        self.recognizers = dict(recognizers)
        if not self.recognizers:
            raise ValueError("at least one preloaded dictation model is required")
        if any(not model_id or model_id == "auto_best" for model_id in self.recognizers):
            raise ValueError("dictation model routes require concrete model IDs")
        self.default_model_id = default_model_id or next(iter(self.recognizers))
        if self.default_model_id not in self.recognizers:
            raise ValueError("default dictation model has no worker route")
        self._active: RPCStreamingRecognizer | None = None

    async def open(self, session_id: str, config: DictationConfig) -> None:
        model_id = self.default_model_id if config.model_id == "auto_best" else config.model_id
        recognizer = self.recognizers.get(model_id)
        if recognizer is None:
            raise RuntimeError(f"dictation model is not preloaded: {config.model_id}")
        if self._active is not None:
            raise RuntimeError("a dictation model route is already active")
        self._active = recognizer
        try:
            await recognizer.open(session_id, config)
        except BaseException:
            self._active = None
            raise

    async def push(self, pcm_s16le: bytes, *, absolute_start_sample: int) -> tuple[TimedToken, ...]:
        return await self._require_active().push(
            pcm_s16le, absolute_start_sample=absolute_start_sample
        )

    async def finalize(
        self,
        chunk: DictationChunk,
        *,
        config: DictationConfig,
        context: tuple[str, ...],
    ) -> tuple[tuple[TimedToken, ...], tuple[str, ...]]:
        return await self._require_active().finalize(chunk, config=config, context=context)

    async def cancel(self) -> None:
        recognizer = self._require_active()
        try:
            await recognizer.cancel()
        finally:
            self._active = None

    def accuracy_hot_switch_required(self, config: DictationConfig) -> bool:
        return self._require_active().accuracy_hot_switch_required(config)

    def expected_accuracy_load_ms(self, config: DictationConfig) -> float | None:
        return self._active.expected_accuracy_load_ms(config) if self._active is not None else None

    async def accuracy_language(self) -> str:
        return await self._require_active().accuracy_language()

    async def prepare_accuracy(self) -> None:
        if self._require_active().accuracy_client is None:
            raise RuntimeError("explicit accuracy worker is unavailable")

    def _require_active(self) -> RPCStreamingRecognizer:
        if self._active is None:
            raise RuntimeError("no dictation model route is active")
        return self._active


class ManifestRoutedStreamingRecognizer:
    """Resolve auto-best and concrete routes from the core's signed-off local state."""

    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self._active: RPCStreamingRecognizer | None = None
        self._active_primary_socket: Path | None = None
        self._hot_switch_required = False
        self._expected_load_ms: float | None = None

    def available_models(self) -> tuple[str, ...]:
        try:
            manifest = load_resident_worker_manifest(self.manifest_path)
        except (OSError, ValueError):
            return ()
        available = set(manifest.profile_routes.values())
        if manifest.default_model_id is not None:
            available.add(manifest.default_model_id)
        return tuple(route.model_id for route in manifest.routes if route.model_id in available)

    async def open(self, session_id: str, config: DictationConfig) -> None:
        if self._active is not None:
            raise RuntimeError("a dictation model route is already active")
        manifest = load_resident_worker_manifest(self.manifest_path)
        if not manifest.ready:
            raise RuntimeError("; ".join(manifest.errors) or "resident workers are unavailable")
        route_by_id = {item.model_id: item for item in manifest.routes}
        if config.model_id == "auto_best":
            language = config.language.value
            if language == "auto_mixed":
                language = "auto"
            key = f"ibus.{language}.{config.confirmation.value}"
            model_id = manifest.profile_routes.get(key, manifest.default_model_id)
        else:
            model_id = config.model_id
        route = route_by_id.get(model_id or "")
        if route is None:
            raise RuntimeError(f"dictation model is not supervised: {model_id}")
        language = config.language.value
        accuracy_route = manifest.accuracy_routes.get(language)
        accuracy_socket = (
            accuracy_route.socket_path if accuracy_route is not None else manifest.accuracy_socket
        )
        if (
            config.confirmation.value == "accuracy"
            and accuracy_route is None
            and accuracy_socket is None
        ):
            raise RuntimeError(f"no supervised accuracy model is installed for {language}")
        for socket in (route.socket_path, manifest.lid_socket, accuracy_socket):
            if socket is not None:
                _require_supervised_socket(self.manifest_path, socket)
        recognizer = RPCStreamingRecognizer(
            route.socket_path,
            model_id=route.model_id,
            lid_socket_path=manifest.lid_socket,
            accuracy_socket_path=accuracy_socket,
        )
        self._active = recognizer
        self._active_primary_socket = route.socket_path
        self._hot_switch_required = config.confirmation.value == "accuracy" and (
            config.language is DictationLanguage.AUTO_MIXED
            or (accuracy_route is not None and accuracy_route.socket_path is None)
        )
        self._expected_load_ms = self._manifest_expected_load_ms(manifest, config)
        try:
            await recognizer.open(session_id, config)
        except BaseException:
            self._active = None
            raise

    async def push(self, pcm_s16le: bytes, *, absolute_start_sample: int) -> tuple[TimedToken, ...]:
        return await self._require_active().push(
            pcm_s16le, absolute_start_sample=absolute_start_sample
        )

    async def finalize(
        self,
        chunk: DictationChunk,
        *,
        config: DictationConfig,
        context: tuple[str, ...],
    ) -> tuple[tuple[TimedToken, ...], tuple[str, ...]]:
        return await self._require_active().finalize(chunk, config=config, context=context)

    async def cancel(self) -> None:
        if self._active is None:
            return
        recognizer = self._active
        self._active = None
        self._active_primary_socket = None
        self._hot_switch_required = False
        self._expected_load_ms = None
        await recognizer.cancel()

    def accuracy_hot_switch_required(self, config: DictationConfig) -> bool:
        return config.confirmation.value == "accuracy" and self._hot_switch_required

    def expected_accuracy_load_ms(self, config: DictationConfig) -> float | None:
        if config.confirmation.value != "accuracy":
            return None
        if self._active is not None:
            return self._expected_load_ms
        try:
            manifest = load_resident_worker_manifest(self.manifest_path)
        except (OSError, ValueError):
            return None
        return self._manifest_expected_load_ms(manifest, config)

    async def accuracy_language(self) -> str:
        return await self._require_active().accuracy_language()

    async def prepare_accuracy(self) -> None:
        recognizer = self._require_active()
        language = await recognizer.accuracy_language()
        manifest = load_resident_worker_manifest(self.manifest_path)
        route = manifest.accuracy_routes.get(language)
        if route is None or route.socket_path is None:
            raise RuntimeError(f"core did not prepare an accuracy worker for {language}")
        _require_supervised_socket(self.manifest_path, route.socket_path)
        primary = self._active_primary_socket
        recognizer._accuracy_language_override = language
        recognizer.install_accuracy_route(
            route.socket_path,
            primary_released=primary is None or not primary.exists(),
        )
        self._hot_switch_required = False
        self._expected_load_ms = None

    @staticmethod
    def _manifest_expected_load_ms(
        manifest: ResidentWorkerManifest, config: DictationConfig
    ) -> float | None:
        if config.language is DictationLanguage.AUTO_MIXED:
            estimates = [
                route.expected_load_ms
                for language, route in manifest.accuracy_routes.items()
                if language in {"zh", "ja", "en"} and route.expected_load_ms is not None
            ]
            return max(estimates) if estimates else None
        route = manifest.accuracy_routes.get(config.language.value)
        return route.expected_load_ms if route is not None else None

    def _require_active(self) -> RPCStreamingRecognizer:
        if self._active is None:
            raise RuntimeError("no resident dictation model route is active")
        return self._active


class GStreamerPipeWireSource:
    """GI is imported only inside dictationd on Fedora, never by test or core imports."""

    PIPELINE = (
        "pipewiresrc do-timestamp=true ! queue max-size-buffers=4 leaky=downstream "
        "! audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=16000,channels=1 "
        "! appsink name=classscribe_sink emit-signals=true sync=false max-buffers=4 drop=true"
    )

    def __init__(self) -> None:
        self._pipeline: Any | None = None
        self._bus: Any | None = None
        self._pending = bytearray()

    def start(
        self,
        callback: Callable[[bytes], None],
        error_callback: Callable[[Exception], None],
    ) -> None:
        try:
            import gi  # type: ignore[import-not-found]

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst  # type: ignore[import-not-found]
        except (ImportError, ValueError) as exc:
            raise RuntimeError("GStreamer GI with pipewiresrc is unavailable") from exc
        Gst.init(None)
        pipeline = Gst.parse_launch(self.PIPELINE)
        sink = pipeline.get_by_name("classscribe_sink")

        def sample(_sink: Any) -> Any:
            try:
                value = _sink.emit("pull-sample")
                if value is None:
                    raise RuntimeError("PipeWire returned no audio sample")
                buffer = value.get_buffer()
                success, mapped = buffer.map(Gst.MapFlags.READ)
                if success:
                    try:
                        self.feed_buffer(bytes(mapped.data), callback)
                    finally:
                        buffer.unmap(mapped)
            except Exception as exc:
                error_callback(exc)
            return Gst.FlowReturn.OK

        sink.connect("new-sample", sample)
        bus = pipeline.get_bus()
        bus.add_signal_watch()

        def bus_message(_bus: Any, message: Any) -> None:
            if message.type == Gst.MessageType.ERROR:
                error, _debug = message.parse_error()
                error_callback(RuntimeError(f"PipeWire/GStreamer error: {error}"))
            elif message.type == Gst.MessageType.EOS:
                error_callback(RuntimeError("PipeWire microphone stream ended"))

        bus.connect("message", bus_message)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            bus.remove_signal_watch()
            raise RuntimeError("PipeWire capture pipeline failed to start")
        self._pipeline = pipeline
        self._bus = bus

    def feed_buffer(self, value: bytes, callback: Callable[[bytes], None]) -> None:
        """Normalize arbitrary GStreamer buffers into exact 20 ms frames."""

        self._pending.extend(value)
        frame_bytes = 320 * 2
        while len(self._pending) >= frame_bytes:
            callback(bytes(self._pending[:frame_bytes]))
            del self._pending[:frame_bytes]

    def stop(self) -> None:
        if self._pipeline is not None:
            from gi.repository import Gst

            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            if self._bus is not None:
                self._bus.remove_signal_watch()
                self._bus = None
            self._pending.clear()


def _require_supervised_socket(manifest_path: Path, socket_path: Path) -> None:
    root = manifest_path.parent.resolve(strict=True)
    workers = root / "workers"
    metadata = socket_path.lstat()
    if (
        socket_path.is_symlink()
        or socket_path.parent != workers
        or not stat.S_ISSOCK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RuntimeError("resident worker socket is outside the supervised runtime directory")


def _response_tokens(
    text: str,
    segments: tuple[dict[str, Any], ...],
    fallback_start: int,
    fallback_end: int,
) -> tuple[TimedToken, ...]:
    words: list[TimedToken] = []
    for segment in segments:
        raw_words = segment.get("words", ())
        if not isinstance(raw_words, (list, tuple)):
            continue
        for word in raw_words:
            if isinstance(word, Mapping):
                words.append(
                    TimedToken(
                        str(word["text"]),
                        int(word["start_sample"]),
                        int(word["end_sample"]),
                        bool(word.get("stable", True)),
                    )
                )
    if words or not text:
        return tuple(words)
    return (TimedToken(text, fallback_start, max(fallback_start + 1, fallback_end)),)


def _probabilities(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for language in ("zh", "ja", "en"):
        probability = value.get(language)
        if isinstance(probability, (int, float)) and not isinstance(probability, bool):
            result[language] = float(probability)
    return result


def _language_code(value: str | None) -> str | None:
    if value is None:
        return None
    return {
        "zh": "zh",
        "chinese": "zh",
        "中文": "zh",
        "ja": "ja",
        "japanese": "ja",
        "日本語": "ja",
        "en": "en",
        "english": "en",
    }.get(value.strip().casefold())


def _japanese_english_terminology(text: str) -> bool:
    latin = sum(character.isascii() and character.isalpha() for character in text)
    japanese = sum(
        "\u3040" <= character <= "\u30ff" or "\u4e00" <= character <= "\u9fff" for character in text
    )
    return bool(latin and japanese and latin / max(1, latin + japanese) <= 0.35)


_SENTENCE_END = frozenset(".?!。！？")  # noqa: RUF001 - supported CJK punctuation


def _punctuate(text: str, mode: str) -> str:
    if mode == "automatic":
        return text
    if mode == "off":
        return "".join(character for character in text if not _is_punctuation(character))
    if mode == "sentence_end":
        return "".join(
            character
            for character in text
            if not _is_punctuation(character) or character in _SENTENCE_END
        )
    raise ValueError("unsupported dictation punctuation mode")


def _apply_token_punctuation(tokens: tuple[TimedToken, ...], mode: str) -> tuple[TimedToken, ...]:
    result: list[TimedToken] = []
    for token in tokens:
        text = _punctuate(token.text, mode)
        if text:
            result.append(TimedToken(text, token.start_sample, token.end_sample, token.stable))
    return tuple(result)


def _is_punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith("P")
