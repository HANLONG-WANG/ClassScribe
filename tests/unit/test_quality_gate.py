from __future__ import annotations

import asyncio

import pytest
from classscribe.asr.models import (
    ASRCandidateEvidence,
    ASRTokenEvidence,
    CandidateRole,
    decode_policy,
)
from classscribe.quality import (
    ComparisonContext,
    QualityContext,
    QualityFeatureExtractor,
    QualityIssue,
    QualityReviewPipeline,
    RetryDirective,
    ReviewRouter,
    compare_language_candidates,
    merge_retry_pieces,
)
from classscribe.quality.routing import SecondaryTrigger, TertiaryTrigger
from classscribe.timeline import SAMPLE_RATE, AudioSpan

SPAN = AudioSpan(10 * SAMPLE_RATE, 20 * SAMPLE_RATE)
REVISION = "a" * 40


def _candidate(
    text: str,
    language: str,
    *,
    model_id: str = "primary",
    tokens: tuple[ASRTokenEvidence, ...] | None = None,
    span: AudioSpan = SPAN,
) -> ASRCandidateEvidence:
    if tokens is None:
        tokens = (ASRTokenEvidence(text or "x", span, 0.8, 0),) if text else ()
    return ASRCandidateEvidence(
        model_id=model_id,
        model_revision=REVISION,
        role=CandidateRole.PRIMARY,
        language_requested=language,
        language_reported=language,
        audio_span=span,
        core_span=span,
        raw_text=text,
        normalized_text=text,
        confidence_raw=0.8,
        tokens=tokens,
        decode=decode_policy(span.duration_samples),
        metrics={"rtf": 0.1},
        warnings=(),
        provenance={"absolute_samples": True},
    )


def _context(**updates: object) -> QualityContext:
    values: dict[str, object] = {
        "requested_span": SPAN,
        "voiced_spans": (SPAN,),
        "vad_speech_ratio": 1.0,
        "snr_db": 24.0,
        "volume_rms": 0.2,
    }
    values.update(updates)
    return QualityContext(**values)  # type: ignore[arg-type]


def test_normal_candidate_extracts_all_feature_domains_without_unconditional_review() -> None:
    report = QualityFeatureExtractor().inspect("c1", _candidate("光合作用。", "zh"), _context())
    assert report.valid_for_consensus
    assert report.acoustic_model_features["confidence_raw_model_local"] == 0.8
    assert report.acoustic_model_features["raw_signals_are_cross_model_probability"] is False
    assert report.text_features["has_sentence_end"] is True
    assert report.timing_features["timestamp_voiced_coverage"] == 1.0
    assert report.multi_model_features["structure_text_available"] is False
    assert not ReviewRouter().secondary(report).run_secondary


def test_infinite_anata_loop_is_rejected_then_split_with_full_coverage_and_model_switch() -> None:
    text = "あなたはだれですか" * 8
    report = QualityFeatureExtractor().inspect(
        "loop",
        _candidate(text, "ja", tokens=()),
        _context(prefix_snapshots=(text[:18], text[:27], text[:36], text[:45])),
    )
    assert not report.valid_for_consensus
    assert QualityIssue.REPEATED_NGRAM in report.issues
    assert QualityIssue.SHORTEST_LOOP in report.issues
    assert report.retry is not None and report.retry.switch_model
    assert report.retry.reject_candidate
    assert report.retry.retry_spans[0].start_sample == SPAN.start_sample
    assert report.retry.retry_spans[-1].end_sample == SPAN.end_sample
    assert report.retry.retry_spans[0].end_sample == report.retry.retry_spans[1].start_sample
    assert all(span.duration_samples < SPAN.duration_samples for span in report.retry.retry_spans)
    decision = ReviewRouter().secondary(report)
    assert SecondaryTrigger.REPETITION_OR_HALLUCINATION in decision.triggers


@pytest.mark.parametrize(
    ("candidate", "context", "issue"),
    [
        (
            _candidate("hallucinated sentence", "en", tokens=()),
            _context(
                voiced_spans=(),
                vad_speech_ratio=0.0,
                no_speech_probability=0.95,
            ),
            QualityIssue.SILENCE_HALLUCINATION,
        ),
        (
            _candidate("光合作用叶绿体", "en"),
            _context(),
            QualityIssue.SCRIPT_MISMATCH,
        ),
        (
            _candidate("bad�??", "en"),
            _context(),
            QualityIssue.REPLACEMENT_CHARACTER,
        ),
    ],
)
def test_silence_hallucination_script_and_illegal_placeholder_never_enter_consensus(
    candidate: ASRCandidateEvidence,
    context: QualityContext,
    issue: QualityIssue,
) -> None:
    report = QualityFeatureExtractor().inspect("bad", candidate, context)
    assert issue in report.issues
    assert not report.valid_for_consensus


def test_second_model_triggers_cover_time_terms_structure_and_acoustics() -> None:
    short_token = ASRTokenEvidence(
        "partial", AudioSpan(SPAN.start_sample, 12 * SAMPLE_RATE), 0.5, 0
    )
    report = QualityFeatureExtractor().inspect(
        "suspect",
        _candidate("partial transcript without punctuation", "en", tokens=(short_token,)),
        _context(
            snr_db=4.0,
            far_field=True,
            structure_text="completely different structure reference",
            suspected_term_error=True,
        ),
    )
    triggers = set(ReviewRouter().secondary(report).triggers)
    assert SecondaryTrigger.INSUFFICIENT_TIME_COVERAGE in triggers
    assert SecondaryTrigger.SUSPECTED_TERMINOLOGY_ERROR in triggers
    assert SecondaryTrigger.STRUCTURE_TEXT_DIVERGENCE in triggers
    assert SecondaryTrigger.LOW_SNR_OR_FAR_FIELD in triggers


def test_prefix_stagnation_and_large_adjacent_word_overlap_are_hard_rejections() -> None:
    prefix_report = QualityFeatureExtractor().inspect(
        "prefix",
        _candidate("ordinary result.", "en"),
        _context(
            prefix_snapshots=(
                "ordinary result",
                "ordinary result",
                "ordinary result",
                "ordinary result",
            )
        ),
    )
    assert QualityIssue.PREFIX_STAGNATION in prefix_report.issues
    assert not prefix_report.valid_for_consensus

    overlapping = (
        ASRTokenEvidence("first", AudioSpan(10 * SAMPLE_RATE, 18 * SAMPLE_RATE), 0.8, 0),
        ASRTokenEvidence("second", AudioSpan(11 * SAMPLE_RATE, 19 * SAMPLE_RATE), 0.8, 1),
    )
    overlap_report = QualityFeatureExtractor().inspect(
        "overlap",
        _candidate("first second.", "en", tokens=overlapping),
        _context(),
    )
    assert QualityIssue.EXCESSIVE_TIME_OVERLAP in overlap_report.issues
    assert not overlap_report.valid_for_consensus


def test_language_specific_comparison_uses_reading_pinyin_and_optional_phonemes() -> None:
    japanese = compare_language_candidates(
        "鳥羽市",
        "飛ばし",
        "ja",
        ComparisonContext(
            readings={"鳥羽市": "とばし", "飛ばし": "とばし"},
            english_phonemes={},
            proper_names=frozenset({"鳥羽市"}),
        ),
    )
    assert japanese.lexical_distance > 0
    assert japanese.pronunciation_distance == 0
    assert japanese.homophone_spelling_conflict
    assert "proper_name" in japanese.high_value_conflicts

    moved_name = compare_language_candidates(
        "鳥羽市の講義です。最後に復習します。",
        "講義です。最後に鳥羽市を復習します。",
        "ja",
        ComparisonContext(
            readings={"鳥羽市": "とばし"},
            english_phonemes={},
            proper_names=frozenset({"鳥羽市"}),
        ),
    )
    assert moved_name.proper_noun_position_conflicts == ("鳥羽市",)

    chinese = compare_language_candidates(
        "适量",
        "质量",
        "zh",
        ComparisonContext(readings={"适量": "shi liang", "质量": "zhi liang"}, english_phonemes={}),
    )
    assert chinese.lexical_distance > 0
    assert chinese.pronunciation_distance is not None

    english = compare_language_candidates(
        "night",
        "knight",
        "en",
        ComparisonContext(readings={}, english_phonemes={"night": "N AY T", "knight": "N AY T"}),
    )
    assert english.homophone_spelling_conflict


def test_third_model_triggers_for_numbers_units_negation_names_and_two_weak_models() -> None:
    extractor = QualityFeatureExtractor()
    weak_context = _context(calibrated_candidate_quality=0.4)
    primary = extractor.inspect("p", _candidate("Dr Smith said not 5 mg", "en"), weak_context)
    secondary = extractor.inspect(
        "s", _candidate("Dr Jones said 50 kg", "en", model_id="secondary"), weak_context
    )
    comparison = compare_language_candidates(
        "Dr Smith said not 5 mg",
        "Dr Jones said 50 kg",
        "en",
        ComparisonContext(
            readings={},
            english_phonemes={},
            proper_names=frozenset({"smith", "jones"}),
        ),
    )
    decision = ReviewRouter().tertiary(primary, secondary, comparison)
    assert decision.run_tertiary
    assert TertiaryTrigger.HIGH_VALUE_TOKEN_CONFLICT in decision.triggers
    assert TertiaryTrigger.BOTH_CANDIDATES_LOW_QUALITY in decision.triggers
    assert set(decision.high_value_conflicts) >= {"number", "unit", "negation", "proper_name"}


def test_review_pipeline_runs_no_fallback_for_normal_and_only_conditional_second_third() -> None:
    pipeline = QualityReviewPipeline()
    calls: list[str] = []

    async def forbidden(_retry: object) -> tuple[str, ASRCandidateEvidence]:
        raise AssertionError("normal primary must not unconditionally invoke another model")

    normal = asyncio.run(
        pipeline.review(
            primary_id="normal",
            primary=_candidate("photosynthesis.", "en"),
            quality_context=_context(),
            comparison_context=ComparisonContext(readings={}, english_phonemes={}),
            run_secondary=forbidden,
            run_tertiary=forbidden,
        )
    )
    assert len(normal.candidates) == 1 and not normal.secondary_decision.run_secondary

    async def secondary(_retry: object) -> tuple[str, ASRCandidateEvidence]:
        calls.append("secondary")
        return "second", _candidate("dose is 50 kg.", "en", model_id="secondary")

    async def tertiary(_retry: object) -> tuple[str, ASRCandidateEvidence]:
        calls.append("tertiary")
        return "third", _candidate("dose is 5 mg.", "en", model_id="third")

    reviewed = asyncio.run(
        pipeline.review(
            primary_id="primary",
            primary=_candidate("dose is 5 mg.", "en"),
            quality_context=_context(suspected_term_error=True),
            comparison_context=ComparisonContext(readings={}, english_phonemes={}),
            run_secondary=secondary,
            run_tertiary=tertiary,
        )
    )
    assert calls == ["secondary", "tertiary"]
    assert len(reviewed.candidates) == 3


def test_loop_retry_runs_shorter_full_coverage_pieces_on_a_replacement_model() -> None:
    pipeline = QualityReviewPipeline()
    looping = _candidate("あなたはだれですか" * 8, "ja", tokens=())
    callback_saw_shorter_spans = False

    async def secondary(retry: RetryDirective | None) -> tuple[str, ASRCandidateEvidence]:
        nonlocal callback_saw_shorter_spans
        assert retry is not None
        directive = retry
        spans = directive.retry_spans
        callback_saw_shorter_spans = all(
            span.duration_samples < SPAN.duration_samples for span in spans
        )
        pieces = tuple(
            _candidate("正常な転写", "ja", model_id="replacement", span=span) for span in spans
        )
        return "replacement-candidate", merge_retry_pieces(directive, pieces, original_core=SPAN)

    async def no_third(_retry: object) -> tuple[str, ASRCandidateEvidence]:
        raise AssertionError("safe replacement does not require a third model")

    outcome = asyncio.run(
        pipeline.review(
            primary_id="loop",
            primary=looping,
            quality_context=_context(),
            comparison_context=ComparisonContext(readings={}, english_phonemes={}),
            run_secondary=secondary,
            run_tertiary=no_third,
        )
    )
    assert callback_saw_shorter_spans
    assert outcome.candidates[1].evidence.audio_span == SPAN
    assert outcome.candidates[1].evidence.model_id == "replacement"
    assert outcome.candidates[1].evidence.provenance["preserved_full_canonical_coverage"] is True
