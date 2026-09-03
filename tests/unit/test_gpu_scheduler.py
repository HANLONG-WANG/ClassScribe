from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from classscribe.models.registry import InstallationStatus, ModelEntry, ModelRegistry, load_registry
from classscribe.scheduler.candidates import run_candidates_sequentially
from classscribe.scheduler.gpu import (
    DecodeAttempt,
    GPULeaseManager,
    GPUMemoryMonitor,
    GPUMemorySample,
    OOMRecoveryChain,
    OOMRecoveryExhausted,
    ResidencyPlanner,
    ResidencyPolicy,
    run_with_oom_recovery,
)
from classscribe_protocol import Priority

ROOT = Path(__file__).resolve().parents[2]


def test_dictation_preempts_classroom_only_at_segment_boundary() -> None:
    async def scenario() -> None:
        manager = GPULeaseManager()
        classroom = await manager.acquire(
            request_id="class-1",
            owner="classroom",
            model_id="class-model",
            priority=Priority.CLASSROOM_PRIMARY,
        )
        await manager.begin_dictation()
        pending = asyncio.create_task(
            manager.acquire(
                request_id="ibus-1",
                owner="ibus",
                model_id="stream-model",
                priority=Priority.DICTATION,
            )
        )
        await asyncio.sleep(0)
        assert not pending.done()
        assert manager.current == classroom
        assert await manager.segment_boundary(classroom)
        ibus = await pending
        assert ibus.owner == "ibus"
        await manager.release(ibus)
        await manager.end_dictation()

    asyncio.run(scenario())


def test_dictation_blocks_new_classroom_dispatch_and_same_model_is_reused() -> None:
    async def scenario() -> None:
        manager = GPULeaseManager()
        first = await manager.acquire(
            request_id="first",
            owner="interactive",
            model_id="shared",
            priority=Priority.INTERACTIVE,
        )
        await manager.release(first)
        await manager.begin_dictation()
        classroom = asyncio.create_task(
            manager.acquire(
                request_id="class",
                owner="classroom",
                model_id="shared",
                priority=Priority.CLASSROOM_PRIMARY,
            )
        )
        await asyncio.sleep(0)
        assert not classroom.done()
        ibus = await manager.acquire(
            request_id="ibus",
            owner="ibus",
            model_id="shared",
            priority=Priority.DICTATION,
        )
        assert ibus.reused_resident_model
        await manager.release(ibus)
        await manager.end_dictation()
        resumed = await classroom
        assert resumed.model_id == "shared"
        await manager.release(resumed, keep_model_resident=False)

    asyncio.run(scenario())


def _installed_registry() -> ModelRegistry:
    registry = load_registry(ROOT / "config/model-registry.v1.yaml")
    models = tuple(
        item.model_copy(
            update={
                "installation": InstallationStatus(
                    state="installed",
                    artifact_sha256="c" * 64,
                    local_revision=item.revision,
                )
            }
        )
        for item in registry.models
    )
    return registry.model_copy(update={"models": models})


def test_residency_policies_use_registry_capabilities_and_memory_margin() -> None:
    registry = _installed_registry()
    planner = ResidencyPlanner()
    fast = planner.plan(
        registry,
        policy=ResidencyPolicy.FAST,
        streaming_profile="ibus.ja.fast",
        final_profile="ibus.ja.accuracy",
        total_vram_mb=12_288,
    )
    assert fast.resident_model_ids == ("nemotron_3_5_asr_streaming_0_6b",)
    balanced = planner.plan(
        registry,
        policy=ResidencyPolicy.BALANCED,
        streaming_profile="ibus.ja.balanced",
        final_profile="ibus.ja.accuracy",
        total_vram_mb=12_288,
    )
    assert balanced.resident_model_ids == ("qwen3_asr_1_7b",)
    accuracy = planner.plan(
        registry,
        policy=ResidencyPolicy.ACCURACY,
        streaming_profile="ibus.ja.fast",
        final_profile="ibus.ja.accuracy",
        total_vram_mb=12_288,
    )
    assert accuracy.hot_switch_required
    assert accuracy.estimated_vram_mb < 12_288


def test_registry_driven_candidates_run_one_at_a_time_in_rank_order() -> None:
    async def scenario() -> None:
        registry = _installed_registry()
        candidates = registry.candidates(
            "classroom.en", language="en", task="asr", mode="batch", installed_only=True
        )[:3]
        active = 0
        peak_active = 0
        order: list[str] = []

        async def operation(candidate: ModelEntry) -> str:
            nonlocal active, peak_active
            model_id = candidate.id
            active += 1
            peak_active = max(peak_active, active)
            order.append(model_id)
            await asyncio.sleep(0)
            active -= 1
            return model_id

        outcomes = await run_candidates_sequentially(candidates, operation)
        assert peak_active == 1
        assert order == [item.id for item in candidates]
        assert [item.value for item in outcomes] == order
        chain = OOMRecoveryChain.from_registry(
            registry,
            profile="classroom.en",
            current_model_id=candidates[0].id,
        )
        assert all(
            registry.model(model_id).resources.estimated_vram_mb
            < candidates[0].resources.estimated_vram_mb
            for model_id in chain.smaller_models
        )

    asyncio.run(scenario())


class _MemoryBackend:
    def __init__(self) -> None:
        self.used = 1000

    def sample(self) -> GPUMemorySample:
        self.used += 100
        return GPUMemorySample(1.0, self.used, 12_288, 12_288 - self.used)


def test_oom_recovery_is_finite_ordered_and_never_repeats_parameters() -> None:
    async def scenario() -> None:
        initial = DecodeAttempt("large", 8, 64, "eager", "float32")
        chain = OOMRecoveryChain(
            smaller_models=("small",), verified_dtypes=("bfloat16",), minimum_window_seconds=8
        )
        monitor = GPUMemoryMonitor(_MemoryBackend())
        attempts: list[DecodeAttempt] = []
        cleanups = 0

        async def cleanup() -> None:
            nonlocal cleanups
            cleanups += 1

        async def operation(attempt: DecodeAttempt) -> str:
            attempts.append(attempt)
            if attempt.model_id != "small":
                raise RuntimeError("CUDA out of memory")
            return "ok"

        result, final = await run_with_oom_recovery(
            "request-oom",
            initial,
            chain,
            operation,
            monitor=monitor,
            is_oom=lambda exc: "out of memory" in str(exc),
            cleanup=cleanup,
        )
        assert result == "ok"
        assert final.model_id == "small"
        assert cleanups == 1
        assert len({item.fingerprint() for item in attempts}) == len(attempts)
        assert len(monitor.oom_history) == len(attempts) - 1
        assert [item.batch_size for item in attempts[:3]] == [8, 8, 1]
        assert monitor.peak_used_mb > 0

    asyncio.run(scenario())


def test_oom_recovery_exhaustion_records_failure() -> None:
    async def scenario() -> None:
        monitor = GPUMemoryMonitor(_MemoryBackend())

        async def fail(_attempt: DecodeAttempt) -> None:
            raise MemoryError("out of memory")

        with pytest.raises(OOMRecoveryExhausted, match="unique OOM attempts"):
            await run_with_oom_recovery(
                "never-loop",
                DecodeAttempt("only", 1, 8, "sdpa", "float16"),
                OOMRecoveryChain(verified_dtypes=("float16",)),
                fail,
                monitor=monitor,
                is_oom=lambda exc: isinstance(exc, MemoryError),
            )
        assert len(monitor.oom_history) == 2

    asyncio.run(scenario())
