from __future__ import annotations

import pytest
from classscribe.asr.models import (
    ASRCandidateEvidence,
    ASRTokenEvidence,
    CandidateRole,
    decode_policy,
)
from classscribe.consensus import (
    ConfusionNetwork,
    ConsensusCandidate,
    ConsensusInputs,
    DeterministicTermRule,
    ReliabilityProfile,
)
from classscribe.quality.models import QualityReport
from classscribe.timeline import SAMPLE_RATE, AudioSpan

SPAN = AudioSpan(4 * SAMPLE_RATE, 12 * SAMPLE_RATE)
REVISION = "b" * 40


def _evidence(
    model: str,
    text: str,
    language: str,
    words: tuple[str, ...] = (),
    *,
    span: AudioSpan = SPAN,
) -> ASRCandidateEvidence:
    width = span.duration_samples // max(len(words), 1)
    tokens = tuple(
        ASRTokenEvidence(
            word,
            AudioSpan(
                span.start_sample + index * width,
                span.end_sample
                if index == len(words) - 1
                else span.start_sample + (index + 1) * width,
            ),
            0.7,
            index,
        )
        for index, word in enumerate(words)
    )
    return ASRCandidateEvidence(
        model,
        REVISION,
        CandidateRole.PRIMARY,
        language,
        language,
        span,
        span,
        text,
        text,
        0.7,
        tokens,
        decode_policy(span.duration_samples),
        {"rtf": 0.1},
        (),
        {"absolute_samples": True},
    )


def _quality(
    candidate_id: str, model: str, valid: bool = True, score: float = 0.9
) -> QualityReport:
    return QualityReport(
        candidate_id,
        model,
        valid,
        score,
        False,
        (),
        {},
        {},
        {},
        {},
        None,
    )


def _candidate(
    candidate_id: str,
    model: str,
    text: str,
    language: str,
    words: tuple[str, ...] = (),
    *,
    valid: bool = True,
    span: AudioSpan = SPAN,
) -> ConsensusCandidate:
    return ConsensusCandidate(
        candidate_id,
        _evidence(model, text, language, words, span=span),
        _quality(candidate_id, model, valid),
    )


def _inputs(**updates: object) -> ConsensusInputs:
    values: dict[str, object] = {
        "reliability": ReliabilityProfile(
            {"a": 0.9, "b": 0.6, "c": 0.4}, "local-gold-v1", True, "lecture"
        ),
        "calibrated_token_confidence": {},
        "voiced_spans": (SPAN,),
    }
    values.update(updates)
    return ConsensusInputs(**values)  # type: ignore[arg-type]


def test_native_word_time_anchor_and_dp_untimed_candidate_make_monotonic_columns() -> None:
    native = _candidate("native", "a", "dose is 5 mg", "en", ("dose", "is", "5", "mg"))
    untimed = _candidate("untimed", "b", "dose is five mg", "en")
    result = ConfusionNetwork().resolve(
        (native, untimed), canonical_span=SPAN, language="en", inputs=_inputs()
    )
    assert result.strategy == "time_aligned_confusion_network"
    assert result.text.startswith("dose is")
    assert len(result.tokens) >= 4
    assert all(
        left.span.end_sample <= right.span.start_sample
        for left, right in zip(result.tokens, result.tokens[1:], strict=False)
    )
    assert result.tokens[0].provenance["candidate_sources"]
    assert all(token.provenance["source_type"] == "asr_candidate" for token in result.tokens)
    assert any(
        source["timing_source"] == "constrained_interval"
        for token in result.tokens
        for source in token.provenance["candidate_sources"]
    )


def test_calibrated_reliability_token_confidence_acoustic_and_glossary_weights_choose_vote() -> (
    None
):
    left = _candidate("left", "a", "five", "en", ("five",))
    right = _candidate("right", "b", "fife", "en", ("fife",))
    inputs = _inputs(
        calibrated_token_confidence={("left", 0): 0.95, ("right", 0): 0.2},
        glossary_terms=frozenset({"five"}),
    )
    result = ConfusionNetwork().resolve(
        (left, right), canonical_span=SPAN, language="en", inputs=inputs
    )
    assert result.text == "five"
    assert result.tokens[0].provenance["selected_candidate_id"] == "left"
    assert result.tokens[0].provenance["reliability_locally_calibrated"] is True
    assert result.tokens[0].provenance["token_confidence_calibrated"] is True


def test_new_standard_spelling_requires_explicit_deterministic_term_rule() -> None:
    candidate = _candidate("alias", "a", "oistt", "en", ("oistt",))
    without_rule = ConfusionNetwork().resolve(
        (candidate,), canonical_span=SPAN, language="en", inputs=_inputs()
    )
    assert without_rule.text == "oistt"
    rule = DeterministicTermRule("course-term-7", "en", "OIST", ("oistt",))
    with_rule = ConfusionNetwork().resolve(
        (candidate,),
        canonical_span=SPAN,
        language="en",
        inputs=_inputs(terminology_rules=(rule,)),
    )
    assert with_rule.text == "OIST"
    assert with_rule.tokens[0].provenance["source_type"] == "deterministic_terminology_rule"
    assert with_rule.tokens[0].provenance["rule_id"] == "course-term-7"
    assert with_rule.tokens[0].provenance["candidate_sources"][0]["candidate_id"] == "alias"


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("zh", "\uff3b听不清 00:00:04.000\u201300:00:12.000\uff3d"),
        ("ja", "\uff3b聞き取り不明 00:00:04.000\u201300:00:12.000\uff3d"),
        ("en", "[inaudible 00:00:04.000\u201300:00:12.000]"),
    ],
)
def test_all_invalid_candidates_use_language_specific_timed_inaudible_marker(
    language: str, expected: str
) -> None:
    invalid = _candidate("invalid", "a", "fabricated", language, valid=False)
    result = ConfusionNetwork().resolve(
        (invalid,), canonical_span=SPAN, language=language, inputs=_inputs()
    )
    assert result.text == expected
    assert result.strategy == "inaudible_marker"
    assert result.low_confidence
    assert result.tokens[0].provenance["not_generated_completion"] is True


def test_three_way_unreliable_disagreement_uses_one_best_candidate_not_fluent_completion() -> None:
    candidates = (
        _candidate("a1", "a", "alpha", "en", ("alpha",)),
        _candidate("b1", "b", "bravo", "en", ("bravo",)),
        _candidate("c1", "c", "charlie", "en", ("charlie",)),
    )
    result = ConfusionNetwork().resolve(
        candidates,
        canonical_span=SPAN,
        language="en",
        inputs=_inputs(reliability=ReliabilityProfile({}, "bootstrap-neutral", False, "lecture")),
    )
    assert result.strategy == "best_candidate_low_confidence"
    assert result.text in {"alpha", "bravo", "charlie"}
    assert result.low_confidence
    assert result.text not in {"alpha bravo charlie", "alpha charlie"}


def test_candidates_from_different_canonical_intervals_are_rejected() -> None:
    wrong_span = AudioSpan(SPAN.start_sample, SPAN.end_sample + SAMPLE_RATE)
    with pytest.raises(ValueError, match="share one canonical"):
        ConfusionNetwork().resolve(
            (
                _candidate("right", "a", "right", "en", ("right",)),
                _candidate("wrong", "b", "wrong", "en", ("wrong",), span=wrong_span),
            ),
            canonical_span=SPAN,
            language="en",
            inputs=_inputs(),
        )


@pytest.mark.parametrize("words", [(), ("OpenAI", "NASA")])
def test_consensus_preserves_surface_case(words: tuple[str, ...]) -> None:
    candidate = _candidate("case", "a", "OpenAI NASA", "en", words)
    result = ConfusionNetwork().resolve(
        (candidate,), canonical_span=SPAN, language="en", inputs=_inputs()
    )
    assert result.text == "OpenAI NASA"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("ja", "では、PowerPoint を共有して授業を進めます。"),
        ("ja", "「本当ですか\uff1f」と言いました。"),
        ("zh", "今天\uff0c学习标点。"),
        ("en", "Hello, world! We use (the same) words."),
    ],
)
def test_single_candidate_preserves_native_surface(language: str, text: str) -> None:
    result = ConfusionNetwork().resolve(
        (_candidate("one", "a", text, language),),
        canonical_span=SPAN,
        language=language,
        inputs=_inputs(),
    )
    assert result.text == text
    assert result.punctuation_sources[0]["model_id"] == "a"
    assert result.punctuation_sources[0]["boundary_count"] > 0


def test_native_surface_survives_best_candidate_fallback() -> None:
    from dataclasses import replace

    candidate = _candidate("one", "a", "こんにちは、皆さん。", "ja")
    candidate = replace(candidate, quality=replace(candidate.quality, quality_gate_score=0.3))
    result = ConfusionNetwork().resolve(
        (candidate,),
        canonical_span=SPAN,
        language="ja",
        inputs=_inputs(),
    )
    assert result.strategy == "best_candidate_low_confidence"
    assert result.text == "こんにちは、皆さん。"


def test_invalid_candidate_cannot_restore_text_or_punctuation() -> None:
    result = ConfusionNetwork().resolve(
        (_candidate("bad", "a", "こんにちは、皆さん。", "ja", valid=False),),
        canonical_span=SPAN,
        language="ja",
        inputs=_inputs(),
    )
    assert result.strategy == "inaudible_marker"
    assert not result.punctuation_sources


def test_native_timed_words_still_use_original_punctuation() -> None:
    result = ConfusionNetwork().resolve(
        (_candidate("one", "a", "Hello, world!", "en", ("Hello", "world")),),
        canonical_span=SPAN,
        language="en",
        inputs=_inputs(),
    )
    assert result.text == "Hello, world!"
    assert len(result.tokens) == 2


def test_mixed_candidate_projection_uses_selected_sources_and_real_offsets() -> None:
    from classscribe.consensus.models import FinalToken
    from classscribe.consensus.punctuation import restore_native_punctuation

    # The winning final character came from B, so A's sentence end cannot be copied.
    tokens = tuple(
        FinalToken(char, SPAN, 1.0, {"selected_candidate_id": "b" if i == 6 else "a"})
        for i, char in enumerate("今日は長い授業")
    )
    restored, evidence = restore_native_punctuation(
        "今日は長い授業",
        tokens,
        (_candidate("a", "a", "今日は、長い授業。", "ja"),),
    )
    assert restored == "今日は、長い授業"
    assert evidence[0]["boundary_count"] == 1


def test_normalized_matching_does_not_change_winning_characters() -> None:
    from classscribe.consensus.models import FinalToken
    from classscribe.consensus.punctuation import restore_native_punctuation

    target = "第2回です"
    tokens = (FinalToken(target, SPAN, 1.0, {"selected_candidate_id": "a"}),)
    restored, _ = restore_native_punctuation(
        target,
        tokens,
        (_candidate("a", "a", "第２回、です。", "ja"),),
    )
    assert restored == "第2回、です。"
