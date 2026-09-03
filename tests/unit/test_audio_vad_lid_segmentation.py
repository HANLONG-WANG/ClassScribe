from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise

import pytest
from classscribe.audio import (
    BoundaryCandidate,
    BoundaryCue,
    BoundaryKind,
    FireRedLIDAdapter,
    FireRedVADAdapter,
    LanguageRouter,
    LIDObservation,
    SileroVADAdapter,
    SpeakerEmbedding,
    SpeechRegionResult,
    TimedToken,
    VADFrame,
    WebRTCVADAdapter,
    choose_structure_window_samples,
    make_structure_windows,
    make_transcript_chunks,
    match_speaker_embeddings,
    plan_boundary_dedup,
    sliding_lid_windows,
)
from classscribe.audio.vad import callable_backend
from classscribe.contracts import LanguageMode
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from hypothesis import given
from hypothesis import strategies as st


def probabilities(zh: float, ja: float, en: float) -> Mapping[LanguageMode, float]:
    return {
        LanguageMode.CHINESE: zh,
        LanguageMode.JAPANESE: ja,
        LanguageMode.ENGLISH: en,
    }


def test_vad_streaming_and_batch_have_identical_absolute_regions() -> None:
    frames = tuple(
        VADFrame(index * 1_600, (index + 1) * 1_600, 0.95 if 10 <= index < 30 else 0.05)
        for index in range(50)
    )
    backend = callable_backend(lambda _path, _streaming: frames)
    adapter = FireRedVADAdapter(backend, min_speech_ms=200, min_silence_ms=300, padding_ms=100)
    batch = adapter.analyze("fixture.wav", total_samples=50 * 1_600)
    session = adapter.stream(50 * 1_600)
    emitted: list[SpeechRegionResult] = []
    for frame in frames:
        emitted.extend(session.push(frame))
    emitted.extend(session.flush())

    assert tuple(emitted) == batch
    assert len(batch) == 1
    assert batch[0].span == AudioSpan(9 * 1_600, 31 * 1_600)
    assert batch[0].source == "firered_vad"
    assert batch[0].vad_score == 0.95


def test_vad_backend_receives_explicit_streaming_mode() -> None:
    modes: list[bool] = []

    def frames(_path: str, streaming: bool) -> tuple[VADFrame, ...]:
        modes.append(streaming)
        return (VADFrame(0, 4_000, 0.9),)

    adapter = FireRedVADAdapter(callable_backend(frames), min_speech_ms=100, padding_ms=0)
    adapter.analyze("x", total_samples=4_000, streaming=False)
    adapter.analyze("x", total_samples=4_000, streaming=True)
    assert modes == [False, True]


def test_vad_fallbacks_are_explicit_and_local_time_reset_is_rejected() -> None:
    frames = (VADFrame(0, 1_600, 0.9), VADFrame(1_600, 3_200, 0.9))
    backend = callable_backend(lambda _path, _streaming: frames)
    assert (
        SileroVADAdapter(backend, min_speech_ms=100).analyze("x", total_samples=3_200)[0].source
        == "silero_vad_fallback"
    )
    assert (
        WebRTCVADAdapter(backend, min_speech_ms=100).analyze("x", total_samples=3_200)[0].source
        == "webrtc_vad_fallback"
    )
    session = FireRedVADAdapter(backend).stream(4_000)
    session.push(VADFrame(1_600, 3_200, 0.9))
    with pytest.raises(ValueError, match="ordered"):
        session.push(VADFrame(0, 1_600, 0.9))


def test_manual_language_modes_completely_bypass_lid() -> None:
    router = LanguageRouter()
    for mode, prompt in (
        (LanguageMode.CHINESE, "zh"),
        (LanguageMode.JAPANESE, "Japanese"),
        (LanguageMode.ENGLISH, "English"),
    ):
        spans = router.route(mode, 10 * SAMPLE_RATE)
        assert len(spans) == 1
        assert spans[0].language is mode
        assert spans[0].decision["global_lid_bypassed"] is True
        assert spans[0].decision["model_language_prompt"] == prompt
        assert spans[0].decision["observations"] == []


def test_lid_uses_five_second_windows_two_point_five_step_and_two_window_hysteresis() -> None:
    windows = sliding_lid_windows(20 * SAMPLE_RATE)
    assert windows[0] == AudioSpan(0, 5 * SAMPLE_RATE)
    assert windows[1] == AudioSpan(5 * SAMPLE_RATE // 2, 15 * SAMPLE_RATE // 2)
    assert windows[-1].end_sample == 20 * SAMPLE_RATE
    values = [
        probabilities(0.05, 0.90, 0.05),
        probabilities(0.05, 0.91, 0.04),
        probabilities(0.05, 0.05, 0.90),  # one English term: do not switch
        probabilities(0.05, 0.88, 0.07),
        probabilities(0.05, 0.06, 0.89),
        probabilities(0.05, 0.05, 0.90),
        probabilities(0.05, 0.05, 0.90),
    ]
    observations = tuple(
        LIDObservation(window, value) for window, value in zip(windows, values, strict=True)
    )
    silence = 11 * SAMPLE_RATE
    spans = LanguageRouter().route(
        LanguageMode.AUTO_MIXED,
        20 * SAMPLE_RATE,
        observations=observations,
        silence_points=(silence,),
    )
    assert [span.language for span in spans] == [LanguageMode.JAPANESE, LanguageMode.ENGLISH]
    assert spans[0].span == AudioSpan(0, silence)
    assert spans[1].span == AudioSpan(silence, 20 * SAMPLE_RATE)
    assert spans[1].decision["transition_from"] == "ja"
    assert spans[1].decision["routing_hint"] == "prefer_unified_ja_en_model"
    assert spans[1].decision["threshold"] == 0.8
    assert spans[1].decision["consecutive_windows"] == 2
    assert spans[1].decision["observations"]


def test_initial_single_english_window_does_not_override_two_stable_japanese_windows() -> None:
    windows = sliding_lid_windows(10 * SAMPLE_RATE)
    values = (
        probabilities(0.03, 0.05, 0.92),
        probabilities(0.04, 0.90, 0.06),
        probabilities(0.03, 0.91, 0.06),
    )
    observations = tuple(
        LIDObservation(window, value) for window, value in zip(windows, values, strict=True)
    )
    spans = LanguageRouter().route(
        LanguageMode.AUTO_MIXED, 10 * SAMPLE_RATE, observations=observations
    )
    assert len(spans) == 1
    assert spans[0].language is LanguageMode.JAPANESE


def test_firered_lid_adapter_preserves_every_raw_probability() -> None:
    class Backend:
        def probabilities(self, audio_path: str, span: AudioSpan) -> Mapping[LanguageMode, float]:
            del audio_path, span
            return probabilities(0.8, 0.1, 0.1)

    observations = FireRedLIDAdapter(Backend()).observe("audio.wav", 8 * SAMPLE_RATE)
    assert len(observations) == 3
    assert all(item.probabilities[LanguageMode.CHINESE] == 0.8 for item in observations)


def test_ninety_minute_structure_windows_are_monotonic_bounded_and_overlapped() -> None:
    total = 90 * 60 * SAMPLE_RATE
    windows = make_structure_windows(total)
    assert windows[0].span.start_sample == 0
    assert windows[-1].span.end_sample == total
    assert all(window.span.end_sample <= total for window in windows)
    assert all(window.span.duration_samples <= 12 * 60 * SAMPLE_RATE for window in windows)
    assert all(window.span.duration_samples >= 4 * 60 * SAMPLE_RATE for window in windows)
    for previous, current in pairwise(windows):
        assert current.span.start_sample > previous.span.start_sample
        assert current.span.start_sample < previous.span.end_sample
        assert previous.overlap_after_samples == current.overlap_before_samples
        assert previous.overlap_after_samples >= 4 * SAMPLE_RATE


@given(st.integers(min_value=1, max_value=90 * 60 * SAMPLE_RATE))
def test_structure_windows_cover_every_sample_without_local_time_reset(total: int) -> None:
    windows = make_structure_windows(total)
    assert windows[0].span.start_sample == 0
    assert windows[-1].span.end_sample == total
    assert all(
        window.span.start_sample >= 0 and window.span.end_sample <= total for window in windows
    )
    assert all(
        left.span.start_sample < right.span.start_sample <= left.span.end_sample
        for left, right in pairwise(windows)
    )


@pytest.mark.parametrize(("minutes", "expected"), [(4, 4), (12, 12), (25, 20), (35, 30), (90, 90)])
def test_structure_window_adapts_only_to_safe_candidates(minutes: int, expected: int) -> None:
    assert (
        choose_structure_window_samples(minutes * 60 * SAMPLE_RATE) == expected * 60 * SAMPLE_RATE
    )


def test_continuous_speech_hard_splits_keep_context_on_both_sides_without_loss() -> None:
    extent = AudioSpan(100 * SAMPLE_RATE, 195 * SAMPLE_RATE)
    chunks = make_transcript_chunks(extent, ())
    assert chunks[0].core_span.start_sample == extent.start_sample
    assert chunks[-1].core_span.end_sample == extent.end_sample
    for left, right in pairwise(chunks):
        assert left.core_span.end_sample == right.core_span.start_sample
        assert left.core_span.duration_samples <= 30 * SAMPLE_RATE
        assert left.audio_span.end_sample - right.audio_span.start_sample >= int(1.6 * SAMPLE_RATE)
        assert left.audio_span.end_sample > left.core_span.end_sample
        assert right.audio_span.start_sample < right.core_span.start_sample
    assert all(extent.start_sample <= chunk.audio_span.start_sample for chunk in chunks)
    assert all(chunk.audio_span.end_sample <= extent.end_sample for chunk in chunks)


def test_natural_boundary_priority_and_distinct_segment_types() -> None:
    extent = AudioSpan(0, 50 * SAMPLE_RATE)
    chunks = make_transcript_chunks(
        extent,
        (
            BoundaryCue(18 * SAMPLE_RATE, BoundaryKind.LANGUAGE_SWITCH),
            BoundaryCue(20 * SAMPLE_RATE, BoundaryKind.SPEAKER_CHANGE),
        ),
    )
    assert chunks[0].core_span.end_sample == 20 * SAMPLE_RATE
    assert chunks[0].boundary_reason == BoundaryKind.SPEAKER_CHANGE.value
    assert chunks[0].audio_span == chunks[0].core_span
    assert not hasattr(chunks[0], "span")
    structure = make_structure_windows(50 * SAMPLE_RATE, window_samples=4 * 60 * SAMPLE_RATE)[0]
    assert hasattr(structure, "span")


def test_boundary_dedup_uses_token_text_and_absolute_time_not_character_count() -> None:
    left = BoundaryCandidate(
        (
            TimedToken("授業", AudioSpan(10_000, 12_000)),
            TimedToken("AI", AudioSpan(12_000, 14_000)),
        ),
        quality_score=0.9,
    )
    right = BoundaryCandidate(
        (
            TimedToken("AI!", AudioSpan(12_100, 14_100)),
            TimedToken("です", AudioSpan(14_100, 16_000)),
        ),
        quality_score=0.7,
    )
    plan = plan_boundary_dedup(left, right)
    assert plan.matched_tokens == 1
    assert plan.drop_right_prefix == 1
    assert plan.drop_left_suffix == 0
    assert plan.method == "token_text_and_absolute_time_alignment"

    unrelated = plan_boundary_dedup(
        left,
        BoundaryCandidate((TimedToken("different", AudioSpan(12_000, 14_000)),), 1.0),
    )
    assert unrelated.matched_tokens == 0


def test_structure_overlap_matches_local_speakers_to_existing_global_labels() -> None:
    matches = match_speaker_embeddings(
        (
            SpeakerEmbedding("S01", (1.0, 0.0)),
            SpeakerEmbedding("S02", (0.0, 1.0)),
        ),
        (
            SpeakerEmbedding("local-S01", (0.01, 0.99)),
            SpeakerEmbedding("local-S02", (0.98, 0.02)),
        ),
    )
    assert {(item.next_local_label, item.previous_global_label) for item in matches} == {
        ("local-S01", "S02"),
        ("local-S02", "S01"),
    }
