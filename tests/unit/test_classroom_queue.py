from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from classscribe.classroom import queue
from classscribe.classroom.pipeline import ClassroomPipeline
from classscribe.contracts import LanguageMode
from classscribe.db.models import Job, JobCheckpoint, JobStatus, Recording
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def add(factory: sessionmaker[Session], order: int) -> str:
    with factory.begin() as session:
        job = Job(
            recording=Recording(
                source_name="lecture.wav",
                source_path=str(uuid4()),
                source_sha256="a" * 64,
                duration_samples=16000,
                sample_rate=16000,
                channels=1,
            ),
            language_mode=LanguageMode.JAPANESE,
            profile_id="balanced",
            queue_order=order,
        )
        session.add(job)
        session.flush()
        return job.id


def test_fifo_waits_for_cleanup_and_continues_after_failure(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    first = add(factory, 1)
    remaining = [add(factory, order) for order in range(2, 11)]
    second = remaining[0]
    releasing, release = threading.Event(), threading.Event()
    calls: list[str] = []

    class Runner:
        def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
            calls.append(job.id)
            if job.id == first:
                raise ValueError("bad classroom")

        def close_job(self, job_id: str) -> None:
            if job_id == first:
                releasing.set()
                assert release.wait(5)

    pipeline = ClassroomPipeline(factory, Runner())

    async def scenario() -> None:
        pipeline.schedule(second)  # scheduling order cannot override persisted FIFO
        try:
            async with asyncio.timeout(3):
                while not releasing.is_set():
                    await asyncio.sleep(0.01)
            assert calls == [first]
            pipeline.schedule(second)
            assert len(pipeline._tasks) == 1
            release.set()
            await asyncio.wait_for(asyncio.gather(*tuple(pipeline._tasks)), 5)
            assert calls.index(second) == 3  # first exhausted its three attempts
            assert pipeline.snapshot(first)["status"] == "failed"
            assert list(dict.fromkeys(calls)) == [first, *remaining]
            assert all(
                pipeline.snapshot(identifier)["status"] == "completed" for identifier in remaining
            )
        finally:
            release.set()
            await pipeline.close()

    asyncio.run(scenario())


def test_paused_queue_survives_restart_and_manual_resume_goes_to_tail(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    first, second = add(factory, 1), add(factory, 2)
    with factory.begin() as session:
        queue.set_paused(session, True)
        session.get_one(Job, first).status = JobStatus.PAUSED
    seen: list[str] = []

    class Runner:
        def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
            seen.append(job.id)

    async def scenario() -> None:
        pipeline = ClassroomPipeline(factory, Runner())
        pipeline.start_recovered_jobs()
        await asyncio.gather(*tuple(pipeline._tasks))
        assert not seen
        pipeline.resume(first)
        with factory.begin() as session:
            assert (
                session.get_one(Job, first).queue_order > session.get_one(Job, second).queue_order
            )
            queue.set_paused(session, False)
        pipeline.schedule(first)
        await asyncio.wait_for(asyncio.gather(*tuple(pipeline._tasks)), 5)
        assert seen[0] == second
        await pipeline.close()

    asyncio.run(scenario())


def test_second_executor_cannot_claim_database(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    from classscribe.errors import ClassScribeError

    _, factory, _ = database

    class Runner:
        def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
            pass

    async def scenario() -> None:
        first, second = ClassroomPipeline(factory, Runner()), ClassroomPipeline(factory, Runner())
        first.start_recovered_jobs()
        try:
            with pytest.raises(ClassScribeError, match="another classroom executor"):
                second.start_recovered_jobs()
        finally:
            await first.close()

    asyncio.run(scenario())


def test_queue_pause_yields_current_job_and_resume_keeps_it_first(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, factory, _ = database
    first, second = add(factory, 1), add(factory, 2)
    entered, released = threading.Event(), threading.Event()
    calls: list[str] = []

    class Runner:
        def run(self, session: Session, job: Job, checkpoint: JobCheckpoint) -> None:
            calls.append(job.id)
            if len(calls) == 1:
                entered.set()
                assert released.wait(5)

    pipeline = ClassroomPipeline(factory, Runner())

    async def scenario() -> None:
        pipeline.schedule(first)
        try:
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            with factory.begin() as session:
                queue.set_paused(session, True)
            released.set()
            await asyncio.wait_for(asyncio.gather(*tuple(pipeline._tasks)), 5)
            assert calls == [first]
            assert pipeline.snapshot(first)["status"] == "pending"
            with factory.begin() as session:
                queue.set_paused(session, False)
            pipeline.schedule(second)
            await asyncio.wait_for(asyncio.gather(*tuple(pipeline._tasks)), 5)
            assert list(dict.fromkeys(calls)) == [first, second]
        finally:
            released.set()
            await pipeline.close()

    asyncio.run(scenario())
