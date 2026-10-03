import json
from itertools import pairwise
from pathlib import Path

from classscribe.readability import paragraph_slices, sentence_spans
from hypothesis import given
from hypothesis import strategies as st

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/readability.json").read_text())


def test_sentence_boundaries_match_the_shared_multilingual_examples() -> None:
    for example in FIXTURES["sentences"]:
        text = example["text"]
        assert [text[start:end] for start, end in sentence_spans(text)] == example["pieces"]


def test_paragraph_boundaries_match_the_shared_frontend_examples() -> None:
    for example in FIXTURES["paragraphs"]:
        sources = example["sources"]
        boundaries = [
            bool(
                index
                and (
                    source["speaker_id"] != sources[index - 1]["speaker_id"]
                    or source["language"] != sources[index - 1]["language"]
                    or (source["start_sample"] - sources[index - 1]["end_sample"]) // 16 >= 1200
                )
            )
            for index, source in enumerate(sources)
        ]
        texts = [source["text"] for source in sources]
        groups = paragraph_slices(texts, boundaries)
        assert [
            [texts[piece.source_index][piece.start : piece.end] for piece in group]
            for group in groups
        ] == example["groups"]


@given(st.text())
def test_sentence_projection_never_loses_or_reorders_original_characters(text: str) -> None:
    spans = sentence_spans(text)
    assert "".join(text[start:end] for start, end in spans) == text
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    assert all(left[1] == right[0] for left, right in pairwise(spans))


def test_paragraph_length_limit_is_soft_and_does_not_cut_a_sentence() -> None:
    first, second = "甲" * 160 + "。", "乙" * 160 + "。"
    groups = paragraph_slices([first + second], [False])
    assert len(groups) == 2
    assert [(group[0].start, group[0].end) for group in groups] == [(0, 161), (161, 322)]
    assert len(paragraph_slices(["甲" * 500], [False])) == 1


def test_short_tail_is_balanced_only_across_a_size_boundary() -> None:
    texts = ["一。二。三。四。五。六。", "七。"]
    assert [len(group) for group in paragraph_slices(texts, [False, False])] == [4, 3]
    assert [len(group) for group in paragraph_slices(texts, [False, True])] == [6, 1]
    assert [len(group) for group in paragraph_slices([texts[0] + "\n\n" + texts[1]], [False])] == [
        6,
        1,
    ]
