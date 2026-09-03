"""Gold benchmark, calibration, ranking, and performance primitives."""

from classscribe.benchmark.acceptance import AcceptanceResult, validate_phase12_acceptance
from classscribe.benchmark.calibration import (
    CalibrationArtifact,
    CalibrationSample,
    fit_calibration,
)
from classscribe.benchmark.evidence import (
    REQUIRED_TEST_EVIDENCE,
    WORKER_CASES,
    WORKER_IDS,
    validate_test_evidence,
)
from classscribe.benchmark.metrics import (
    PerformanceObservation,
    SpeakerSpan,
    TimedWord,
    aggregate_performance,
    score_safety,
    score_speakers,
    score_text,
    score_timeline,
)
from classscribe.benchmark.models import (
    BenchmarkPrediction,
    GoldCoverage,
    GoldRecord,
    load_gold_manifest,
    load_predictions,
)
from classscribe.benchmark.persistence import BenchmarkRepository
from classscribe.benchmark.ranking import (
    RankingPolicy,
    assess_automatic_quality,
    rank_candidates,
)
from classscribe.benchmark.runner import BenchmarkReport, BenchmarkRunner

__all__ = [
    "REQUIRED_TEST_EVIDENCE",
    "WORKER_CASES",
    "WORKER_IDS",
    "AcceptanceResult",
    "BenchmarkPrediction",
    "BenchmarkReport",
    "BenchmarkRepository",
    "BenchmarkRunner",
    "CalibrationArtifact",
    "CalibrationSample",
    "GoldCoverage",
    "GoldRecord",
    "PerformanceObservation",
    "RankingPolicy",
    "SpeakerSpan",
    "TimedWord",
    "aggregate_performance",
    "assess_automatic_quality",
    "fit_calibration",
    "load_gold_manifest",
    "load_predictions",
    "rank_candidates",
    "score_safety",
    "score_speakers",
    "score_text",
    "score_timeline",
    "validate_phase12_acceptance",
    "validate_test_evidence",
]
