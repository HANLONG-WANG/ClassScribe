import json

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
