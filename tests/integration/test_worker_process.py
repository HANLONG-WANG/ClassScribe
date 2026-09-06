from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from classscribe.models.worker_process import WorkerProcess, WorkerProcessSpec, worker_command
from classscribe_protocol import Priority, ProtocolError, RPCRequest

ROOT = Path(__file__).resolve().parents[2]
REVISION = "e" * 40
WORKERS = (
    "moss_td",
    "firered",
    "granite",
    "qwen",
    "ark",
    "moss_en",
    "nemotron",
    "pyannote",
    "funasr_experimental",
    "voxtral",
    "vibevoice",
)


def test_isolated_worker_process_load_health_unload_and_crash_survival(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        model_root = tmp_path / "model"
        model_root.mkdir()
        worker = WorkerProcess(
            WorkerProcessSpec(
                worker_id="qwen",
                command=worker_command(ROOT, "qwen"),
                socket_path=tmp_path / "runtime/qwen.sock",
                data_roots=(data_root,),
            )
        )
        await worker.start()
        try:
            health = await worker.call(
                RPCRequest("health", "job", 1000, Priority.BACKGROUND, "health", {})
            )
            assert health.ok
            loaded = await worker.call(
                RPCRequest(
                    "load",
                    "job",
                    1000,
                    Priority.CLASSROOM_PRIMARY,
                    "load",
                    {
                        "model_id": "qwen-test",
                        "model_revision": REVISION,
                        "model_path": str(model_root),
                    },
                )
            )
            assert loaded.ok
            unsupported = await worker.call(
                RPCRequest(
                    "batch",
                    "job",
                    1000,
                    Priority.CLASSROOM_PRIMARY,
                    "transcribe_batch",
                    {
                        "audio_path": str(data_root / "missing.wav"),
                        "start_sample": 0,
                        "end_sample": 16000,
                        "sample_rate": 16000,
                    },
                )
            )
            assert not unsupported.ok
            assert unsupported.error_code == "invalid_request"
            unloaded = await worker.call(
                RPCRequest("unload", "job", 1000, Priority.BACKGROUND, "unload", {})
            )
            assert unloaded.ok

            assert worker.process is not None
            worker.process.kill()
            await worker.process.wait()
            with pytest.raises(ProtocolError, match="not running"):
                await worker.call(
                    RPCRequest("after-crash", "job", 1000, Priority.BACKGROUND, "health", {})
                )
        finally:
            await worker.stop()

    asyncio.run(scenario())


def test_worker_command_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        worker_command(ROOT, "../qwen")


def test_worker_transport_supports_runtime_root_beyond_af_unix_path_limit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        socket_path = tmp_path / ("r" * 50) / "worker.sock"
        assert len(str(socket_path).encode()) > 107
        process = WorkerProcess(
            WorkerProcessSpec(
                worker_id="qwen",
                command=worker_command(ROOT, "qwen"),
                socket_path=socket_path,
                data_roots=(),
            )
        )
        await process.start()
        try:
            response = await process.call(
                RPCRequest("health", "long-runtime", 1000, Priority.BACKGROUND, "health", {})
            )
            assert response.ok
            assert socket_path.exists()
        finally:
            await process.stop()
        assert not socket_path.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("worker_id", WORKERS)
def test_every_worker_serves_versioned_rpc_lifecycle(worker_id: str, tmp_path: Path) -> None:
    async def scenario() -> None:
        model_root = tmp_path / "model"
        model_root.mkdir()
        if worker_id == "moss_td":
            (model_root / "config.json").write_text("{}", encoding="utf-8")
        elif worker_id == "pyannote":
            (model_root / "config.yaml").write_text("pipeline: local", encoding="utf-8")
        process = WorkerProcess(
            WorkerProcessSpec(
                worker_id=worker_id,
                command=worker_command(ROOT, worker_id),
                socket_path=tmp_path / "runtime/worker.sock",
                data_roots=(),
            )
        )
        await process.start()
        try:
            capabilities = await process.call(
                RPCRequest(
                    "capabilities",
                    "contract",
                    1000,
                    Priority.BACKGROUND,
                    "capabilities",
                    {},
                )
            )
            assert capabilities.ok
            assert capabilities.result["worker_id"] == worker_id
            assert capabilities.result["capabilities"]
            loaded = await process.call(
                RPCRequest(
                    "load",
                    "contract",
                    1000,
                    Priority.BACKGROUND,
                    "load",
                    {
                        "model_id": f"{worker_id}-contract",
                        "model_revision": REVISION,
                        "model_path": str(model_root),
                    },
                )
            )
            if worker_id in {"moss_td", "pyannote"}:
                assert not loaded.ok
                assert loaded.error_code == "model_load_failed"
            else:
                assert loaded.ok
                assert loaded.model_revision == REVISION
            cancelled = await process.cancel(
                request_id="cancel",
                target_request_id="not-running",
                job_id="contract",
            )
            assert cancelled.ok and cancelled.result["cancelled"] is False
            unloaded = await process.call(
                RPCRequest(
                    "unload",
                    "contract",
                    1000,
                    Priority.BACKGROUND,
                    "unload",
                    {},
                )
            )
            assert unloaded.ok
        finally:
            await process.stop()

    asyncio.run(scenario())
