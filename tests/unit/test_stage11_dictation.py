from __future__ import annotations

import asyncio
import json
import socket
import sys
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import pytest
from classscribe.api.app import create_app
from classscribe.paths import AppPaths
from classscribe.scheduler import GPULeaseIPCServer, GPULeaseManager
from classscribe_protocol import (
    ActivationMode,
    ConfirmationMode,
    DictationConfig,
    DictationLanguage,
    DictationState,
    DictationStatus,
    RPCRequest,
    RPCResponse,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ibus/dictationd"))
sys.path.insert(0, str(ROOT / "ibus/engine"))
sys.path.insert(0, str(ROOT / "ibus/hotkey_portal"))

from classscribe_dictationd.daemon import DictationService  # noqa: E402
from classscribe_dictationd.runtime import (  # noqa: E402
    GStreamerPipeWireSource,
    ManifestRoutedStreamingRecognizer,
    RoutedStreamingRecognizer,
    RPCStreamingRecognizer,
    _punctuate,
)
from classscribe_dictationd.session import DictationController  # noqa: E402
from classscribe_dictationd.streaming import (  # noqa: E402
    SAMPLE_RATE,
    ChunkPlanner,
    DictationChunk,
    StableLanguageRouter,
    StablePrefix,
    TimedToken,
    merge_timed_tokens,
)
from classscribe_hotkey_portal.runtime import PortalController  # noqa: E402
from classscribe_ibus_engine.controller import (  # noqa: E402
    ACTIVATION_KEY,
    EngineController,
)


class _Recognizer:
    def __init__(self, *, candidates: tuple[str, ...] = ()) -> None:
        self.opened = ""
        self.cancelled = False
        self.tokens: tuple[TimedToken, ...] = ()
        self.candidates = candidates

    async def open(self, session_id: str, config: DictationConfig) -> None:
        del config
        self.opened = session_id

    async def push(self, pcm_s16le: bytes, *, absolute_start_sample: int) -> tuple[TimedToken, ...]:
        end = absolute_start_sample + len(pcm_s16le) // 2
        self.tokens = (
            *self.tokens,
            TimedToken(chr(65 + len(self.tokens)), absolute_start_sample, end),
        )
        return self.tokens

    async def finalize(
        self, chunk: Any, *, config: DictationConfig, context: tuple[str, ...]
    ) -> tuple[tuple[TimedToken, ...], tuple[str, ...]]:
        del chunk, config, context
        return self.tokens, self.candidates

    async def cancel(self) -> None:
        self.cancelled = True

    def accuracy_hot_switch_required(self, config: DictationConfig) -> bool:
        del config
        return False

    def expected_accuracy_load_ms(self, config: DictationConfig) -> float | None:
        del config
        return None

    async def accuracy_language(self) -> str:
        return "zh"

    async def prepare_accuracy(self) -> None:
        return None


def test_five_minute_chunk_plan_has_no_core_gap_and_time_token_merge_has_no_loss() -> None:
    planner = ChunkPlanner(hard_seconds=25, overlap_seconds=2.0)
    chunks = []
    frames = 5 * 60 * 50
    for _ in range(frames):
        chunk = planner.push(320, voiced=True)
        if chunk is not None:
            chunks.append(chunk)
    tail = planner.release()
    if tail is not None:
        chunks.append(tail)
    assert chunks[0].core_start_sample == 0
    assert chunks[-1].end_sample == 5 * 60 * SAMPLE_RATE
    assert all(left.end_sample == right.core_start_sample for left, right in pairwise(chunks))
    assert all(
        item.context_start_sample == max(0, item.core_start_sample - 2 * SAMPLE_RATE)
        for item in chunks
    )

    truth = tuple(
        TimedToken(f"{second}|", second * SAMPLE_RATE, (second + 1) * SAMPLE_RATE)
        for second in range(300)
    )
    merged: tuple[TimedToken, ...] = ()
    for chunk in chunks:
        incoming = tuple(
            token
            for token in truth
            if token.start_sample < chunk.end_sample
            and chunk.context_start_sample < token.end_sample
        )
        merged = merge_timed_tokens(merged, incoming)
    assert merged == truth


def test_stable_prefix_retains_pending_tail_across_hard_chunks() -> None:
    prefix = StablePrefix(stable_after_seconds=45)
    truth = tuple(
        TimedToken(chr(0x4E00 + second), second * SAMPLE_RATE, (second + 1) * SAMPLE_RATE)
        for second in range(75)
    )
    committed: list[TimedToken] = []
    for end in (25, 50, 75):
        incoming = tuple(
            token
            for token in truth
            if max(0, end - 27) * SAMPLE_RATE < token.end_sample <= end * SAMPLE_RATE
        )
        committed.extend(prefix.update(incoming, absolute_sample=end * SAMPLE_RATE))
    committed.extend(
        prefix.finalize(tuple(token for token in truth if token.end_sample > 48 * SAMPLE_RATE))
    )
    assert tuple(committed) == truth


def test_unstable_token_is_never_dropped_when_it_ages_past_prefix_horizon() -> None:
    prefix = StablePrefix(stable_after_seconds=1)
    unstable = TimedToken("保留", 0, 320, stable=False)
    assert prefix.update((unstable,), absolute_sample=32_000) == ()
    assert prefix.pending == (unstable,)
    stable = TimedToken("保留", 0, 320, stable=True)
    assert prefix.update((stable,), absolute_sample=32_320) == (stable,)
    assert not prefix.pending


def test_manual_and_auto_language_routing_only_switch_at_stable_boundaries() -> None:
    manual = StableLanguageRouter(DictationLanguage.JAPANESE)
    assert manual.observe({"en": 1.0}, stable_boundary=True) is DictationLanguage.JAPANESE
    automatic = StableLanguageRouter(DictationLanguage.AUTO_MIXED)
    evidence = {"zh": 0.05, "ja": 0.9, "en": 0.05}
    assert automatic.observe(evidence, stable_boundary=False) is None
    assert automatic.observe(evidence, stable_boundary=True) is None
    assert automatic.observe(evidence, stable_boundary=True) is DictationLanguage.JAPANESE
    english = {"zh": 0.05, "ja": 0.05, "en": 0.9}
    assert (
        automatic.observe(english, stable_boundary=True, english_terminology=True)
        is DictationLanguage.JAPANESE
    )

    firered_only = StableLanguageRouter(DictationLanguage.AUTO_MIXED)
    assert firered_only.observe({}, {"zh": 0.93}, stable_boundary=True) is None
    assert firered_only.observe({}, {"zh": 0.93}, stable_boundary=True) is DictationLanguage.CHINESE


def test_controller_preedit_candidate_commit_and_cancel_state_machine() -> None:
    async def scenario() -> None:
        recognizer = _Recognizer(candidates=("first", "second"))
        controller = DictationController(recognizer)
        assert (await controller.begin("session")).state is DictationState.LISTENING
        interim = await controller.feed(b"\0\1" * 320, voiced=True)
        assert interim.state is DictationState.INTERIM_UPDATE and interim.preedit == "A"
        selecting = await controller.release(defer_candidates=True)
        assert selecting.state is DictationState.CANDIDATE_SELECT
        committed = controller.select_candidate(1)
        assert committed.state is DictationState.IDLE and committed.commit_text == "second"
        states = [item.state for item in controller.events_after(0)]
        assert DictationState.ARMING in states
        assert DictationState.FINALIZING in states
        assert DictationState.COMMITTING in states
        assert recognizer.cancelled

        await controller.begin("cancel")
        await controller.feed(b"\0\1" * 320, voiced=True)
        cancelled = await controller.cancel()
        assert cancelled.state is DictationState.IDLE and cancelled.commit_text == ""

    asyncio.run(scenario())


def test_final_after_semantic_cut_keeps_prior_tail_and_suppresses_window_only_candidates() -> None:
    async def scenario() -> None:
        recognizer = _Recognizer(candidates=("current-window-only", "alternate"))
        controller = DictationController(recognizer)
        await controller.begin("long")
        for _ in range(150):
            await controller.feed(b"\0\1" * 320, voiced=True)
        for _ in range(35):
            await controller.feed(b"\0\0" * 320, voiced=False)
        assert any("semantic_endpoint" in item.message for item in controller.events_after(0))
        for _ in range(10):
            await controller.feed(b"\0\1" * 320, voiced=True)
        completed = await controller.release(defer_candidates=True)
        assert completed.state is DictationState.IDLE
        assert completed.commit_text == "".join(item.text for item in recognizer.tokens)
        assert "current-window-only" not in completed.commit_text

    asyncio.run(scenario())


class _AudioSource:
    def __init__(self) -> None:
        self.callback: Any = None
        self.error_callback: Any = None
        self.stopped = False

    def start(self, callback: Any, error_callback: Any) -> None:
        self.callback = callback
        self.error_callback = error_callback

    def stop(self) -> None:
        self.stopped = True


class _Lease:
    def __init__(self) -> None:
        self.begun: list[str] = []
        self.ended: list[str] = []
        self.prepared: list[tuple[str, str]] = []

    async def begin(self, session_id: str) -> None:
        self.begun.append(session_id)

    async def end(self, session_id: str) -> None:
        self.ended.append(session_id)

    async def prepare_accuracy(self, session_id: str, language: str) -> None:
        self.prepared.append((session_id, language))


def test_dictation_service_owns_audio_and_matching_gpu_lease_without_persistence() -> None:
    async def scenario() -> None:
        source = _AudioSource()
        lease = _Lease()
        service = DictationService(DictationController(_Recognizer()), source, lease)
        started = await service.start()
        assert started.state is DictationState.LISTENING
        source.callback(b"\0\1" * 320)
        await asyncio.sleep(0)
        stopped = await service.stop()
        assert stopped.state is DictationState.IDLE
        assert lease.begun == lease.ended
        assert source.stopped

    asyncio.run(scenario())


def test_microphone_disconnect_returns_to_idle_without_committing() -> None:
    async def scenario() -> None:
        source = _AudioSource()
        lease = _Lease()
        service = DictationService(DictationController(_Recognizer()), source, lease)
        await service.start()
        source.error_callback(RuntimeError("device removed"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert service.controller.current.state is DictationState.IDLE
        assert "microphone disconnected" in service.controller.current.message
        assert service.controller.current.commit_text == ""
        assert lease.begun == lease.ended

    asyncio.run(scenario())


def test_dictation_service_prepares_core_hot_switch_before_accuracy_finalize() -> None:
    class HotSwitchRecognizer(_Recognizer):
        def __init__(self) -> None:
            super().__init__()
            self.prepared = False

        def accuracy_hot_switch_required(self, _config: DictationConfig) -> bool:
            return True

        def expected_accuracy_load_ms(self, _config: DictationConfig) -> float:
            return 4200.0

        async def accuracy_language(self) -> str:
            return "ja"

        async def prepare_accuracy(self) -> None:
            self.prepared = True

        async def finalize(
            self, chunk: Any, *, config: DictationConfig, context: tuple[str, ...]
        ) -> tuple[tuple[TimedToken, ...], tuple[str, ...]]:
            assert self.prepared
            return await super().finalize(chunk, config=config, context=context)

    async def scenario() -> None:
        source = _AudioSource()
        lease = _Lease()
        recognizer = HotSwitchRecognizer()
        controller = DictationController(recognizer)
        controller.configure(
            DictationConfig(
                language=DictationLanguage.JAPANESE,
                confirmation=ConfirmationMode.ACCURACY,
            )
        )
        service = DictationService(controller, source, lease)
        status = await service.start()
        assert status.expected_accuracy_load_ms == 4200.0
        source.callback(b"\0\1" * 320)
        await asyncio.sleep(0)
        stopped = await service.stop()
        assert stopped.state is DictationState.IDLE
        assert lease.prepared == [(lease.begun[0], "ja")]
        assert recognizer.prepared

    asyncio.run(scenario())


class _Bridge:
    def __init__(self) -> None:
        self.preedits: list[str] = []
        self.commits: list[str] = []
        self.lookups: list[tuple[tuple[str, ...], bool]] = []
        self.messages: list[str] = []
        self.properties: list[Mapping[str, str | bool]] = []
        self.available_models: tuple[str, ...] = ()

    def update_preedit(self, text: str, stable_characters: int) -> None:
        del stable_characters
        self.preedits.append(text)

    def commit(self, text: str) -> None:
        self.commits.append(text)

    def update_lookup(self, candidates: tuple[str, ...], visible: bool) -> None:
        self.lookups.append((candidates, visible))

    def show_status(self, message: str) -> None:
        self.messages.append(message)

    def update_properties(self, values: Mapping[str, str | bool]) -> None:
        self.properties.append(values)

    def update_available_models(self, model_ids: tuple[str, ...]) -> None:
        self.available_models = model_ids


class _Control:
    def __init__(self) -> None:
        self.revision = 0
        self.fail = False
        self.instance_id = "fixture"
        self.current = DictationStatus(None, 0, DictationState.IDLE).as_dict()

    def request(self, action: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        del params
        if self.fail:
            raise RuntimeError("down")
        events = []
        if action == "begin":
            events = [self._status(DictationState.ARMING), self._status(DictationState.LISTENING)]
        elif action == "release":
            events = [
                self._status(DictationState.COMMITTING, commit_text="done"),
                self._status(DictationState.IDLE),
            ]
        return {
            "ok": True,
            "events": events,
            "current": self.current,
            "instance_id": self.instance_id,
            "config": DictationConfig().as_dict(),
        }

    def _status(self, state: DictationState, **changes: Any) -> dict[str, Any]:
        self.revision += 1
        self.current = DictationStatus(
            "session" if state is not DictationState.IDLE else None, self.revision, state, **changes
        ).as_dict()
        return self.current


def test_thin_engine_passes_normal_keys_and_never_crashes_on_daemon_failure() -> None:
    bridge = _Bridge()
    client = _Control()
    engine = EngineController(client, bridge)
    assert engine.handle_key(ord("x")) is False
    assert engine.handle_key(ACTIVATION_KEY) is True
    assert engine.handle_key(ACTIVATION_KEY, released=True) is True
    assert bridge.commits == ["done"]
    client.fail = True
    assert engine.poll()
    assert bridge.preedits[-1] == ""
    assert "unavailable" in bridge.messages[-1]

    engine.config = DictationConfig(activation=ActivationMode.TOGGLE)
    client.fail = False
    assert engine.handle_key(ACTIVATION_KEY)


class _PortalBackend:
    def register(self, activated: Any, deactivated: Any) -> None:
        self.activated = activated
        self.deactivated = deactivated

    def run(self) -> None:
        pass


def test_portal_uses_global_shortcuts_callbacks_and_keeps_ibus_fallback(tmp_path: Path) -> None:
    backend = _PortalBackend()
    control = _Control()
    status_path = tmp_path / "portal-status.json"
    portal = PortalController(backend, control, status_path=status_path)
    assert portal.register() and portal.available
    assert json.loads(status_path.read_text(encoding="utf-8"))["available"] is True
    assert status_path.stat().st_mode & 0o777 == 0o600
    backend.activated()
    backend.deactivated()
    assert "registered" in portal.diagnostic

    class Broken(_PortalBackend):
        def register(self, activated: Any, deactivated: Any) -> None:
            del activated, deactivated
            raise RuntimeError("unsupported")

    failed = PortalController(Broken(), control)
    assert not failed.register()
    assert "IBus input source" in failed.diagnostic


def test_gstreamer_buffers_are_exact_twenty_ms_frames() -> None:
    source = GStreamerPipeWireSource()
    frames: list[bytes] = []
    source.feed_buffer(b"x" * 1000, frames.append)
    source.feed_buffer(b"y" * 920, frames.append)
    assert [len(item) for item in frames] == [640, 640, 640]
    assert "pipewiresrc" in source.PIPELINE and "rate=16000,channels=1" in source.PIPELINE


def test_dictation_punctuation_modes_never_change_non_punctuation_characters() -> None:
    source = "Hello, 世界；test!"  # noqa: RUF001 - punctuation fixture
    assert _punctuate(source, "automatic") == source
    assert _punctuate(source, "sentence_end") == "Hello 世界test!"
    assert _punctuate(source, "off") == "Hello 世界test"


def test_core_gpu_lease_ipc_is_idempotent_and_same_session_balanced(tmp_path: Path) -> None:
    async def request(path: Path, action: str, *, language: str | None = None) -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(path)
        payload = {"action": action, "session_id": "s1"}
        if language is not None:
            payload["language"] = language
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        result = cast(dict[str, Any], json.loads(await reader.readline()))
        writer.close()
        await writer.wait_closed()
        return result

    async def scenario() -> None:
        manager = GPULeaseManager()
        events: list[str] = []
        server = GPULeaseIPCServer(
            tmp_path / "lease.sock",
            manager,
            on_begin=lambda: events.append("begin"),
            on_end=lambda: events.append("end"),
            on_prepare_accuracy=lambda language: events.append(f"accuracy:{language}"),
        )
        await server.start()
        try:
            assert (await request(server.socket_path, "begin_dictation"))["ok"]
            assert (await request(server.socket_path, "begin_dictation"))["ok"]
            assert manager.dictation_active
            assert (await request(server.socket_path, "prepare_accuracy", language="ja"))["ok"]
            assert (await request(server.socket_path, "end_dictation"))["ok"]
            assert not manager.dictation_active
            assert events == ["begin", "accuracy:ja", "end"]
        finally:
            await server.close()

    asyncio.run(scenario())


def test_gpu_lease_response_waits_until_async_safe_boundary_callback(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        callback_started = asyncio.Event()

        async def on_begin() -> None:
            callback_started.set()
            await gate.wait()

        manager = GPULeaseManager()
        server = GPULeaseIPCServer(
            tmp_path / "lease.sock",
            manager,
            on_begin=on_begin,
        )
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            writer.write(b'{"action":"begin_dictation","session_id":"safe"}\n')
            await writer.drain()
            response = asyncio.create_task(reader.readline())
            await callback_started.wait()
            await asyncio.sleep(0)
            assert not response.done()
            gate.set()
            assert json.loads(await response) == {"ok": True, "dictation_sessions": 1}
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_core_lifespan_hands_gpu_between_classroom_and_resident_workers(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class Pipeline:
        async def close(self) -> None:
            events.append("classroom-closed")

        def start_recovered_jobs(self) -> None:
            events.append("jobs-recovered")

        async def arm_dictation_at_safe_boundary(self) -> tuple[str, ...]:
            events.append("classroom-paused")
            return ("job",)

        def resume_preempted(self) -> tuple[str, ...]:
            events.append("classroom-resumed")
            return ("job",)

    class Resident:
        async def start(self) -> None:
            events.append("resident-started")

        async def resume_for_dictation(self) -> Any:
            events.append("resident-loaded")
            return type("ReadyManifest", (), {"ready": True, "errors": ()})()

        async def end_dictation(self) -> None:
            events.append("resident-unloaded")

        async def close(self) -> None:
            events.append("resident-closed")

    service = cast(
        Any,
        type(
            "Service",
            (),
            {"pipeline": Pipeline(), "resident_workers": Resident()},
        )(),
    )
    paths = AppPaths.from_environment(
        {"XDG_RUNTIME_DIR": str(tmp_path.parent / "r")}, home=tmp_path / "home"
    )
    app = create_app(
        service=service,
        enable_scheduler_ipc=True,
        runtime_paths=paths,
    )

    async def lease(action: str) -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(paths.runtime / "gpu-lease.sock")
        writer.write(json.dumps({"action": action, "session_id": "voice"}).encode() + b"\n")
        await writer.drain()
        response = cast(dict[str, Any], json.loads(await reader.readline()))
        writer.close()
        await writer.wait_closed()
        return response

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            assert (await lease("begin_dictation"))["ok"] is True
            assert (await lease("end_dictation"))["ok"] is True

    asyncio.run(scenario())
    assert events == [
        "jobs-recovered",
        "resident-started",
        "classroom-paused",
        "resident-loaded",
        "resident-unloaded",
        "classroom-resumed",
        "classroom-closed",
        "resident-closed",
    ]


def test_compatibility_matrix_covers_required_application_families() -> None:
    data = json.loads((ROOT / "config/ibus-compatibility.v1.json").read_text(encoding="utf-8"))
    applications = {item["application"] for item in data["applications"]}
    assert applications == {
        "GNOME Text Editor",
        "KWrite/Kate",
        "Firefox",
        "Chromium",
        "VS Code",
        "GNOME Console",
        "Konsole",
        "LibreOffice Writer",
    }
    assert data["fallback"] == "plain_preedit_then_commit"


def test_dictation_model_property_only_accepts_a_preloaded_installed_model() -> None:
    service = DictationService(
        DictationController(_Recognizer()),
        _AudioSource(),
        _Lease(),
        available_models=("nemotron",),
    )
    assert service.configure(DictationConfig(model_id="nemotron")).properties["model_id"] == (
        "nemotron"
    )
    with pytest.raises(ValueError, match="not installed"):
        service.configure(DictationConfig(model_id="missing"))


def test_dictation_model_router_selects_concrete_or_auto_best_preloaded_worker() -> None:
    async def scenario() -> None:
        first = _Recognizer()
        second = _Recognizer()
        router = RoutedStreamingRecognizer(
            cast(Any, {"first": first, "second": second}), default_model_id="second"
        )
        await router.open("automatic", DictationConfig(model_id="auto_best"))
        assert second.opened == "automatic" and first.opened == ""
        await router.cancel()
        await router.open("concrete", DictationConfig(model_id="first"))
        assert first.opened == "concrete"
        await router.cancel()
        with pytest.raises(RuntimeError, match="not preloaded"):
            await router.open("missing", DictationConfig(model_id="missing"))

    asyncio.run(scenario())


def test_manifest_router_reloads_profile_route_at_idle_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import classscribe_dictationd.runtime as runtime

    workers = tmp_path / "workers"
    workers.mkdir()
    transports: list[socket.socket] = []
    socket_paths: dict[str, Path] = {}
    for model_id in ("default", "japanese"):
        path = workers / f"{model_id}.sock"
        transport = socket.socket(socket.AF_UNIX)
        transport.bind(str(path))
        path.chmod(0o600)
        transports.append(transport)
        socket_paths[model_id] = path

    manifest_path = tmp_path / "resident-workers.json"
    manifest = {
        "schema_version": 1,
        "protocol_version": 1,
        "ready": True,
        "default_model_id": "default",
        "routes": [
            {
                "model_id": model_id,
                "model_revision": character * 40,
                "socket_path": str(socket_paths[model_id]),
            }
            for model_id, character in (("default", "a"), ("japanese", "b"))
        ],
        "profile_routes": {
            "ibus.ja.fast": "japanese",
            "ibus.auto.fast": "japanese",
        },
        "vad_socket": str(socket_paths["default"]),
        "lid_socket": None,
        "accuracy_socket": None,
        "accuracy_routes": {},
        "errors": [],
        "generated_at": "2026-09-04T00:00:00+00:00",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    opened: list[tuple[str, str]] = []

    class FakeRecognizer(_Recognizer):
        def __init__(self, _socket_path: Path, *, model_id: str, **_kwargs: Any) -> None:
            super().__init__()
            self.model_id = model_id

        async def open(self, session_id: str, config: DictationConfig) -> None:
            opened.append((self.model_id, session_id))
            await super().open(session_id, config)

    monkeypatch.setattr(runtime, "RPCStreamingRecognizer", FakeRecognizer)

    async def scenario() -> None:
        router = ManifestRoutedStreamingRecognizer(manifest_path)
        assert router.available_models() == ("default", "japanese")
        await router.open(
            "first",
            DictationConfig(
                language=DictationLanguage.JAPANESE,
                confirmation=ConfirmationMode.FAST,
            ),
        )
        await router.cancel()
        cast(dict[str, str], manifest["profile_routes"])["ibus.ja.fast"] = "default"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path.chmod(0o600)
        await router.open(
            "second",
            DictationConfig(
                language=DictationLanguage.JAPANESE,
                confirmation=ConfirmationMode.FAST,
            ),
        )
        await router.cancel()
        await router.open(
            "third",
            DictationConfig(
                language=DictationLanguage.AUTO_MIXED,
                confirmation=ConfirmationMode.FAST,
            ),
        )
        await router.cancel()

    try:
        asyncio.run(scenario())
    finally:
        for transport in transports:
            transport.close()
    assert opened == [
        ("japanese", "first"),
        ("default", "second"),
        ("japanese", "third"),
    ]


def test_accuracy_confirmation_uses_a_separate_preloaded_worker() -> None:
    class Client:
        def __init__(self, *, final_text: str = "") -> None:
            self.methods: list[str] = []
            self.final_text = final_text

        async def call(self, request: Any) -> RPCResponse:
            self.methods.append(request.method)
            text = self.final_text if request.method == "transcribe_pcm" else ""
            start = int(request.params.get("core_start_sample", 0))
            end = int(request.params.get("end_sample", start + 1))
            return RPCResponse(
                request_id=request.request_id,
                job_id=request.job_id,
                ok=True,
                model_id="fixture",
                model_revision="f" * 40,
                normalized_text=text,
                language="ja",
                segments=(
                    {
                        "start_sample": start,
                        "end_sample": end,
                        "text": text,
                        "words": [
                            {
                                "text": text,
                                "start_sample": start,
                                "end_sample": end,
                                "stable": True,
                            }
                        ],
                    },
                )
                if text
                else (),
                result={"candidates": [text]} if text else {},
            )

    async def scenario() -> None:
        recognizer = RPCStreamingRecognizer(
            Path("/tmp/primary.sock"),
            accuracy_socket_path=Path("/tmp/accuracy.sock"),
        )
        primary = Client()
        accuracy = Client(final_text="高精度")
        recognizer.client = cast(Any, primary)
        recognizer.accuracy_client = cast(Any, accuracy)
        config = DictationConfig(
            language=DictationLanguage.JAPANESE,
            confirmation=ConfirmationMode.ACCURACY,
        )
        await recognizer.open("session", config)
        await recognizer.push(b"\0\0" * 320, absolute_start_sample=0)
        tokens, candidates = await recognizer.finalize(
            DictationChunk(0, 0, 0, 320, "release"), config=config, context=()
        )
        assert "".join(item.text for item in tokens) == "高精度"
        assert candidates == ("高精度",)
        assert accuracy.methods == ["transcribe_pcm"]
        assert "stream_flush" not in primary.methods

    asyncio.run(scenario())


def test_stable_prefix_does_not_skip_unstable_word() -> None:
    prefix = StablePrefix(stable_after_seconds=1)
    words = (TimedToken("A", 0, 100, stable=False), TimedToken("B", 100, 200))
    assert prefix.update(words, absolute_sample=32000) == ()
    assert prefix.finalize(()) == words


def test_vad_rejects_missing_voiced_instead_of_silent_audio() -> None:
    from classscribe_dictationd.runtime import RPCFrameVAD

    class Client:
        async def call(self, request: RPCRequest) -> RPCResponse:
            return RPCResponse(
                request.request_id,
                request.job_id,
                True,
                "vad",
                "a" * 40,
                result={"accepted_samples": 320},
            )

    vad = RPCFrameVAD(Path("/unused"))
    vad.client = cast(Any, Client())

    async def scenario() -> None:
        await vad.open("s")
        with pytest.raises(RuntimeError, match="boolean voiced"):
            await vad.voiced(b"\0" * 640)

    asyncio.run(scenario())


def test_failed_audio_queue_and_late_callback_do_not_enter_next_session() -> None:
    class Recognizer(_Recognizer):
        def __init__(self) -> None:
            super().__init__()
            self.fail_once = True
            self.frames: list[bytes] = []

        async def push(self, frame: bytes, *, absolute_start_sample: int) -> tuple[TimedToken, ...]:
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("ASR failed")
            self.frames.append(frame)
            return ()

    async def scenario() -> None:
        source = _AudioSource()
        recognizer = Recognizer()
        service = DictationService(DictationController(recognizer), source, _Lease())
        await service.start()
        old_callback = source.callback
        for _ in range(3):
            old_callback(b"\x01\x00" * 320)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.wait_for(service._queue.join(), 1)
        await service.start()
        old_callback(b"\x02\x00" * 320)
        source.callback(b"\x03\x00" * 320)
        await asyncio.sleep(0)
        await service.stop()
        assert recognizer.frames == [b"\x03\x00" * 320]

    asyncio.run(scenario())


def test_begin_acknowledges_arming_and_release_cancels_slow_prepare(tmp_path: Path) -> None:
    from classscribe_dictationd.daemon import DictationControlServer

    class SlowLease(_Lease):
        async def begin(self, session_id: str) -> None:
            self.begun.append(session_id)
            await asyncio.Event().wait()

    async def scenario() -> None:
        source = _AudioSource()
        lease = SlowLease()
        service = DictationService(DictationController(_Recognizer()), source, lease)
        server = DictationControlServer(tmp_path / "control.sock", service)
        response = await asyncio.wait_for(server._dispatch("begin", {}), 0.1)
        assert cast(dict[str, Any], response["current"])["state"] == "arming"
        await asyncio.sleep(0)
        await asyncio.wait_for(server._dispatch("release", {}), 0.1)
        assert service.controller.current.state is DictationState.IDLE
        assert lease.begun == lease.ended
        assert service._consumer is None

    asyncio.run(scenario())


def test_begin_timeout_does_not_forget_held_key_release() -> None:
    class TimeoutControl(_Control):
        def __init__(self) -> None:
            super().__init__()
            self.actions: list[str] = []

        def request(
            self, action: str, params: Mapping[str, object] | None = None
        ) -> dict[str, Any]:
            self.actions.append(action)
            if action == "begin":
                raise TimeoutError()
            return super().request(action, params)

    client = TimeoutControl()
    engine = EngineController(client, _Bridge())
    engine.handle_key(ACTIVATION_KEY)
    engine.handle_key(ACTIVATION_KEY, released=True)
    assert "release" in client.actions


def test_new_engine_syncs_cursor_without_replaying_history() -> None:
    class HistoricalControl(_Control):
        def __init__(self) -> None:
            super().__init__()
            self.history = [
                self._status(DictationState.ARMING),
                self._status(DictationState.COMMITTING, commit_text="old"),
                self._status(DictationState.IDLE),
            ]

        def request(
            self, action: str, params: Mapping[str, object] | None = None
        ) -> dict[str, Any]:
            response = super().request(action, params)
            if action == "status":
                response["events"] = self.history
            return response

    client = HistoricalControl()
    bridge = _Bridge()
    engine = EngineController(client, bridge)
    engine.poll()
    assert bridge.commits == []
    engine.handle_key(ACTIVATION_KEY)
    engine.handle_key(ACTIVATION_KEY, released=True)
    assert bridge.commits == ["done"]
    engine.focus_out()
    engine._apply(
        DictationStatus("session", 999, DictationState.COMMITTING, commit_text="wrong context")
    )
    assert bridge.commits == ["done"]


def test_daemon_generation_change_resets_old_cursor_without_replay() -> None:
    client = _Control()
    client.revision = 500
    client.current = client._status(DictationState.IDLE)
    bridge = _Bridge()
    engine = EngineController(client, bridge)
    engine.poll()
    assert engine.revision == 501
    client.instance_id = "restarted"
    client.revision = 0
    client.current = client._status(DictationState.IDLE)
    engine.poll()
    assert engine.revision == 1
    engine.handle_key(ACTIVATION_KEY)
    engine.handle_key(ACTIVATION_KEY, released=True)
    assert bridge.commits == ["done"]


@pytest.mark.parametrize("slow_prepare", [False, True])
def test_abandoned_core_lease_expires_even_during_prepare(
    tmp_path: Path, slow_prepare: bool
) -> None:
    async def scenario() -> None:
        manager = GPULeaseManager()
        finished = asyncio.Event()

        async def begin() -> None:
            if slow_prepare:
                await asyncio.Event().wait()

        server = GPULeaseIPCServer(
            tmp_path / "lease.sock",
            manager,
            lease_timeout_seconds=0.06,
            on_begin=begin,
            on_end=finished.set,
        )
        await server.start()
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write(b'{"action":"begin_dictation","session_id":"crashed"}\n')
        await writer.drain()
        if not slow_prepare:
            assert json.loads(await reader.readline())["ok"]
        writer.close()
        await writer.wait_closed()
        try:
            await asyncio.wait_for(finished.wait(), 1)
            assert not manager.dictation_active
            assert not server._sessions
        finally:
            await server.close()

    asyncio.run(scenario())


def test_failed_lease_end_is_retained_and_retried() -> None:
    class FlakyLease(_Lease):
        failures = 1

        async def end(self, session_id: str) -> None:
            if self.failures:
                self.failures -= 1
                raise ConnectionError("core unavailable")
            await super().end(session_id)

    async def scenario() -> None:
        lease = FlakyLease()
        service = DictationService(DictationController(_Recognizer()), _AudioSource(), lease)
        await service.start()
        await service.cancel()
        assert service._pending_lease_ends == set(lease.begun)
        await service._retry_lease_ends()
        assert not service._pending_lease_ends
        assert lease.begun == lease.ended
        await service.close()

    asyncio.run(scenario())


def test_scheduler_transmits_requested_language_profile_and_model(tmp_path: Path) -> None:
    from classscribe.scheduler import GPULeaseIPCServer, GPULeaseManager
    from classscribe_dictationd.daemon import SchedulerLeaseClient

    observed: list[dict[str, str]] = []

    async def scenario() -> None:
        server = GPULeaseIPCServer(
            tmp_path / "lease.sock",
            GPULeaseManager(),
            on_prepare=lambda options: observed.append(dict(options)),
        )
        await server.start()
        client = SchedulerLeaseClient(server.socket_path)
        try:
            config = DictationConfig(
                language=DictationLanguage.ENGLISH,
                confirmation=ConfirmationMode.FAST,
                model_id="english-model",
            )
            await client.begin("configured", config=config)
            assert observed == [{"language": "en", "profile": "fast", "model_id": "english-model"}]
            await client.end("configured")
        finally:
            await server.close()

    asyncio.run(scenario())


def test_unloaded_models_remain_selectable_from_safe_catalog(tmp_path: Path) -> None:
    path = tmp_path / "resident-workers.json"
    catalog = tmp_path / "resident-model-catalog.json"
    catalog.write_text(json.dumps({"schema_version": 1, "models": ["qwen", "nemotron"]}))
    catalog.chmod(0o600)
    recognizer = ManifestRoutedStreamingRecognizer(path)
    assert recognizer.available_models() == ("qwen", "nemotron")
    catalog.chmod(0o666)
    assert recognizer.available_models() == ()
