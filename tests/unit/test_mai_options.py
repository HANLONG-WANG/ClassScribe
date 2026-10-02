from typing import Any

import pytest
from classscribe.api.schemas import JobCreate
from classscribe.classroom.mai import request_definition
from classscribe.classroom.mai_options import MaiTranscriptionOptions
from classscribe.contracts import LanguageMode
from classscribe.errors import ClassScribeError
from pydantic import ValidationError


@pytest.mark.parametrize("style", ["verbatim", "clean"])
@pytest.mark.parametrize("timestamps", ["word", "segment", "none"])
@pytest.mark.parametrize("diarization", [True, False])
def test_user_options_reach_the_mai_definition(
    style: str, timestamps: str, diarization: bool
) -> None:
    definition = request_definition(
        {
            "language": "ja",
            "speaker_count": "1",
            "mai_options": {
                "transcribe_style": style,
                "timestamps": timestamps,
                "diarization": diarization,
                "locale": "fr",
                "profanity_filter_mode": "None",
            },
        },
        [],
    )
    assert definition == {
        "enhancedMode": {
            "enabled": True,
            "model": "MAI-Transcribe-2",
            "modelOptions": {"transcribeStyle": style, "timestamps": timestamps},
        },
        "diarization": {"enabled": diarization},
        "locales": ["fr"],
        "profanityFilterMode": "None",
    }
    assert "maxSpeakers" not in definition["diarization"]


@pytest.mark.parametrize("mode", ["None", "Masked", "Removed", "Tags"])
def test_profanity_mode_and_automatic_language(mode: str) -> None:
    definition = request_definition(
        {"language": "ja", "mai_options": {"locale": None, "profanity_filter_mode": mode}},
        [],
    )
    assert definition["profanityFilterMode"] == mode
    assert "locales" not in definition


@pytest.mark.parametrize("weight", [None, 0, 1.5, 2])
def test_glossary_and_manual_phrases_are_merged_with_one_weight(weight: float | None) -> None:
    definition = request_definition(
        {
            "mai_options": {
                "phrases": [" GPU ", "manual term", "manual term"],
                "phrase_biasing_weight": weight,
            }
        },
        ["GPU", "course term", " GPU "],
    )
    expected: dict[str, Any] = {"phrases": ["GPU", "course term", "manual term"]}
    if weight is not None:
        expected["biasingWeight"] = weight
    assert definition["phraseList"] == expected
    assert "phraseList" not in request_definition({"mai_options": {}}, [])


@pytest.mark.parametrize(
    "invalid",
    [
        {"timestamps": "sentence"},
        {"transcribe_style": "summary"},
        {"locale": "unknown"},
        {"diarization": "false"},
        {"diarization": 1},
        {"profanity_filter_mode": "invalid"},
        {"phrase_biasing_weight": -0.1},
        {"phrase_biasing_weight": 2.1},
        {"phrase_biasing_weight": True},
        {"phrase_biasing_weight": float("nan")},
        {"phrase_biasing_weight": float("inf")},
        {"phrases": ["x" * 201]},
        {"phrases": [" "]},
        {"phrases": ["term"] * 501},
        {"max_speakers": 2},
        {"prompt": "summarize"},
        {"channels": [0, 1]},
    ],
)
def test_api_rejects_invalid_or_unsupported_options(invalid: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobCreate.model_validate(
            {
                "provider": "azure_mai",
                "recording_id": "recording",
                "language": "auto_mixed",
                "mai_options": invalid,
            }
        )
    with pytest.raises(ClassScribeError, match="MAI 转写选项无效"):
        request_definition({"mai_options": invalid}, [])


def test_merged_glossary_limit_is_checked_before_transport() -> None:
    with pytest.raises(ClassScribeError, match="最多 500"):
        request_definition({"mai_options": {"phrases": ["extra"]}}, [str(i) for i in range(500)])
    with pytest.raises(ClassScribeError, match="最多 200"):
        request_definition({"mai_options": {}}, ["x" * 201])
    assert (
        len(
            request_definition({"mai_options": {"phrases": ["0"]}}, [str(i) for i in range(500)])[
                "phraseList"
            ]["phrases"]
        )
        == 500
    )


def test_local_jobs_do_not_accept_mai_options() -> None:
    with pytest.raises(ValidationError, match="MAI options require"):
        JobCreate(
            recording_id="recording",
            language=LanguageMode.JAPANESE,
            mai_options=MaiTranscriptionOptions(),
        )


def test_job_schema_preserves_explicit_mai_settings() -> None:
    value = JobCreate.model_validate(
        {
            "recording_id": "recording",
            "language": "auto_mixed",
            "provider": "azure_mai",
            "mai_options": {"locale": "yue", "transcribe_style": "clean", "phrases": [" GPU "]},
        }
    )
    assert value.model_dump(mode="json")["mai_options"]["phrases"] == ["GPU"]
    assert value.mai_options is not None and value.mai_options.locale == "yue"
