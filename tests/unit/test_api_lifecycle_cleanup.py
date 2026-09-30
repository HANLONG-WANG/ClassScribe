from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from classscribe.api.app import create_app
from classscribe.paths import AppPaths


class Resource:
    def __init__(self, name: str, events: list[str], failures: set[str]) -> None:
        self.name = name
        self.events = events
        self.failures = failures

    async def start(self) -> None:
        self.events.append(f"{self.name}-start")

    def start_recovered_jobs(self) -> None:
        self.events.append("pipeline-start")

    async def close(self) -> None:
        self.events.append(self.name)
        if self.name in self.failures:
            raise RuntimeError(self.name)


class Service:
    def __init__(
        self,
        events: list[str],
        failures: set[str],
        cleanup: Callable[[], None] | None = None,
    ) -> None:
        self.pipeline = Resource("pipeline", events, failures)
        self.resident_workers = Resource("resident", events, failures)
        self.background = Resource("background", events, failures)
        self.cleanup_retained_derived = cleanup

    async def close_background_jobs(self) -> None:
        await self.background.close()


def application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: Service,
    events: list[str],
    failures: set[str],
) -> Any:
    import classscribe.api.app as module

    class Lease(Resource):
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            super().__init__("lease", events, failures)

    monkeypatch.setattr(module, "GPULeaseIPCServer", Lease)
    paths = AppPaths.from_environment(
        {"XDG_RUNTIME_DIR": str(tmp_path / "runtime")}, home=tmp_path / "home"
    )
    return create_app(service=cast(Any, service), enable_scheduler_ipc=True, runtime_paths=paths)


@pytest.mark.parametrize("failure", ["background", "pipeline", "lease", "resident"])
def test_shutdown_error_does_not_skip_other_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    events: list[str] = []
    failures = {failure}
    app = application(tmp_path, monkeypatch, Service(events, failures), events, failures)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match=failure):
            async with app.router.lifespan_context(app):
                pass

    asyncio.run(scenario())
    assert events[-4:] == ["background", "pipeline", "lease", "resident"]


def test_primary_failure_and_multiple_cleanup_errors_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    failures = {"background", "pipeline"}
    app = application(tmp_path, monkeypatch, Service(events, failures), events, failures)

    async def scenario() -> None:
        with pytest.raises(BaseExceptionGroup) as raised:
            async with app.router.lifespan_context(app):
                raise ValueError("primary")
        assert [str(error) for error in raised.value.exceptions] == [
            "primary",
            "background",
            "pipeline",
        ]

    asyncio.run(scenario())
    assert events[-4:] == ["background", "pipeline", "lease", "resident"]


def test_shutdown_waits_for_the_retention_thread_before_closing_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, finish = threading.Event(), threading.Event()
    events: list[str] = []

    def cleanup() -> None:
        started.set()
        if not finish.wait(3):
            raise AssertionError("test cleanup was not released")
        events.append("retention-finished")

    app = application(tmp_path, monkeypatch, Service(events, set(), cleanup), events, set())

    async def scenario() -> None:
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        assert await asyncio.to_thread(started.wait, 1)
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        try:
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert "background" not in events
        finally:
            finish.set()
            await shutdown

    asyncio.run(scenario())
    assert events[-5:] == ["retention-finished", "background", "pipeline", "lease", "resident"]


def test_ordinary_retention_error_is_logged_and_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[str] = []
    calls: list[int] = []
    original_sleep = asyncio.sleep

    def cleanup() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("private path must not enter log")

    async def sleep(delay: float) -> None:
        if delay == 3600 and len(calls) < 2:
            await original_sleep(0)
        elif delay == 3600:
            await asyncio.Event().wait()
        else:
            await original_sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    app = application(tmp_path, monkeypatch, Service(events, set(), cleanup), events, set())

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            async with asyncio.timeout(1):
                while len(calls) < 2:
                    await original_sleep(0)

    asyncio.run(scenario())
    assert len(calls) >= 2
    assert "retention cleanup failed (OSError)" in caplog.text
    assert "private path" not in caplog.text
