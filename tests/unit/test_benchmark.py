from __future__ import annotations

import asyncio
import json
import stat
import wave
from pathlib import Path
from typing import Any, Literal

import pytest
from classscribe.benchmark import (
    REQUIRED_TEST_EVIDENCE,
    WORKER_CASES,
    WORKER_IDS,
    BenchmarkPrediction,
    BenchmarkReport,
    BenchmarkRepository,
    BenchmarkRunner,
    CalibrationSample,
    GoldCoverage,
    GoldRecord,
    PerformanceObservation,
    RankingPolicy,
    SpeakerSpan,
    TimedWord,
    aggregate_performance,
    assess_automatic_quality,
    fit_calibration,
    load_gold_manifest,
    rank_candidates,
    score_safety,
    score_speakers,
    score_text,
    score_timeline,
    validate_phase12_acceptance,
)
from classscribe.benchmark.normalizers import (
    normalized_cjk_units,
    normalized_english_words,
    raw_cjk_units,
    raw_english_words,
)
from classscribe.db.models import BenchmarkRun, BenchmarkStatus
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from scripts.validate_ibus_desktop import build_report

ROOT = Path(__file__).resolve().parents[2]


def test_language_normalization_keeps_raw_and_comparable_metrics_separate() -> None:
    assert raw_cjk_units("ＡＢＣ， １２") == tuple("ＡＢＣ，１２")  # noqa: RUF001
    assert normalized_cjk_units("ＡＢＣ， １２") == tuple("abc12")  # noqa: RUF001
    assert raw_english_words("ClassScribe's GPU, FAST!") == (
        "ClassScribe's",
        "GPU",
        "FAST",
    )
    assert normalized_english_words("ClassScribe’s GPU") == ("classscribe's", "gpu")  # noqa: RUF001

    metrics = score_text(
        "Hello, NASA 12 kg.",
        "hello NASA 12 kg",
        "en",
        terms=("NASA",),
        term_vocabulary=("NASA", "CUDA"),
        entities={"number": ("12",), "unit": ("kg",), "negation": ("not",)},
    )
    assert metrics["normalized_wer"] == 0
    assert isinstance(metrics["raw_wer"], (int, float)) and metrics["raw_wer"] > 0
    assert metrics["number_accuracy"] == 1
    assert metrics["negation_accuracy"] == 0
    assert metrics["term"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert isinstance(metrics["capitalization_f1"], (int, float))
    assert 0 <= metrics["capitalization_f1"] <= 1


def test_punctuation_safety_timeline_speaker_and_performance_metrics() -> None:
    text = score_text("a,b.", "a,b", "en")
    punctuation = text["punctuation"]
    assert isinstance(punctuation, dict)
    assert punctuation["f1"] == pytest.approx(2 / 3)
    assert score_text("「今日」", "「今日", "ja")["paired_quote_bracket_errors"] == 1

    safety = score_safety(
        "abc",
        "xyzxyzxyzxyz�",
        duration_seconds=3600,
        tags=("silence",),
        voiced_spans=((0, 100),),
        predicted_spans=((25, 75),),
    )
    assert safety["silence_hallucinations_per_hour"] == 1
    assert safety["loop_triggers_per_hour"] == 1
    assert safety["voiced_coverage"] == 0.5
    assert float(safety["invalid_character_rate"]) > 0

    reference_words = (
        TimedWord("one", 0, 8_000, "teacher"),
        TimedWord("two", 8_000, 16_000, "student"),
    )
    predicted_words = (
        TimedWord("one", 160, 8_160, "A"),
        TimedWord("two", 8_160, 16_000, "B"),
    )
    timeline = score_timeline(
        reference_words,
        predicted_words,
        reference_sentence_boundaries=(8_000, 16_000),
        predicted_sentence_boundaries=(8_100, 16_000),
        duration_samples=16_000,
    )
    assert timeline["structural_errors"] == 0
    assert timeline["word_boundary_mae_ms"] == pytest.approx(7.5)
    assert timeline["sentence_boundary_f1_250ms"] == 1

    speakers = score_speakers(
        (SpeakerSpan("teacher", 0, 8_000), SpeakerSpan("student", 8_000, 16_000)),
        (SpeakerSpan("A", 0, 8_000), SpeakerSpan("B", 8_000, 16_000)),
        reference_words=reference_words,
        predicted_words=predicted_words,
        cross_window_speakers=("teacher", "teacher", "student"),
    )
    assert speakers["der"] == 0
    assert speakers["jer"] == 0
    assert speakers["cross_window_speaker_switches"] == 1

    performance = aggregate_performance(
        (
            PerformanceObservation(10, 2, 1000, 2000, 100, 500, 1, 30),
            PerformanceObservation(20, 4, 1200, 1800, 300, 900, 2, 50),
        )
    )
    assert performance["rtf"] == 0.2
    assert performance["peak_vram_mb"] == 1200
    assert performance["commit_latency_p50_ms"] == 700
    assert performance["projected_90m_seconds"] == 1080
    assert performance["runtime_observation_coverage"] == 1
    assert performance["interim_observation_coverage"] == 1


def test_manifest_contract_and_gold_coverage_gate_are_strict(tmp_path: Path) -> None:
    with wave.open(str(tmp_path / "sample.wav"), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\0\0" * 16_000)
    manifest = tmp_path / "gold.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "item_id": "one",
                "audio": "sample.wav",
                "scenario": "classroom",
                "split": "train",
                "language": "ja",
                "start_sample": 0,
                "end_sample": 16_000,
                "text": "今日",
                "words": [{"text": "今日", "start_sample": 0, "end_sample": 16_000}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert load_gold_manifest(manifest)[0].duration_seconds == 1
    with pytest.raises(ValueError, match="coverage gate"):
        GoldCoverage.inspect(load_gold_manifest(manifest)).require(production=False)

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text(manifest.read_text(encoding="utf-8") * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_gold_manifest(invalid)

    (tmp_path / "linked.wav").symlink_to(tmp_path / "sample.wav")
    linked = tmp_path / "linked.jsonl"
    linked.write_text(
        manifest.read_text(encoding="utf-8").replace("sample.wav", "linked.wav"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe"):
        load_gold_manifest(linked)


def test_calibration_selects_on_validation_reports_test_and_invalidates_on_revision() -> None:
    splits: tuple[Literal["train", "validation", "test"], ...] = (
        "train",
        "validation",
        "test",
    )
    samples = tuple(
        CalibrationSample(split, score, error, (score * 0.5,))
        for split in splits
        for score, error in ((0.1, 0.8), (0.4, 0.4), (0.8, 0.1), (0.95, 0.0))
    )
    artifact = fit_calibration(
        samples,
        model_id="model",
        model_revision="a" * 40,
        language="ja",
        scenario="classroom",
        manifest_sha256="b" * 64,
        error_threshold=0.2,
    )
    assert artifact.method in {"isotonic", "platt"}
    assert 0 <= artifact.predict(0.9, (0.45,)) <= 1
    assert artifact.validation_brier >= 0 and artifact.test_brier >= 0
    assert artifact.valid_for(model_revision="a" * 40, manifest_sha256="b" * 64)
    assert not artifact.valid_for(model_revision="c" * 40, manifest_sha256="b" * 64)
    with pytest.raises(ValueError, match="train, validation, and test"):
        fit_calibration(
            samples[:4],
            model_id="model",
            model_revision="a" * 40,
            language="ja",
            scenario="classroom",
            manifest_sha256="b" * 64,
            error_threshold=0.2,
        )


def test_constraint_first_ranking_separates_language_and_scenario() -> None:
    baseline = {
        "normalized_wer": 0.1,
        "term": {"f1": 1.0},
        "punctuation": {"per": 0.1},
        "structural_errors": 0,
        "loop_triggers_per_hour": 0,
        "silence_hallucinations_per_hour": 0,
        "rtf": 0.2,
        "peak_vram_mb": 1000,
        "interim_latency_p50_ms": 200,
        "commit_latency_p50_ms": 500,
        "commit_latency_p95_ms": 900,
        "provenance_coverage": 1.0,
        "runtime_observation_coverage": 1.0,
        "interim_observation_coverage": 1.0,
        "commit_observation_coverage": 1.0,
    }
    calibration_a = {
        "method": "isotonic",
        "model_revision": "a",
        "manifest_sha256": "m" * 64,
    }
    calibration_b = {
        "method": "platt",
        "model_revision": "b",
        "manifest_sha256": "m" * 64,
    }
    ranked = rank_candidates(
        (
            {
                "model_id": "accurate",
                "model_revision": "a",
                "language": "en",
                "scenario": "ibus",
                "metrics": baseline,
                "calibration": calibration_a,
            },
            {
                "model_id": "looping",
                "model_revision": "b",
                "language": "en",
                "scenario": "ibus",
                "metrics": {**baseline, "normalized_wer": 0, "loop_triggers_per_hour": 1},
                "calibration": calibration_b,
            },
        ),
        language="en",
        scenario="ibus",
        policy=RankingPolicy(),
    )
    assert ranked[0]["model_id"] == "accurate" and ranked[0]["eligible"]
    assert not ranked[1]["eligible"]

    automatic = {
        **baseline,
        "normalized_wer": 0.11,
        "omission_rate": 0.0,
        "provenance_coverage": 1.0,
    }
    single = {
        **baseline,
        "omission_rate": 0.0,
        "provenance_coverage": 1.0,
    }
    assert assess_automatic_quality(automatic, single, language="en")["passed"] is True
    assert (
        assess_automatic_quality({**automatic, "provenance_coverage": 0.9}, single, language="en")[
            "passed"
        ]
        is False
    )


def test_complete_runner_calibrates_each_product_language_and_writes_private_report(
    tmp_path: Path,
) -> None:
    records: list[GoldRecord] = []
    predictions: list[BenchmarkPrediction] = []
    languages: tuple[Literal["zh", "ja", "en"], ...] = ("zh", "ja", "en")
    scenarios: tuple[Literal["classroom", "ibus"], ...] = ("classroom", "ibus")
    splits: tuple[Literal["train", "validation", "test"], ...] = (
        "train",
        "validation",
        "test",
    )
    for language in languages:
        for scenario in scenarios:
            for split in splits:
                identifier = f"{language}-{scenario}-{split}"
                duration = 100 if scenario == "classroom" else 61
                text = "hello lesson" if language == "en" else "课程内容"
                records.append(
                    GoldRecord(
                        identifier,
                        f"{identifier}.wav",
                        scenario,
                        split,
                        language,
                        0,
                        duration * 16_000,
                        text,
                        voiced_spans=((0, duration * 16_000),),
                        tags=("continuous_over_60s",) if scenario == "ibus" else (),
                    )
                )
                predictions.append(
                    BenchmarkPrediction(
                        identifier,
                        "fixture-model",
                        "f" * 40,
                        text,
                        confidence=0.9,
                        voiced_spans=((0, duration * 16_000),),
                        runtime_seconds=duration * 0.1,
                        peak_vram_mb=1024,
                        peak_ram_mb=2048,
                        interim_latency_ms=200 if scenario == "ibus" else None,
                        commit_latency_ms=500 if scenario == "ibus" else None,
                        provenance_coverage=1.0,
                    )
                )
    runner = BenchmarkRunner(
        tuple(records),
        manifest_version="private-v1",
        hardware={"gpu": "fixture"},
        parameters={"decode": "deterministic"},
        production_gold=False,
    )
    report = runner.run(tuple(predictions))
    assert report.status == "completed"
    assert len(report.calibrations) == 6
    assert len(report.rankings) == 6
    assert all(item["models"] == ["fixture-model"] for item in report.rankings)
    output = tmp_path / "results" / "report.json"
    report.write(output)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))["manifest_version"] == "private-v1"

    async def predictor(record: GoldRecord, model_id: str, revision: str) -> BenchmarkPrediction:
        return next(
            item
            for item in predictions
            if item.item_id == record.item_id
            and item.model_id == model_id
            and item.model_revision == revision
        )

    executed = asyncio.run(runner.execute((("fixture-model", "f" * 40),), predictor))
    assert executed.status == "completed"


def test_desktop_matrix_never_marks_inventory_as_compatibility_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = {
        "required_scenarios": ["preedit", "commit"],
        "applications": [
            {
                "id": "editor",
                "application": "Editor",
                "toolkit": "GTK",
                "command_candidates": ["editor"],
                "preedit_attributes": True,
            }
        ],
    }
    monkeypatch.setattr("scripts.validate_ibus_desktop.shutil.which", lambda _value: "/bin/editor")
    inventory = build_report(matrix, {})
    assert inventory["status"] == "incomplete"
    application = inventory["applications"]
    assert isinstance(application, list) and application[0]["passed"] is False
    evidence = {
        "applications": {"editor": {"scenarios": {"preedit": "passed", "commit": "passed"}}},
        "sessions": {
            name: "passed" for name in ("wayland-gnome", "wayland-kde", "x11-gnome", "x11-kde")
        },
    }
    assert build_report(matrix, evidence)["status"] == "passed"


def test_benchmark_report_persistence_is_atomic_and_status_gated(
    database: tuple[Engine, sessionmaker[Session], Path],
) -> None:
    _, sessions, _ = database
    with sessions.begin() as session:
        run = BenchmarkRun(
            manifest_version="gold-v1",
            status=BenchmarkStatus.RUNNING,
            hardware_json={},
            parameters_json={"requested": True},
            metrics_json={},
            ranking_json=[],
        )
        session.add(run)
        session.flush()
        run_id = run.id
    report = BenchmarkReport(
        1,
        "gold-v1",
        "a" * 64,
        "completed",
        {"gpu": "local"},
        {"offline": True},
        {},
        (
            {
                "item_id": "one",
                "split": "test",
                "scenario": "classroom",
                "language": "ja",
                "model_id": "model",
                "model_revision": "b" * 40,
                "gold": {"text": "正解"},
                "prediction": {"text": "正解"},
                "metrics": {"normalized_cer": 0.0},
            },
        ),
        (),
        (
            {
                "language": "ja",
                "scenario": "classroom",
                "models": ["model"],
                "passed": True,
            },
        ),
    )
    repository = BenchmarkRepository(sessions)
    persisted = repository.complete(run_id, report)
    assert persisted.status is BenchmarkStatus.COMPLETED
    with sessions() as session:
        stored = session.get_one(BenchmarkRun, run_id)
        assert stored.metrics_json["item_count"] == 1
        assert stored.parameters_json["manifest_sha256"] == "a" * 64
        assert len(stored.items) == 1
    with pytest.raises(ValueError, match="not an empty running run"):
        repository.complete(run_id, report)


def test_fixed_historical_regressions_remain_detectable() -> None:
    manifest = json.loads((ROOT / "tests/golden/regressions.v1.json").read_text(encoding="utf-8"))
    cases = manifest["cases"]
    assert {item["id"] for item in cases} == {
        "long-output-first-block-only",
        "funasr-infinite-loop",
        "punctuation-empty-or-question-question",
        "three-models-one-timestamp",
        "wrong-text-forced-alignment",
        "vad-hard-cut-without-overlap",
    }
    for case in cases:
        for node_id in case["tests"]:
            relative, function = node_id.split("::", maxsplit=1)
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert f"def {function}(" in source

    omission = score_safety(
        "First complete sentence. Second complete sentence. Third complete sentence.",
        "First complete sentence.",
        duration_seconds=65,
    )
    assert omission["omission_rate"] == pytest.approx(2 / 3)
    artifact = score_text("This is complete.", "This is complete??", "en")
    punctuation = artifact["punctuation"]
    assert isinstance(punctuation, dict)
    assert punctuation["f1"] == 0 and punctuation["per"] > 0
    timeline = score_timeline(
        (),
        (
            TimedWord("one", 0, 10),
            TimedWord("two", 0, 10),
            TimedWord("three", 0, 10),
        ),
        duration_samples=100,
    )
    assert timeline["structural_errors"] == 2


def test_phase12_acceptance_requires_real_complete_external_evidence() -> None:
    records: list[GoldRecord] = []
    languages: tuple[Literal["zh", "ja", "en"], ...] = ("zh", "ja", "en")
    splits: tuple[Literal["train", "validation", "test"], ...] = (
        "train",
        "validation",
        "test",
    )
    for language_index, language in enumerate(languages):
        classroom_seconds = 5_400 if language == "zh" else 1_800
        speaker_count = {"zh": 5, "ja": 2, "en": 1}[language]
        speakers = tuple(
            SpeakerSpan(f"speaker-{index}", index * 16_000, (index + 1) * 16_000)
            for index in range(speaker_count)
        )
        records.append(
            GoldRecord(
                f"{language}-classroom",
                f"{language}/classroom.wav",
                "classroom",
                splits[language_index],
                language,
                0,
                classroom_seconds * 16_000,
                "lesson",
                speaker_spans=speakers,
                tags=(
                    "code_switch",
                    "silence",
                    "background_music",
                    "interrupted",
                    "overlap",
                )
                if language == "zh"
                else (),
            )
        )
        for index in range(50):
            duration = 300 if index == 0 else 1
            records.append(
                GoldRecord(
                    f"{language}-ibus-{index}",
                    f"{language}/ibus-{index}.wav",
                    "ibus",
                    splits[index % 3],
                    language,
                    0,
                    duration * 16_000,
                    "dictation",
                )
            )
    rankings = [
        {
            "language": language,
            "scenario": scenario,
            "models": ["real-model"],
            "passed": True,
        }
        for language in languages
        for scenario in ("classroom", "ibus")
    ]
    report = {
        "status": "completed",
        "manifest_sha256": "a" * 64,
        "parameters": {"real_model_execution": True, "synthetic_gold": False},
        "rankings": rankings,
        "items": [
            {
                "metrics": {
                    "structural_errors": 0,
                    "loop_triggers_per_hour": 0,
                    "silence_hallucinations_per_hour": 0,
                    "provenance_coverage": 1,
                }
            }
        ],
    }
    real_keys = {
        "worker_contracts",
        "real_model_zh_ja_en",
        "pipeline_90m",
        "ibus_5m",
        "gpu_preemption",
        "oom_recovery",
    }
    tests: dict[str, dict[str, Any]] = {
        key: {
            "status": "passed",
            "evidence_kind": "real_hardware" if key in real_keys else "automated",
            "command": f"run-{key}",
            "completed_at": "2026-09-04T00:00:00+00:00",
            "artifact_sha256": "b" * 64,
        }
        for key in REQUIRED_TEST_EVIDENCE
    }
    tests["worker_contracts"]["workers"] = [
        {
            "worker_id": worker_id,
            "model_revision": "c" * 40,
            "cases": {case: "passed" for case in WORKER_CASES},
            "baseline_vram_mb": 100,
            "post_exit_vram_mb": 100,
        }
        for worker_id in WORKER_IDS
    ]
    tests["real_model_zh_ja_en"]["languages"] = {
        language: {"model_id": f"real-{language}", "model_revision": "d" * 40}
        for language in languages
    }
    passed = validate_phase12_acceptance(
        tuple(records), report, {"status": "passed"}, tests, manifest_sha256="a" * 64
    )
    assert passed.status == "passed" and all(passed.checks.values())
    incomplete = validate_phase12_acceptance(
        tuple(records), report, {"status": "incomplete"}, {}, manifest_sha256="b" * 64
    )
    assert incomplete.status == "incomplete"
    assert not incomplete.checks["desktop_matrix"]
    assert not incomplete.checks["manifest_hash_matches"]


def test_isotonic_ties_are_order_independent() -> None:
    from classscribe.benchmark.calibration import CalibrationSample, _fit_isotonic

    samples = (CalibrationSample("train", 0.5, 0),) * 2
    assert (
        _fit_isotonic(samples, (0.0, 1.0))
        == _fit_isotonic(samples, (1.0, 0.0))
        == {"thresholds": [0.5], "values": [0.5]}
    )
