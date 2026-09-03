"""Dictation state machine independent of GI and concrete streaming model runtimes."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol
from uuid import uuid4

from classscribe_protocol import DictationConfig, DictationState, DictationStatus

from classscribe_dictationd.streaming import (
    ChunkPlanner,
    DictationChunk,
    RollingContext,
    StablePrefix,
    TimedToken,
)


class StreamingRecognizer(Protocol):
    async def open(self, session_id: str, config: DictationConfig) -> None: ...

    async def push(
        self, pcm_s16le: bytes, *, absolute_start_sample: int
    ) -> tuple[TimedToken, ...]: ...

    async def finalize(
        self,
        chunk: DictationChunk,
        *,
        config: DictationConfig,
        context: tuple[str, ...],
    ) -> tuple[tuple[TimedToken, ...], tuple[str, ...]]: ...

    async def cancel(self) -> None: ...

    def accuracy_hot_switch_required(self, config: DictationConfig) -> bool: ...

    def expected_accuracy_load_ms(self, config: DictationConfig) -> float | None: ...

    async def accuracy_language(self) -> str: ...

    async def prepare_accuracy(self) -> None: ...


class DictationController:
    """Own one microphone/model session; every transition is observable and recoverable."""

    def __init__(
        self,
        recognizer: StreamingRecognizer,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.recognizer = recognizer
        self.clock = clock
        self.config = DictationConfig()
        self.session_id: str | None = None
        self._revision = 0
        self._events: list[DictationStatus] = []
        self._planner = self._new_planner()
        self._stable = StablePrefix(stable_after_seconds=self.config.stable_prefix_seconds)
        self._context = RollingContext(self.config.rolling_context_segments)
        self._interim: tuple[TimedToken, ...] = ()
        self._pending_candidates: tuple[str, ...] = ()
        self._has_prior_chunk = False
        self._release_started_at = 0.0
        self._publish(DictationState.IDLE)

    def configure(self, config: DictationConfig) -> DictationStatus:
        if self.current.state is not DictationState.IDLE:
            raise RuntimeError("dictation configuration changes only at an idle boundary")
        self.config = config
        self._planner = self._new_planner()
        self._stable = StablePrefix(stable_after_seconds=config.stable_prefix_seconds)
        self._context = RollingContext(config.rolling_context_segments)
        return self._publish(DictationState.IDLE, message="configuration_updated")

    async def begin(self, session_id: str | None = None) -> DictationStatus:
        self.prepare(session_id)
        return await self.activate()

    def prepare(self, session_id: str | None = None) -> DictationStatus:
        """Enter ARMING before potentially slow microphone/GPU acquisition."""

        if self.current.state is not DictationState.IDLE:
            raise RuntimeError("dictation is already active")
        self.session_id = session_id or str(uuid4())
        self._planner = self._new_planner()
        self._stable = StablePrefix(stable_after_seconds=self.config.stable_prefix_seconds)
        self._context = RollingContext(self.config.rolling_context_segments)
        self._interim = ()
        self._pending_candidates = ()
        self._has_prior_chunk = False
        return self._publish(DictationState.ARMING, message="microphone_and_gpu")

    async def activate(self) -> DictationStatus:
        if self.current.state is not DictationState.ARMING or self.session_id is None:
            raise RuntimeError("dictation is not armed")
        try:
            await self.recognizer.open(self.session_id, self.config)
        except Exception as exc:
            return self.fail(f"recognizer unavailable: {type(exc).__name__}")
        return self._publish(DictationState.LISTENING)

    async def feed(self, pcm_s16le: bytes, *, voiced: bool) -> DictationStatus:
        if self.current.state not in {DictationState.LISTENING, DictationState.INTERIM_UPDATE}:
            raise RuntimeError("audio is accepted only while listening")
        if not pcm_s16le or len(pcm_s16le) % 2:
            raise ValueError("PCM frame must contain whole signed 16-bit samples")
        frame_samples = len(pcm_s16le) // 2
        absolute_start = self._planner.absolute_sample
        chunk = self._planner.push(frame_samples, voiced=voiced)
        try:
            interim = await self.recognizer.push(pcm_s16le, absolute_start_sample=absolute_start)
            self._interim = interim
            stable = self._stable.update(interim, absolute_sample=self._planner.absolute_sample)
            commit = "".join(item.text for item in stable)
            if commit:
                self._context.add(commit)
            if chunk is not None:
                await self._finalize_chunk(chunk, release=False)
        except Exception as exc:
            return self.fail(f"streaming inference failed: {type(exc).__name__}")
        preedit = "".join(item.text for item in self._stable.pending)
        return self._publish(
            DictationState.INTERIM_UPDATE,
            preedit=preedit if self.config.show_interim else "",
            stable_characters=sum(len(item.text) for item in stable),
            commit_text=commit,
        )

    async def release(self, *, defer_candidates: bool = False) -> DictationStatus:
        if self.current.state not in {DictationState.LISTENING, DictationState.INTERIM_UPDATE}:
            raise RuntimeError("dictation is not listening")
        self._release_started_at = self.clock()
        self._publish(DictationState.FINALIZING)
        chunk = self._planner.release()
        candidates: tuple[str, ...] = ()
        if chunk is not None:
            try:
                candidates = await self._finalize_chunk(chunk, release=True)
            except Exception as exc:
                return self.fail(f"final decode failed: {type(exc).__name__}")
        final_tokens = self._stable.finalize(self._interim)
        final_text = "".join(item.text for item in final_tokens)
        if candidates and not self._has_prior_chunk:
            final_text = candidates[0]
        elif candidates:
            # Worker candidates cover only the current overlap window after an
            # automatic cut, so presenting them would duplicate or omit text.
            candidates = ()
        with_cancel_error = ""
        try:
            await self.recognizer.cancel()
        except Exception as exc:
            with_cancel_error = f"stream close failed: {type(exc).__name__}"
        if len(candidates) > 1 and defer_candidates:
            self._pending_candidates = candidates
            return self._publish(
                DictationState.CANDIDATE_SELECT,
                candidates=candidates,
                message=with_cancel_error,
            )
        return self._commit(final_text, message=with_cancel_error)

    def select_candidate(self, index: int) -> DictationStatus:
        if self.current.state is not DictationState.CANDIDATE_SELECT:
            raise RuntimeError("no dictation candidates are awaiting selection")
        if not 0 <= index < len(self._pending_candidates):
            raise ValueError("candidate index is out of range")
        return self._commit(self._pending_candidates[index])

    def _commit(self, final_text: str, *, message: str = "") -> DictationStatus:
        self._publish(DictationState.COMMITTING, commit_text=final_text)
        latency = (self.clock() - self._release_started_at) * 1000
        self.session_id = None
        self._pending_candidates = ()
        return self._publish(
            DictationState.IDLE,
            commit_text=final_text,
            latency_ms=latency,
            message=message,
        )

    async def cancel(self) -> DictationStatus:
        if self.current.state is DictationState.IDLE:
            return self.current
        try:
            await self.recognizer.cancel()
        finally:
            self.session_id = None
            self._interim = ()
            self._pending_candidates = ()
        return self._publish(DictationState.IDLE, message="cancelled", preedit="")

    def accuracy_hot_switch_required(self) -> bool:
        method = getattr(self.recognizer, "accuracy_hot_switch_required", None)
        return bool(method(self.config)) if callable(method) else False

    async def accuracy_language(self) -> str:
        method = getattr(self.recognizer, "accuracy_language", None)
        if callable(method):
            return str(await method())
        return self.config.language.value if self.config.language.value != "auto" else "zh"

    async def prepare_accuracy(self) -> None:
        method = getattr(self.recognizer, "prepare_accuracy", None)
        if callable(method):
            await method()

    def fail(self, message: str) -> DictationStatus:
        self._publish(DictationState.ERROR, message=message, preedit="")
        self.session_id = None
        self._interim = ()
        self._pending_candidates = ()
        return self._publish(DictationState.IDLE, message=message, preedit="")

    @property
    def current(self) -> DictationStatus:
        return self._events[-1]

    def events_after(self, revision: int) -> tuple[DictationStatus, ...]:
        return tuple(item for item in self._events if item.revision > revision)

    async def _finalize_chunk(self, chunk: DictationChunk, *, release: bool) -> tuple[str, ...]:
        tokens, candidates = await self.recognizer.finalize(
            chunk, config=self.config, context=self._context.values()
        )
        if release:
            self._interim = tokens
            return candidates
        newly_stable = self._stable.update(tokens, absolute_sample=chunk.end_sample)
        stable_text = "".join(item.text for item in newly_stable)
        if stable_text:
            self._context.add(stable_text)
        self._interim = tokens
        self._has_prior_chunk = True
        self._publish(
            DictationState.LISTENING,
            preedit="".join(item.text for item in self._stable.pending),
            commit_text=stable_text,
            message=f"chunk_{chunk.sequence}_{chunk.reason}",
        )
        return candidates

    def _publish(self, state: DictationState, **changes: Any) -> DictationStatus:
        self._revision += 1
        properties = {
            "language": self.config.language.value,
            "confirmation": self.config.confirmation.value,
            "activation": self.config.activation.value,
            "model_id": self.config.model_id,
            "show_interim": self.config.show_interim,
            "punctuation": self.config.punctuation.value,
        }
        expected_method = getattr(self.recognizer, "expected_accuracy_load_ms", None)
        expected_load_ms = (
            expected_method(self.config)
            if self.config.confirmation.value == "accuracy" and callable(expected_method)
            else None
        )
        base = DictationStatus(
            self.session_id,
            self._revision,
            state,
            expected_accuracy_load_ms=expected_load_ms,
            properties=properties,
        )
        status = replace(base, **changes)
        self._events.append(status)
        del self._events[: max(0, len(self._events) - 512)]
        return status

    def _new_planner(self) -> ChunkPlanner:
        return ChunkPlanner(
            endpoint_silence_ms=self.config.endpoint_silence_ms,
            hard_seconds=self.config.hard_chunk_seconds,
            overlap_seconds=self.config.overlap_seconds,
        )
