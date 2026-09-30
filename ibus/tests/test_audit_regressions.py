from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
for package in ("protocol/python", "ibus/dictationd", "ibus/engine"):
    sys.path.insert(0, str(ROOT / package))

from classscribe_dictationd.daemon import DictationService  # noqa: E402
from classscribe_dictationd.runtime import GStreamerPipeWireSource  # noqa: E402
from classscribe_dictationd.session import DictationController  # noqa: E402
from classscribe_dictationd.streaming import RollingContext  # noqa: E402
from classscribe_ibus_engine.runtime import run_ibus  # noqa: E402
from classscribe_protocol import DictationState  # noqa: E402


@pytest.mark.parametrize("cancelled", [False, True])
def test_cancel_failure_always_returns_idle_and_releases_lease(cancelled: bool) -> None:
    class Recognizer:
        async def open(self, *_args: object) -> None:
            pass

        async def cancel(self) -> None:
            if cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError("worker unavailable")

    class Source:
        def start(self, *_args: object) -> None:
            pass

        def stop(self) -> None:
            pass

    class Lease:
        def __init__(self) -> None:
            self.active: set[str] = set()

        async def begin(self, session_id: str) -> None:
            self.active.add(session_id)

        async def end(self, session_id: str) -> None:
            self.active.discard(session_id)

    async def scenario() -> None:
        lease = Lease()
        controller = DictationController(cast(Any, Recognizer()))
        service = DictationService(controller, Source(), cast(Any, lease))
        await service.start()
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await service.cancel()
        else:
            status = await service.cancel()
            assert "stream close failed" in status.message
        assert controller.current.state is DictationState.IDLE
        assert controller.session_id is None
        assert not lease.active
        assert service._consumer is None
        restarted = await service.start()
        assert restarted.state is DictationState.LISTENING
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await service.cancel()
        else:
            await service.cancel()
        assert not lease.active

    asyncio.run(scenario())


def test_pipewire_bus_errors_are_delivered_without_a_glib_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Bus:
        def __init__(self) -> None:
            self.handler: Any = None

        def set_sync_handler(self, handler: Any, _data: object) -> None:
            self.handler = handler

    bus = Bus()

    class Pipeline:
        def get_by_name(self, _name: str) -> Any:
            return types.SimpleNamespace(connect=lambda *_args: None)

        def get_bus(self) -> Bus:
            return bus

        def set_state(self, _state: object) -> int:
            return 1

    gst = types.SimpleNamespace(
        init=lambda _args: None,
        parse_launch=lambda _text: Pipeline(),
        State=types.SimpleNamespace(PLAYING=1, NULL=0),
        StateChangeReturn=types.SimpleNamespace(FAILURE=-1),
        MessageType=types.SimpleNamespace(ERROR=1, EOS=2),
        BusSyncReply=types.SimpleNamespace(DROP=0),
    )
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", types.SimpleNamespace(Gst=gst))
    source = GStreamerPipeWireSource()
    errors: list[Exception] = []
    source.start(lambda _frame: None, errors.append)
    assert bus.handler is not None
    assert bus.handler(bus, types.SimpleNamespace(type=2), None) == 0
    assert "ended" in str(errors[0])
    bus.handler(
        bus,
        types.SimpleNamespace(type=1, parse_error=lambda: (RuntimeError("disconnected"), "")),
        None,
    )
    assert "disconnected" in str(errors[1])
    source.stop()
    assert bus.handler is None
    assert source._pipeline is None


def test_destroyed_ibus_engine_removes_its_poll_source(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: dict[str, Any] = {}
    removed: list[int] = []

    class Engine:
        def __init__(self, **_kwargs: object) -> None:
            self.destroyed = False

        def do_destroy(self) -> None:
            self.destroyed = True

        def update_preedit_text(self, *_args: object) -> None:
            pass

        def update_lookup_table(self, *_args: object) -> None:
            pass

    class Factory:
        def add_engine(self, name: str, engine: Any) -> None:
            registered[name] = engine

    ibus = types.SimpleNamespace(
        init=lambda: None,
        Engine=Engine,
        Text=types.SimpleNamespace(new_from_string=lambda value: value),
        LookupTable=types.SimpleNamespace(new=lambda *_args: None),
        Bus=lambda: types.SimpleNamespace(
            is_connected=lambda: True, get_connection=lambda: None, request_name=lambda *_args: None
        ),
        Factory=types.SimpleNamespace(new=lambda _connection: Factory()),
    )
    glib = types.SimpleNamespace(
        timeout_add=lambda *_args: 42,
        source_remove=removed.append,
        MainLoop=lambda: types.SimpleNamespace(run=lambda: None),
    )
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", types.SimpleNamespace(GLib=glib, IBus=ibus))
    run_ibus(Path("/unused"))
    cls = registered["classscribe-voice"]
    monkeypatch.setattr(cls, "_register_properties", lambda *_args: None)
    engine = cls(None, "/test")
    engine.do_destroy()
    assert removed == [42]
    assert engine.destroyed
    assert engine._poll_source == 0
    assert not engine.controller._focused


def test_five_context_chunks_obey_total_character_budget() -> None:
    context = RollingContext(5)
    for index in range(5):
        context.add(str(index) * 600)
    assert context.values() == tuple(str(index) * 600 for index in range(1, 5))
    assert sum(map(len, context.values())) == 2400
    context.add("latest" * 1000)
    assert context.values() == (("latest" * 1000)[-2400:],)
