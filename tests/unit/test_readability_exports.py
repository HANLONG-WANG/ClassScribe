import json
from pathlib import Path

import pytest
from classscribe.exports.models import ExportFormat, ExportLayer, ExportSegment, ExportView
from classscribe.exports.renderers import render_export
from classscribe.timeline import AudioSpan


def _segment(text: str, *, user_text: str | None = None) -> ExportSegment:
    return ExportSegment(
        "source",
        AudioSpan(16_000, 176_000),
        "zh",
        text,
        text,
        text,
        user_text,
        (),
        speaker="teacher",
    )


def test_paragraph_export_projects_long_source_without_fabricating_sentence_times() -> None:
    text = "一。二。三。四。五。六。七。"
    records = json.loads(
        render_export(
            (_segment(text),),
            output_format=ExportFormat.JSON,
            layer=ExportLayer.FAITHFUL,
            view=ExportView.READABLE_PARAGRAPHS,
        )
    )["records"]
    assert [record["text"] for record in records] == ["一。二。三。四。", "五。六。七。"]
    assert "".join(record["text"] for record in records) == text
    assert (
        render_export(
            (_segment(text),),
            output_format=ExportFormat.TXT,
            layer=ExportLayer.FAITHFUL,
            view=ExportView.READABLE_PARAGRAPHS,
        )
        == "一。二。三。四。\n\n五。六。七。\n"
    )
    for record in records:
        assert record["segment_ids"] == ["source"]
        assert (record["start_sample"], record["end_sample"]) == (16_000, 176_000)
        assert record["coarse_timing"] is True
        for sentence in record["sentences"]:
            assert sentence["timing_scope"] == "source_segment"
            assert (
                sentence["text"]
                == text[sentence["display_text_start"] : sentence["display_text_end"]]
            )
            assert (sentence["start_sample"], sentence["end_sample"]) == (16_000, 176_000)


def test_paragraph_export_matches_the_shared_frontend_boundaries() -> None:
    fixtures = json.loads((Path(__file__).parents[1] / "fixtures/readability.json").read_text())
    for example in fixtures["paragraphs"]:
        sources = example["sources"]
        segments = tuple(
            ExportSegment(
                str(index),
                AudioSpan(source["start_sample"], source["end_sample"]),
                source["language"],
                source["text"],
                source["text"],
                source["text"],
                None,
                (),
                speaker=source["speaker_id"],
                pause_before_ms=(source["start_sample"] - sources[index - 1]["end_sample"]) // 16
                if index
                else 0,
            )
            for index, source in enumerate(sources)
        )
        records = json.loads(
            render_export(
                segments,
                output_format=ExportFormat.JSON,
                layer=ExportLayer.FAITHFUL,
                view=ExportView.READABLE_PARAGRAPHS,
            )
        )["records"]
        assert [
            [sentence["text"] for sentence in record["sentences"]] for record in records
        ] == example["groups"]


@pytest.mark.parametrize("output_format", [ExportFormat.SRT, ExportFormat.VTT])
def test_reading_projection_cannot_change_subtitle_text_or_time(
    output_format: ExportFormat,
) -> None:
    segments = (_segment("一。二。三。四。五。六。七。"),)
    assert render_export(
        segments,
        output_format=output_format,
        layer=ExportLayer.FAITHFUL,
        view=ExportView.READABLE_PARAGRAPHS,
    ) == render_export(segments, output_format=output_format, layer=ExportLayer.FAITHFUL)


def test_paragraph_export_preserves_deliberately_empty_user_text() -> None:
    result = render_export(
        (_segment("需要删除的原文。", user_text=""),),
        output_format=ExportFormat.JSON,
        layer=ExportLayer.USER,
        view=ExportView.READABLE_PARAGRAPHS,
    )
    records = json.loads(result)["records"]
    assert len(records) == 1
    assert records[0]["text"] == ""
