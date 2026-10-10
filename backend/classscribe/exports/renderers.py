"""Deterministic renderers for all six ClassScribe export formats."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from classscribe.asr.variants import TraditionalChineseConverter
from classscribe.exports.models import (
    ExportFormat,
    ExportLayer,
    ExportSegment,
    ExportView,
)
from classscribe.exports.subtitles import build_subtitle_cues
from classscribe.readability import PARAGRAPH_PAUSE_MS, paragraph_slices
from classscribe.recovery import atomic_write_text
from classscribe.timeline import format_sample_timestamp

TextTransform = Callable[[str, str], str]


def render_export(
    segments: tuple[ExportSegment, ...],
    *,
    output_format: ExportFormat,
    layer: ExportLayer,
    view: ExportView = ExportView.SENTENCES,
    traditional_chinese: bool = False,
) -> str:
    ordered = _validate_segments(segments)
    transform = _traditional_transform if traditional_chinese else _identity_transform
    if output_format is ExportFormat.SRT:
        return _render_srt(ordered, layer, transform)
    if output_format is ExportFormat.VTT:
        return _render_vtt(ordered, layer, transform)
    records = _records(ordered, layer, view, transform)
    if output_format is ExportFormat.TXT:
        return "\n\n".join(str(record["text"]) for record in records) + ("\n" if records else "")
    if output_format is ExportFormat.MARKDOWN:
        return _render_markdown(records)
    if output_format is ExportFormat.CSV:
        return _render_csv(records)
    if output_format is ExportFormat.JSON:
        return (
            json.dumps(
                {
                    "schema_version": 1,
                    "timeline": "absolute_int64_samples_at_16000_hz",
                    "requested_layer": layer.value,
                    "view": view.value,
                    "traditional_chinese_display_variant": traditional_chinese,
                    "records": records,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    raise ValueError(f"unsupported export format: {output_format}")


def write_export(
    destination: Path,
    segments: tuple[ExportSegment, ...],
    *,
    output_format: ExportFormat,
    layer: ExportLayer,
    view: ExportView = ExportView.SENTENCES,
    traditional_chinese: bool = False,
) -> None:
    expected_suffix = ".md" if output_format is ExportFormat.MARKDOWN else f".{output_format.value}"
    if destination.suffix.lower() != expected_suffix:
        raise ValueError(f"export destination must end with {expected_suffix}")
    atomic_write_text(
        destination,
        render_export(
            segments,
            output_format=output_format,
            layer=layer,
            view=view,
            traditional_chinese=traditional_chinese,
        ),
    )


def _records(
    segments: tuple[ExportSegment, ...],
    layer: ExportLayer,
    view: ExportView,
    transform: TextTransform,
) -> list[dict[str, Any]]:
    sentence_records = [_segment_record(item, layer, transform) for item in segments]
    if view is ExportView.SENTENCES:
        return sentence_records
    boundaries = [
        bool(
            index
            and (
                segment.pause_before_ms >= PARAGRAPH_PAUSE_MS
                or segment.chapter_marker is not None
                or segment.semantic_boundary_before
                or segment.speaker != segments[index - 1].speaker
                or segment.language != segments[index - 1].language
            )
        )
        for index, segment in enumerate(segments)
    ]
    texts = [str(record["text"]) for record in sentence_records]
    paragraphs: list[dict[str, Any]] = []
    for group in paragraph_slices(texts, boundaries):
        records: list[dict[str, Any]] = []
        for piece in group:
            record = dict(sentence_records[piece.source_index])
            record["text"] = texts[piece.source_index][piece.start : piece.end]
            # Reading fragments keep the source range; they are not aligned subtitles.
            record["display_text_start"] = piece.start
            record["display_text_end"] = piece.end
            record["timing_scope"] = "source_segment"
            if piece.start != 0 or piece.end != len(texts[piece.source_index]):
                record["coarse_timing"] = True
            records.append(record)
        paragraphs.append(_merge_records(records))
    return paragraphs


def _segment_record(
    segment: ExportSegment, layer: ExportLayer, transform: TextTransform
) -> dict[str, Any]:
    text, resolved = segment.text_for(layer)
    tokens = segment.tokens_for(layer)
    start = tokens[0].span.start_sample if tokens else segment.span.start_sample
    end = tokens[-1].span.end_sample if tokens else segment.span.end_sample
    return {
        "segment_ids": [segment.segment_id],
        "start_sample": start,
        "end_sample": end,
        "start": format_sample_timestamp(start),
        "end": format_sample_timestamp(end),
        "speaker": segment.speaker,
        "language": segment.language,
        "requested_layer": layer.value,
        "resolved_layer": resolved.value,
        "timing_quality": segment.timing_quality,
        "coarse_timing": segment.coarse_timing,
        "text": transform(text, segment.language),
        "raw_text": segment.raw_text,
        "faithful_text": segment.faithful_text,
        "smart_corrected_text": segment.smart_corrected_text,
        "user_text": segment.user_text,
        "tokens": [
            {
                "text": token.text,
                "start_sample": token.span.start_sample,
                "end_sample": token.span.end_sample,
                "protected_group": token.protected_group,
                "provenance": token.provenance,
            }
            for token in tokens
        ],
        "source": {
            "recording_id": segment.source_recording_id,
            "clip_start_sample": segment.source_offset_sample,
            "start_sample": segment.source_offset_sample + start
            if segment.timing_quality != "invalid"
            else None,
            "end_sample": segment.source_offset_sample + end
            if segment.timing_quality != "invalid"
            else None,
        }
        if segment.source_recording_id and segment.source_offset_sample is not None
        else None,
    }


def _merge_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    first = records[0]
    text = ""
    previous: dict[str, Any] | None = None
    for record in records:
        piece = str(record["text"])
        separator = ""
        if (
            previous is not None
            and previous["segment_ids"] != record["segment_ids"]
            and text
            and piece
            and not text[-1].isspace()
            and not piece[0].isspace()
            and record["language"] not in {"zh", "ja"}
        ):
            separator = " "
        text += separator + piece
        previous = record
    start = min(record["start_sample"] for record in records)
    end = max(record["end_sample"] for record in records)
    return {
        "segment_ids": list(
            dict.fromkeys(item for record in records for item in record["segment_ids"])
        ),
        "start_sample": start,
        "end_sample": end,
        "start": format_sample_timestamp(start),
        "end": format_sample_timestamp(end),
        "speaker": first["speaker"],
        "language": first["language"],
        "requested_layer": first["requested_layer"],
        "resolved_layer": first["resolved_layer"],
        "timing_quality": (
            first["timing_quality"]
            if all(record["timing_quality"] == first["timing_quality"] for record in records)
            else "mixed"
        ),
        "coarse_timing": any(record["coarse_timing"] for record in records),
        "text": text,
        "sentences": records,
    }


def _render_markdown(records: list[dict[str, Any]]) -> str:
    lines = ["# ClassScribe transcript", ""]
    for record in records:
        speaker = f" · {record['speaker']}" if record["speaker"] else ""
        timing = " · 粗时间" if record["coarse_timing"] else ""
        lines.extend((f"## {record['start']}{speaker}{timing}", "", str(record["text"]), ""))
    return "\n".join(lines).rstrip() + "\n"


def _render_csv(records: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=(
            "segment_ids",
            "start_sample",
            "end_sample",
            "start",
            "end",
            "speaker",
            "language",
            "requested_layer",
            "resolved_layer",
            "timing_quality",
            "coarse_timing",
            "text",
        ),
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                key: "|".join(record[key]) if key == "segment_ids" else record.get(key)
                for key in writer.fieldnames
            }
        )
    return output.getvalue()


def _render_srt(
    segments: tuple[ExportSegment, ...], layer: ExportLayer, transform: TextTransform
) -> str:
    blocks: list[str] = []
    coarse_ids = {segment.segment_id for segment in segments if segment.coarse_timing}
    for index, cue in enumerate(build_subtitle_cues(segments, layer), start=1):
        language = _segment_language(segments, cue.segment_ids[0])
        text = transform("\n".join(cue.lines), language)
        if cue.coarse_timing or any(segment_id in coarse_ids for segment_id in cue.segment_ids):
            text = f"[粗时间] {text}"
        start = format_sample_timestamp(cue.start_sample).replace(".", ",")
        end = format_sample_timestamp(cue.end_sample).replace(".", ",")
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _render_vtt(
    segments: tuple[ExportSegment, ...], layer: ExportLayer, transform: TextTransform
) -> str:
    blocks = ["WEBVTT"]
    coarse_ids = {segment.segment_id for segment in segments if segment.coarse_timing}
    for cue in build_subtitle_cues(segments, layer):
        language = _segment_language(segments, cue.segment_ids[0])
        text = transform(chr(10).join(cue.lines), language)
        if cue.coarse_timing or any(segment_id in coarse_ids for segment_id in cue.segment_ids):
            text = f"[粗时间] {text}"
        blocks.append(
            f"{format_sample_timestamp(cue.start_sample)} --> "
            f"{format_sample_timestamp(cue.end_sample)}\n"
            f"{text}"
        )
    return "\n\n".join(blocks) + "\n"


def _validate_segments(segments: tuple[ExportSegment, ...]) -> tuple[ExportSegment, ...]:
    ordered = tuple(sorted(segments, key=lambda item: (item.span.start_sample, item.segment_id)))
    previous_start = -1
    for segment in ordered:
        if segment.span.start_sample < previous_start:
            raise ValueError("export segment ordering is invalid")
        previous_start = segment.span.start_sample
    return ordered


def _segment_language(segments: tuple[ExportSegment, ...], segment_id: str) -> str:
    return next(item.language for item in segments if item.segment_id == segment_id)


def _identity_transform(text: str, _language: str) -> str:
    return text


def _traditional_transform(text: str, language: str) -> str:
    if language != "zh":
        return text
    return TraditionalChineseConverter().from_faithful_simplified(text).text
