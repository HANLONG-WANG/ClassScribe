from __future__ import annotations

from classscribe.structure.models import StructureSegment
from classscribe.structure.stitching import (
    EmbeddingObservation,
    GlobalSpeakerTracker,
    OverlapSpeakerConstraint,
    SpeakerDecision,
    deduplicate_structure_segments,
    infer_overlap_constraints,
)
from classscribe.timeline import AudioSpan

REVISION = "c" * 40


def _segment(
    window: int,
    start: int,
    end: int,
    speaker: str,
    text: str,
    score: float = 0.5,
) -> StructureSegment:
    return StructureSegment(
        window_ordinal=window,
        span=AudioSpan(start, end),
        speaker_local=speaker,
        text=text,
        acoustic_events=(),
        source_model="moss_td_0_9b",
        source_revision=REVISION,
        selection_score=score,
        provenance={"absolute_samples": True, "adopted_as_final": False},
    )


def _observations(
    window: int, speaker: str, start: int, vector: tuple[float, ...]
) -> tuple[EmbeddingObservation, ...]:
    return (
        EmbeddingObservation(window, speaker, AudioSpan(start, start + 16_000), vector, 0.95),
        EmbeddingObservation(
            window,
            speaker,
            AudioSpan(start + 20_000, start + 36_000),
            vector,
            0.9,
        ),
    )


def test_multiple_nonoverlap_embeddings_match_swapped_local_labels_to_centroids() -> None:
    tracker = GlobalSpeakerTracker()
    first_segments = (
        _segment(0, 0, 100_000, "A", "teacher"),
        _segment(0, 100_000, 200_000, "B", "student"),
    )
    first = tracker.stitch_window(
        0,
        first_segments,
        (*_observations(0, "A", 0, (1.0, 0.0)), *_observations(0, "B", 100_000, (0.0, 1.0))),
    )
    second_segments = (
        _segment(1, 190_000, 300_000, "X", "same teacher"),
        _segment(1, 300_000, 400_000, "Y", "same student"),
    )
    second = tracker.stitch_window(
        1,
        second_segments,
        (
            *_observations(1, "X", 200_000, (0.99, 0.01)),
            *_observations(1, "Y", 300_000, (0.01, 0.99)),
        ),
    )
    assert second.mapping["X"] == first.mapping["A"]
    assert second.mapping["Y"] == first.mapping["B"]
    assert all(item.decision is SpeakerDecision.EMBEDDING_MATCH for item in second.diagnostics)


def test_one_embedding_is_low_reliability_and_creates_a_new_speaker() -> None:
    tracker = GlobalSpeakerTracker()
    initial = tracker.stitch_window(
        0,
        (_segment(0, 0, 100_000, "A", "base"),),
        _observations(0, "A", 0, (1.0, 0.0)),
    )
    weak = tracker.stitch_window(
        1,
        (_segment(1, 100_000, 200_000, "Z", "weak"),),
        (EmbeddingObservation(1, "Z", AudioSpan(110_000, 130_000), (1.0, 0.0), 0.99),),
    )
    assert weak.mapping["Z"] != initial.mapping["A"]
    assert weak.diagnostics[0].decision is SpeakerDecision.LOW_RELIABILITY_NEW


def test_strong_time_aligned_overlap_constraint_can_continue_identity() -> None:
    tracker = GlobalSpeakerTracker()
    first = tracker.stitch_window(
        0,
        (_segment(0, 0, 100_000, "A", "same boundary words"),),
        _observations(0, "A", 0, (1.0, 0.0)),
    )
    current = (_segment(1, 90_000, 100_000, "Q", "same boundary words"),)
    constraints = infer_overlap_constraints(first.segments, current)
    second = tracker.stitch_window(1, current, (), constraints)
    assert constraints[0].evidence_score >= 0.9
    assert second.mapping["Q"] == first.mapping["A"]
    assert second.diagnostics[0].decision is SpeakerDecision.OVERLAP_CONSTRAINT
    assert second.speaker_id_switches == 0


def test_speaker_id_switch_metric_detects_conflict_with_weaker_overlap_evidence() -> None:
    tracker = GlobalSpeakerTracker()
    first = tracker.stitch_window(
        0,
        (
            _segment(0, 0, 100_000, "A", "a"),
            _segment(0, 100_000, 200_000, "B", "b"),
        ),
        (*_observations(0, "A", 0, (1.0, 0.0)), *_observations(0, "B", 100_000, (0.0, 1.0))),
    )
    constraint = OverlapSpeakerConstraint(1, "C", first.mapping["A"], 0.85)
    second = tracker.stitch_window(
        1,
        (_segment(1, 190_000, 260_000, "C", "conflict"),),
        _observations(1, "C", 190_000, (0.0, 1.0)),
        (constraint,),
    )
    assert second.mapping["C"] == first.mapping["B"]
    assert second.overlap_links == 1
    assert second.speaker_id_switches == 1
    assert second.speaker_id_switch_rate == 1.0


def test_cross_window_duplicate_keeps_quality_winner_without_concatenation() -> None:
    earlier = _segment(0, 90_000, 130_000, "A", "repeated boundary", 0.4).assign_speaker(
        "SPEAKER_01"
    )
    later = _segment(1, 100_000, 140_000, "X", "repeated boundary", 0.9).assign_speaker(
        "SPEAKER_01"
    )
    distinct = _segment(1, 140_000, 200_000, "X", "new text", 0.6).assign_speaker("SPEAKER_01")
    kept, diagnostics = deduplicate_structure_segments((earlier, later, distinct))
    assert [item.text for item in kept] == ["repeated boundary", "new text"]
    assert kept[0].window_ordinal == 1
    assert len(diagnostics) == 1
    assert diagnostics[0].kept_window == 1
    assert diagnostics[0].dropped_window == 0
