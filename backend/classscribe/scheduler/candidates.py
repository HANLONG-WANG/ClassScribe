"""Ordered classroom candidate execution without parallel model residency."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from classscribe.models.registry import ModelEntry


@dataclass(frozen=True, slots=True)
class CandidateOutcome[ResultT]:
    model_id: str
    revision: str
    value: ResultT | None
    error: str | None


async def run_candidates_sequentially[ResultT](
    candidates: Sequence[ModelEntry],
    operation: Callable[[ModelEntry], Awaitable[ResultT]],
    *,
    continue_on_error: bool = True,
) -> tuple[CandidateOutcome[ResultT], ...]:
    """Load/infer/unload callbacks complete before the next registry candidate starts."""

    ids = [item.id for item in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate sequence contains a duplicate model")
    outcomes: list[CandidateOutcome[ResultT]] = []
    for candidate in candidates:
        if not candidate.enabled or candidate.installation.state != "installed":
            raise ValueError(f"candidate is not enabled and installed: {candidate.id}")
        try:
            value = await operation(candidate)
        except Exception as exc:
            outcomes.append(
                CandidateOutcome(
                    candidate.id, candidate.revision, None, f"{type(exc).__name__}: {exc}"
                )
            )
            if not continue_on_error:
                break
        else:
            outcomes.append(CandidateOutcome(candidate.id, candidate.revision, value, None))
    return tuple(outcomes)
