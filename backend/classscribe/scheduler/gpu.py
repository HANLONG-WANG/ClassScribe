"""Single-GPU leases, residency planning, NVML telemetry, and finite OOM recovery."""

from __future__ import annotations

import asyncio
import ctypes
import heapq
import secrets
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from classscribe_protocol.messages import Priority

from classscribe.models.registry import ModelEntry, ModelRegistry


@dataclass(frozen=True, slots=True)
class GPULease:
    token: str
    request_id: str
    owner: str
    model_id: str
    priority: Priority
    acquired_monotonic: float
    reused_resident_model: bool


@dataclass(order=True, slots=True)
class _Waiter:
    priority: int
    sequence: int
    request_id: str
    owner: str
    model_id: str
    future: asyncio.Future[GPULease]


class GPULeaseManager:
    """Serialize GPU ownership and request cooperative classroom preemption."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._waiters: list[_Waiter] = []
        self._sequence = 0
        self._owner: GPULease | None = None
        self._dictation_sessions = 0
        self._preempt_requested = False
        self._resident_model_id: str | None = None

    @property
    def current(self) -> GPULease | None:
        return self._owner

    @property
    def resident_model_id(self) -> str | None:
        return self._resident_model_id

    @property
    def dictation_active(self) -> bool:
        return self._dictation_sessions > 0

    async def acquire(
        self,
        *,
        request_id: str,
        owner: str,
        model_id: str,
        priority: Priority,
        timeout_seconds: float | None = None,
    ) -> GPULease:
        if not request_id or not owner or not model_id:
            raise ValueError("lease identity fields must not be empty")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[GPULease] = loop.create_future()
        async with self._lock:
            self._sequence += 1
            heapq.heappush(
                self._waiters,
                _Waiter(priority.value, self._sequence, request_id, owner, model_id, future),
            )
            self._dispatch_locked()
        try:
            if timeout_seconds is None:
                return await future
            async with asyncio.timeout(timeout_seconds):
                return await future
        except BaseException:
            future.cancel()
            async with self._lock:
                self._dispatch_locked()
            raise

    async def release(self, lease: GPULease, *, keep_model_resident: bool = True) -> None:
        async with self._lock:
            if self._owner is None or self._owner.token != lease.token:
                raise ValueError("cannot release a lease not held by the caller")
            self._resident_model_id = lease.model_id if keep_model_resident else None
            self._owner = None
            self._preempt_requested = False
            self._dispatch_locked()

    async def begin_dictation(self) -> None:
        """Block new classroom work immediately; active work yields at its next segment."""

        async with self._lock:
            self._dictation_sessions += 1
            if self._owner is not None and self._owner.priority.value >= Priority.CLASSROOM_PRIMARY:
                self._preempt_requested = True

    async def end_dictation(self) -> None:
        async with self._lock:
            if self._dictation_sessions == 0:
                raise ValueError("dictation session counter is already zero")
            self._dictation_sessions -= 1
            if self._dictation_sessions == 0:
                self._dispatch_locked()

    async def segment_boundary(self, lease: GPULease) -> bool:
        """Return true and release only when a classroom owner reaches a safe boundary."""

        async with self._lock:
            if self._owner is None or self._owner.token != lease.token:
                raise ValueError("segment boundary reported by a non-owner")
            higher_waiter = any(
                not item.future.cancelled() and item.priority < lease.priority.value
                for item in self._waiters
            )
            should_yield = lease.priority.value >= Priority.CLASSROOM_PRIMARY and (
                self._preempt_requested or higher_waiter
            )
            if should_yield:
                self._resident_model_id = lease.model_id
                self._owner = None
                self._preempt_requested = False
                self._dispatch_locked()
            return should_yield

    def requires_unload(self, next_model_id: str, *, memory_preflight_ok: bool) -> bool:
        return not memory_preflight_ok or (
            self._resident_model_id is not None and self._resident_model_id != next_model_id
        )

    def _dispatch_locked(self) -> None:
        if self._owner is not None:
            return
        deferred: list[_Waiter] = []
        selected: _Waiter | None = None
        while self._waiters:
            waiter = heapq.heappop(self._waiters)
            if waiter.future.cancelled():
                continue
            if self._dictation_sessions and waiter.priority >= Priority.CLASSROOM_PRIMARY:
                deferred.append(waiter)
                continue
            selected = waiter
            break
        for waiter in deferred:
            heapq.heappush(self._waiters, waiter)
        if selected is None:
            return
        lease = GPULease(
            token=secrets.token_urlsafe(24),
            request_id=selected.request_id,
            owner=selected.owner,
            model_id=selected.model_id,
            priority=Priority(selected.priority),
            acquired_monotonic=time.monotonic(),
            reused_resident_model=self._resident_model_id == selected.model_id,
        )
        self._owner = lease
        self._resident_model_id = selected.model_id
        selected.future.set_result(lease)


class ResidencyPolicy(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURACY = "accuracy"


@dataclass(frozen=True, slots=True)
class ResidencyPlan:
    policy: ResidencyPolicy
    resident_model_ids: tuple[str, ...]
    hot_switch_required: bool
    estimated_vram_mb: int
    reason: str


class ResidencyPlanner:
    """Choose resident models solely from registry capabilities and rankings."""

    def plan(
        self,
        registry: ModelRegistry,
        *,
        policy: ResidencyPolicy,
        streaming_profile: str,
        final_profile: str,
        total_vram_mb: int,
    ) -> ResidencyPlan:
        streaming = registry.candidates(
            streaming_profile, task="streaming", mode="streaming", installed_only=True
        )
        if not streaming:
            return ResidencyPlan(policy, (), True, 0, "no installed streaming candidate")
        stream = streaming[0]
        if policy is ResidencyPolicy.FAST:
            return self._single(policy, stream, "keep only the fastest streaming candidate")
        combined = next(
            (item for item in streaming if "final_decode" in item.modes and "asr" in item.tasks),
            None,
        )
        if policy is ResidencyPolicy.BALANCED:
            if combined is None:
                return self._single(policy, stream, "final decode requires a hot switch")
            return self._single(policy, combined, "one model supports streaming and final decode")
        final = registry.candidates(final_profile, task="asr", installed_only=True)
        if not final:
            return self._single(policy, stream, "no installed final-decode candidate")
        final_model = final[0]
        models = (stream,) if stream.id == final_model.id else (stream, final_model)
        required = sum(item.resources.estimated_vram_mb for item in models)
        required += max(item.resources.safety_margin_mb for item in models)
        if required > total_vram_mb:
            return self._single(
                policy,
                stream,
                f"accuracy pair needs {required} MB; final model will be hot-switched",
            )
        return ResidencyPlan(
            policy,
            tuple(item.id for item in models),
            False,
            required,
            "streaming and language-specific final models fit with safety margin",
        )

    @staticmethod
    def _single(policy: ResidencyPolicy, model: ModelEntry, reason: str) -> ResidencyPlan:
        required = model.resources.estimated_vram_mb + model.resources.safety_margin_mb
        return ResidencyPlan(policy, (model.id,), True, required, reason)


@dataclass(frozen=True, slots=True)
class GPUMemorySample:
    timestamp_monotonic: float
    used_mb: int
    total_mb: int
    free_mb: int


class MemoryBackend(Protocol):
    def sample(self) -> GPUMemorySample | None: ...


class NVMLBackend:
    """Minimal ctypes binding: core remains free of CUDA/model Python packages."""

    def __init__(self, device_index: int = 0) -> None:
        self.device_index = device_index
        self._library: Any | None = None
        self._handle = ctypes.c_void_p()

    def sample(self) -> GPUMemorySample | None:
        try:
            library = self._load()
            memory = _NVMLMemory()
            if library.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(memory)) != 0:
                return None
            divisor = 1024 * 1024
            return GPUMemorySample(
                time.monotonic(),
                memory.used // divisor,
                memory.total // divisor,
                memory.free // divisor,
            )
        except (AttributeError, OSError, RuntimeError):
            return None

    def _load(self) -> Any:
        if self._library is not None:
            return self._library
        library = ctypes.CDLL("libnvidia-ml.so.1")
        if library.nvmlInit_v2() != 0:
            raise RuntimeError("NVML initialization failed")
        if (
            library.nvmlDeviceGetHandleByIndex_v2(self.device_index, ctypes.byref(self._handle))
            != 0
        ):
            raise RuntimeError("NVML device lookup failed")
        self._library = library
        return library


class _NVMLMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


@dataclass(frozen=True, slots=True)
class OOMEvent:
    request_id: str
    model_id: str
    attempt_fingerprint: tuple[object, ...]
    sample: GPUMemorySample | None
    detail: str


class GPUMemoryMonitor:
    def __init__(self, backend: MemoryBackend | None = None) -> None:
        self.backend = backend or NVMLBackend()
        self.peak_used_mb = 0
        self.last_sample: GPUMemorySample | None = None
        self.oom_history: list[OOMEvent] = []

    def sample(self) -> GPUMemorySample | None:
        current = self.backend.sample()
        if current is not None:
            self.last_sample = current
            self.peak_used_mb = max(self.peak_used_mb, current.used_mb)
        return current

    def record_oom(self, request_id: str, attempt: DecodeAttempt, detail: str) -> None:
        self.oom_history.append(
            OOMEvent(request_id, attempt.model_id, attempt.fingerprint(), self.sample(), detail)
        )


class RecoveryStep(StrEnum):
    CLEANUP = "cleanup_exited_workers_and_cuda_cache"
    BATCH_ONE = "batch_size_one"
    SHORTER_WINDOW = "shorter_window"
    LOW_MEMORY_ATTENTION = "low_memory_attention"
    LOWER_PRECISION = "bf16_fp16_or_verified_quantization"
    SMALLER_MODEL = "smaller_model"


@dataclass(frozen=True, slots=True)
class DecodeAttempt:
    model_id: str
    batch_size: int
    window_seconds: int
    attention_backend: str
    dtype: str
    quantization: str | None = None
    cleanup_generation: int = 0
    completed_steps: tuple[RecoveryStep, ...] = ()

    def fingerprint(self) -> tuple[object, ...]:
        return (
            self.model_id,
            self.batch_size,
            self.window_seconds,
            self.attention_backend,
            self.dtype,
            self.quantization,
            self.cleanup_generation,
        )


class OOMRecoveryExhausted(RuntimeError):
    pass


class OOMRecoveryChain:
    """Advance through the mandated recovery chain without repeating parameters."""

    def __init__(
        self,
        *,
        smaller_models: Sequence[str] = (),
        verified_dtypes: Sequence[str] = ("bfloat16", "float16"),
        verified_quantizations: Sequence[str] = (),
        minimum_window_seconds: int = 8,
    ) -> None:
        self.smaller_models = tuple(smaller_models)
        self.verified_dtypes = tuple(verified_dtypes)
        self.verified_quantizations = tuple(verified_quantizations)
        self.minimum_window_seconds = minimum_window_seconds

    @classmethod
    def from_registry(
        cls,
        registry: ModelRegistry,
        *,
        profile: str,
        current_model_id: str,
        verified_dtypes: Sequence[str] = ("bfloat16", "float16"),
        verified_quantizations: Sequence[str] = (),
        minimum_window_seconds: int = 8,
    ) -> OOMRecoveryChain:
        current = registry.model(current_model_id)
        smaller = tuple(
            item.id
            for item in registry.candidates(profile, installed_only=True)
            if item.id != current_model_id
            and item.resources.estimated_vram_mb < current.resources.estimated_vram_mb
        )
        return cls(
            smaller_models=smaller,
            verified_dtypes=verified_dtypes,
            verified_quantizations=verified_quantizations,
            minimum_window_seconds=minimum_window_seconds,
        )

    def next(self, attempt: DecodeAttempt) -> DecodeAttempt:
        remaining = [item for item in RecoveryStep if item not in attempt.completed_steps]
        for step in remaining:
            updated = self._apply(step, attempt)
            completed = (*attempt.completed_steps, step)
            if updated is None:
                attempt = replace(attempt, completed_steps=completed)
                continue
            updated = replace(updated, completed_steps=completed)
            if updated.fingerprint() != attempt.fingerprint():
                return updated
            attempt = updated
        raise OOMRecoveryExhausted("finite OOM recovery chain exhausted")

    def _apply(self, step: RecoveryStep, attempt: DecodeAttempt) -> DecodeAttempt | None:
        if step is RecoveryStep.CLEANUP:
            return replace(attempt, cleanup_generation=attempt.cleanup_generation + 1)
        if step is RecoveryStep.BATCH_ONE:
            return replace(attempt, batch_size=1) if attempt.batch_size != 1 else None
        if step is RecoveryStep.SHORTER_WINDOW:
            window = max(self.minimum_window_seconds, attempt.window_seconds // 2)
            return (
                replace(attempt, window_seconds=window) if window < attempt.window_seconds else None
            )
        if step is RecoveryStep.LOW_MEMORY_ATTENTION:
            return (
                replace(attempt, attention_backend="sdpa")
                if attempt.attention_backend != "sdpa"
                else None
            )
        if step is RecoveryStep.LOWER_PRECISION:
            for dtype in self.verified_dtypes:
                if dtype != attempt.dtype:
                    return replace(attempt, dtype=dtype)
            for quantization in self.verified_quantizations:
                if quantization != attempt.quantization:
                    return replace(attempt, quantization=quantization)
            return None
        if step is RecoveryStep.SMALLER_MODEL:
            for model_id in self.smaller_models:
                if model_id != attempt.model_id:
                    return replace(attempt, model_id=model_id)
        return None


async def run_with_oom_recovery[ResultT](
    request_id: str,
    initial: DecodeAttempt,
    chain: OOMRecoveryChain,
    operation: Callable[[DecodeAttempt], Awaitable[ResultT]],
    *,
    monitor: GPUMemoryMonitor,
    is_oom: Callable[[BaseException], bool],
    cleanup: Callable[[], Awaitable[None]] | None = None,
) -> tuple[ResultT, DecodeAttempt]:
    attempt = initial
    seen: set[tuple[object, ...]] = set()
    while True:
        fingerprint = attempt.fingerprint()
        if fingerprint in seen:
            raise OOMRecoveryExhausted("OOM recovery attempted identical parameters")
        seen.add(fingerprint)
        monitor.sample()
        try:
            return await operation(attempt), attempt
        except BaseException as exc:
            if not is_oom(exc):
                raise
            monitor.record_oom(request_id, attempt, str(exc))
            try:
                next_attempt = chain.next(attempt)
            except OOMRecoveryExhausted as exhausted:
                raise OOMRecoveryExhausted(
                    f"{request_id} failed after {len(seen)} unique OOM attempts"
                ) from exhausted
            if (
                next_attempt.cleanup_generation != attempt.cleanup_generation
                and cleanup is not None
            ):
                await cleanup()
            attempt = next_attempt
