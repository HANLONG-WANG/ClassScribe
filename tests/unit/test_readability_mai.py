import json
from itertools import pairwise

import pytest
from classscribe.classroom.mai import parse_mai_response


def _response(text: str, words: list[str]) -> bytes:
    return json.dumps(
        {
            "phrases": [
                {
                    "text": text,
                    "offsetMilliseconds": 0,
                    "durationMilliseconds": 10_000,
                    "words": [
                        {
                            "text": word,
                            "offsetMilliseconds": index * 200,
                            "durationMilliseconds": 100,
                        }
                        for index, word in enumerate(words)
                    ],
                }
            ]
        }
    ).encode()


def test_mai_splits_when_sentence_marks_and_quotes_are_outside_words() -> None:
    text = "「今日は晴れです。」 次は雨です。"
    result = parse_mai_response(_response(text, ["今日は晴れです", "次は雨です"]), 160_000, "ja")
    assert [phrase.text for phrase in result] == ["「今日は晴れです。」", "次は雨です。"]
    assert [(phrase.start, phrase.end) for phrase in result] == [(0, 1600), (3200, 4800)]
    assert [word[0] for phrase in result for word in phrase.words] == [
        "「今日は晴れです。」",
        "次は雨です。",
    ]


def test_mai_does_not_split_titles_decimals_or_a_closing_quote() -> None:
    result = parse_mai_response(
        _response(
            'Dr. Smith measured 3.14 mg. "Next sentence!"',
            ["Dr.", "Smith", "measured", "3.14", "mg.", "Next", "sentence"],
        ),
        160_000,
        "en",
    )
    assert [phrase.text for phrase in result] == [
        "Dr. Smith measured 3.14 mg.",
        '"Next sentence!"',
    ]
    assert [(phrase.start, phrase.end) for phrase in result] == [(0, 14_400), (16_000, 20_800)]


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("First. Missing words.", ["First"]),
        ("The value is 3.14.", ["The", "value", "is", "314"]),
        ("Don't change it.", ["Dont", "change", "it"]),
        ("Use 3,000 units.", ["Use", "3000", "units"]),
    ],
)
def test_mai_tolerance_does_not_hide_missing_or_changed_content(
    text: str, words: list[str]
) -> None:
    result = parse_mai_response(_response(text, words), 160_000, "en")
    assert len(result) == 1
    assert result[0].text == text
    assert result[0].words == ()


def test_mai_preserves_separate_punctuation_without_inventing_word_times() -> None:
    result = parse_mai_response(
        _response("Hello. Next!", ["Hello", ".", "Next", "!"]), 160_000, "en"
    )
    assert [phrase.text for phrase in result] == ["Hello.", "Next!"]
    assert [(phrase.start, phrase.end) for phrase in result] == [(0, 4800), (6400, 11200)]


def test_mai_preserves_leading_decimal_points_and_their_original_word_times() -> None:
    result = parse_mai_response(
        _response(
            "Use .5 mg and -.25 g. Next sentence.",
            ["Use", ".5", "mg", "and", "-.25", "g", "Next", "sentence"],
        ),
        160_000,
        "en",
    )
    assert [phrase.text for phrase in result] == ["Use .5 mg and -.25 g.", "Next sentence."]
    assert [(phrase.start, phrase.end) for phrase in result] == [(0, 17_600), (19_200, 24_000)]
    incomplete = parse_mai_response(_response("Use .5 mg.", ["Use", "5", "mg"]), 160_000, "en")
    assert incomplete[0].text == "Use .5 mg."
    assert incomplete[0].words == ()


def _timed(text: str, words: list[tuple[str, int, int]], end: int = 10_000) -> bytes:
    return json.dumps(
        {
            "phrases": [
                {
                    "text": text,
                    "offsetMilliseconds": 0,
                    "durationMilliseconds": end,
                    "words": [
                        {"text": word, "offsetMilliseconds": start, "durationMilliseconds": length}
                        for word, start, length in words
                    ],
                }
            ]
        }
    ).encode()


def test_mai_trims_overlapping_word_times_instead_of_dropping_the_split() -> None:
    # MAI reports a later word starting inside, or even entirely within, its predecessor.
    words = [("一", 0, 500), ("。", 400, 80), ("二", 600, 100), ("三", 650, 20), ("。", 800, 80)]
    result = parse_mai_response(_timed("一。二三。", words), 160_000, "ja")
    assert [phrase.text for phrase in result] == ["一。", "二三。"]
    times = [(start, end) for phrase in result for _, start, end in phrase.words]
    assert all(start < end for start, end in times)
    assert all(left[1] <= right[0] for left, right in pairwise(times))


def test_mai_clips_trailing_punctuation_to_the_phrase_end() -> None:
    words = [("一", 0, 100), ("。", 200, 80), ("二", 300, 100), ("。", 450, 80)]
    result = parse_mai_response(_timed("一。二。", words, end=500), 160_000, "ja")
    assert [phrase.text for phrase in result] == ["一。", "二。"]
    assert result[-1].end == 8000


def test_mai_collapses_words_reported_before_their_predecessor() -> None:
    # Chunk seams can report the next sentence seconds before the previous one ends.
    words = [
        ("一", 3000, 100),
        ("。", 3100, 80),
        ("二", 100, 100),
        ("三", 3300, 80),
        ("。", 3400, 80),
    ]
    result = parse_mai_response(_timed("一。二三。", words), 160_000, "ja")
    assert [phrase.text for phrase in result] == ["一。", "二三。"]
    assert result[1].words[0][1:] == (3180 * 16, 3180 * 16)
    assert result[1].start == 3180 * 16


def test_mai_returns_words_attached_to_the_previous_phrase() -> None:
    def phrase(text: str, start: int, words: list[tuple[str, int]]) -> dict[str, object]:
        return {
            "text": text,
            "offsetMilliseconds": start,
            "durationMilliseconds": 1000,
            "words": [
                {"text": word, "offsetMilliseconds": offset, "durationMilliseconds": 50}
                for word, offset in words
            ],
        }

    payload = json.dumps(
        {
            "phrases": [
                phrase(
                    "名前です。",
                    0,
                    [("名", 0), ("前", 100), ("で", 200), ("す", 300), ("。", 400), ("TSMC", 900)],
                ),
                phrase("TSMCね。", 1000, [("ね", 1500), ("。", 1600)]),
            ]
        }
    ).encode()
    result = parse_mai_response(payload, 160_000, "ja")
    assert [phrase.text for phrase in result] == ["名前です。", "TSMCね。"]
    assert [word[0] for word in result[1].words] == ["TSMC", "ね", "。"]
    assert result[1].start == 1000 * 16


def test_mai_corrects_short_phrase_locale_guesses_between_cjk_neighbours() -> None:
    def phrase(text: str, locale: str, start: int) -> dict[str, object]:
        return {
            "text": text,
            "locale": locale,
            "offsetMilliseconds": start,
            "durationMilliseconds": 500,
        }

    payload = json.dumps(
        {
            "phrases": [
                phrase("今日は故障率の話をします。", "ja-JP", 0),
                phrase("一定。", "zh-CN", 1000),
                phrase("DFRとね。", "zh-CN", 2000),
                phrase("Dependability.", "en-US", 3000),
                phrase("それでは始めましょう、よろしくお願いします。", "ja-JP", 4000),
                phrase("这是一段足够长的中文内容、不应被改成日语。", "zh-CN", 5000),
            ]
        }
    ).encode()
    result = parse_mai_response(payload, 160_000, "auto_mixed")
    assert [phrase.language for phrase in result] == ["ja", "ja", "ja", "en", "ja", "zh"]


def test_mai_splits_a_word_that_straddles_two_phrases() -> None:
    payload = json.dumps(
        {
            "phrases": [
                {
                    "text": "ここが0.003。",
                    "offsetMilliseconds": 0,
                    "durationMilliseconds": 1000,
                    "words": [
                        {"text": text, "offsetMilliseconds": start, "durationMilliseconds": 50}
                        for text, start in [
                            ("こ", 0),
                            ("こ", 100),
                            ("が", 200),
                            ("0.003。0.997", 300),
                        ]
                    ],
                },
                {
                    "text": "0.997と。",
                    "offsetMilliseconds": 1000,
                    "durationMilliseconds": 1000,
                    "words": [
                        {"text": text, "offsetMilliseconds": start, "durationMilliseconds": 50}
                        for text, start in [("と", 1200), ("。", 1300)]
                    ],
                },
            ]
        }
    ).encode()
    result = parse_mai_response(payload, 160_000, "ja")
    assert [phrase.text for phrase in result] == ["ここが0.003。", "0.997と。"]
    assert all(phrase.words for phrase in result)
