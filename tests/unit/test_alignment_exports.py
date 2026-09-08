from __future__ import annotations

import json
from pathlib import Path

import pytest
from classscribe.alignment import (
    AlignedToken,
    AlignmentGateDecision,
    AlignmentGateInput,
    TimingEvidence,
    TimingSource,
    build_alignment_request,
    evaluate_alignment_gate,
    parse_alignment_response,
    select_canonical_timing,
    validate_timing,
)
from classscribe.exports import (
    ExportFormat,
    ExportLayer,
    ExportSegment,
    ExportToken,
    ExportView,
    build_subtitle_cues,
    render_export,
    write_export,
)
from classscribe.models.registry import load_registry
from classscribe.quality.models import QualityIssue
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from classscribe_protocol import RPCResponse

ROOT = Path(__file__).resolve().parents[2]
SPAN = AudioSpan(10 * SAMPLE_RATE, 14 * SAMPLE_RATE)
VOICED = (AudioSpan(10 * SAMPLE_RATE, 13 * SAMPLE_RATE),)


def _evidence(source: TimingSource, *, reliable: bool = True) -> TimingEvidence:
    return TimingEvidence(
        source,
        SPAN,
        "one two",
        (
            AlignedToken("one", AudioSpan(10 * SAMPLE_RATE, 11 * SAMPLE_RATE)),
            AlignedToken("two", AudioSpan(11 * SAMPLE_RATE, 13 * SAMPLE_RATE)),
        ),
        reliable,
        True,
        alignment_cost=0.1,
        vad_gap_error_ms=100,
    )


def _gate(*issues: QualityIssue) -> AlignmentGateDecision:
    return evaluate_alignment_gate(
        AlignmentGateInput(SPAN, "one two", "en", "en", 3 * SAMPLE_RATE, 0.9, issues),
        registry_safe_seconds=300,
    )


def test_forced_alignment_gate_applies_short_text_coverage_loop_and_language_rules() -> None:
    accepted = _gate()
    assert accepted.allowed and accepted.safe_max_seconds == 30
    rejected = evaluate_alignment_gate(
        AlignmentGateInput(
            AudioSpan(0, 31 * SAMPLE_RATE),
            "loop loop",
            "en",
            "ja",
            4 * SAMPLE_RATE,
            0.4,
            (QualityIssue.REPEATED_NGRAM,),
        ),
        registry_safe_seconds=300,
    )
    assert not rejected.allowed
    assert {
        "segment_reaches_safe_duration_limit",
        "language_mismatch",
        "decode_loop_present",
    } <= set(rejected.reasons)


def test_canonical_timing_priority_and_explicit_coarse_fallback() -> None:
    gate = _gate()
    native = _evidence(TimingSource.NATIVE_WORD)
    moss = _evidence(TimingSource.MOSS_STRUCTURE)
    vad = _evidence(TimingSource.VAD_COARSE)
    selected = select_canonical_timing(
        final_text="one two",
        canonical_span=SPAN,
        voiced_spans=VOICED,
        native=native,
        moss_structure=moss,
        alignment_gate=gate,
        run_forced_aligner=lambda: _evidence(TimingSource.QWEN_FORCED),
        vad_coarse=vad,
    )
    assert selected.source is TimingSource.NATIVE_WORD
    fallback = select_canonical_timing(
        final_text="one two",
        canonical_span=SPAN,
        voiced_spans=VOICED,
        native=None,
        moss_structure=None,
        alignment_gate=gate,
        run_forced_aligner=lambda: (_ for _ in ()).throw(RuntimeError("bad alignment")),
        vad_coarse=vad,
    )
    assert fallback.source is TimingSource.VAD_COARSE and fallback.coarse_timing
    assert any("forced_aligner_failed" in reason for reason in fallback.fallback_reasons)


def test_alignment_validation_rejects_high_cost_or_changed_text() -> None:
    changed = TimingEvidence(
        TimingSource.QWEN_FORCED,
        SPAN,
        "one two",
        (AlignedToken("wrong", AudioSpan(10 * SAMPLE_RATE, 13 * SAMPLE_RATE)),),
        True,
        False,
        alignment_cost=0.9,
    )
    validation = validate_timing(changed, VOICED)
    assert not validation.valid
    assert "aligned_tokens_changed_or_omitted_final_text" in validation.reasons
    assert "alignment_cost_too_high" in validation.reasons


def test_qwen_alignment_rpc_preserves_text_and_absolute_samples(tmp_path: Path) -> None:
    entry = load_registry(ROOT / "config/model-registry.v1.yaml").model("qwen3_forced_aligner_0_6b")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"not opened by request construction")
    gate = _gate()
    request = build_alignment_request(
        request_id="align-1",
        job_id="job",
        audio_path=audio,
        entry=entry,
        canonical_span=SPAN,
        final_text="one two",
        language="en",
        gate=gate,
    )
    response = RPCResponse(
        "align-1",
        "job",
        True,
        entry.id,
        entry.revision,
        raw_text="one two",
        normalized_text="one two",
        language="en",
        segments=(
            {
                "start_sample": SPAN.start_sample,
                "end_sample": SPAN.end_sample,
                "text": "one two",
                "words": [
                    {
                        "text": "one",
                        "start_sample": SPAN.start_sample,
                        "end_sample": 11 * SAMPLE_RATE,
                    },
                    {
                        "text": "two",
                        "start_sample": 11 * SAMPLE_RATE,
                        "end_sample": 13 * SAMPLE_RATE,
                    },
                ],
            },
        ),
    )
    parsed = parse_alignment_response(response, request, entry, SPAN)
    assert parsed.final_text == "one two"
    assert parsed.tokens[1].span.end_sample == 13 * SAMPLE_RATE


def _export_segment() -> ExportSegment:
    tokens = (
        ExportToken("We", AudioSpan(16000, 24000)),
        ExportToken("used", AudioSpan(24000, 32000)),
        ExportToken("20", AudioSpan(32000, 40000), protected_group="dose"),
        ExportToken("mg", AudioSpan(40000, 48000), protected_group="dose"),
        ExportToken("ClassScribe.", AudioSpan(48000, 64000), protected_group="product"),
    )
    return ExportSegment(
        "segment-1",
        AudioSpan(0, 80_000),
        "en",
        "we used 20 mg classscribe",
        "We used 20 mg ClassScribe.",
        "We used 20 mg ClassScribe.",
        None,
        tokens,
        smart_tokens=tokens,
        speaker="Teacher",
    )


@pytest.mark.parametrize("output_format", tuple(ExportFormat))
def test_all_six_formats_export_each_layer_from_final_time(
    output_format: ExportFormat, tmp_path: Path
) -> None:
    segment = _export_segment()
    rendered = render_export(
        (segment,),
        output_format=output_format,
        layer=ExportLayer.USER,
        view=ExportView.SENTENCES,
    )
    assert "ClassScribe" in rendered
    assert "00:00:01" in rendered or output_format is ExportFormat.TXT
    suffix = ".md" if output_format is ExportFormat.MARKDOWN else f".{output_format.value}"
    destination = tmp_path / f"transcript{suffix}"
    write_export(
        destination,
        (segment,),
        output_format=output_format,
        layer=ExportLayer.USER,
    )
    assert destination.read_text(encoding="utf-8") == rendered
    if output_format is ExportFormat.JSON:
        data = json.loads(rendered)
        assert data["records"][0]["resolved_layer"] == "smart"
        assert data["timeline"].endswith("16000_hz")


def test_subtitle_has_at_most_two_lines_and_never_splits_protected_number_unit() -> None:
    cues = build_subtitle_cues((_export_segment(),), ExportLayer.SMART)
    assert cues and all(len(cue.lines) <= 2 for cue in cues)
    assert cues[0].start_sample == 16_000
    assert cues[-1].end_sample == 64_000
    rendered = "\n".join(line for cue in cues for line in cue.lines)
    assert "20 mg" in rendered


def test_readable_paragraph_view_uses_semantic_boundaries_without_changing_times() -> None:
    first = _export_segment()
    second = ExportSegment(
        "segment-2",
        AudioSpan(80_000, 160_000),
        "en",
        "next",
        "Next.",
        "Next.",
        None,
        (ExportToken("Next.", AudioSpan(96_000, 128_000)),),
        pause_before_ms=1500,
    )
    rendered = render_export(
        (first, second),
        output_format=ExportFormat.JSON,
        layer=ExportLayer.FAITHFUL,
        view=ExportView.READABLE_PARAGRAPHS,
    )
    records = json.loads(rendered)["records"]
    assert len(records) == 2
    assert records[0]["start_sample"] == 16000
    assert records[1]["start_sample"] == 96000


def test_active_text_without_tokens_exports_with_segment_timing() -> None:
    from classscribe.exports.models import ExportLayer, ExportSegment
    from classscribe.exports.subtitles import build_subtitle_cues

    segment = ExportSegment(
        "derived",
        AudioSpan(16000, 48000),
        "en",
        "Split text",
        "Split text",
        "Split text",
        "Split text",
        (),
    )
    for layer in ExportLayer:
        (cue,) = build_subtitle_cues((segment,), layer)
        assert cue.lines == ("Split text",)
        assert (cue.start_sample, cue.end_sample) == (16000, 48000)
