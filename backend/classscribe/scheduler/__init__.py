"""GPU scheduling and ordered execution primitives."""

from classscribe.scheduler.candidates import CandidateOutcome, run_candidates_sequentially
from classscribe.scheduler.gpu import (
    DecodeAttempt,
    GPULease,
    GPULeaseManager,
    GPUMemoryMonitor,
    OOMRecoveryChain,
    OOMRecoveryExhausted,
    ResidencyPlan,
    ResidencyPlanner,
    ResidencyPolicy,
    run_with_oom_recovery,
)
from classscribe.scheduler.ipc import GPULeaseIPCServer

__all__ = [
    "CandidateOutcome",
    "DecodeAttempt",
    "GPULease",
    "GPULeaseIPCServer",
    "GPULeaseManager",
    "GPUMemoryMonitor",
    "OOMRecoveryChain",
    "OOMRecoveryExhausted",
    "ResidencyPlan",
    "ResidencyPlanner",
    "ResidencyPolicy",
    "run_candidates_sequentially",
    "run_with_oom_recovery",
]
