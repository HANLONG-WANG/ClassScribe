"""MAI-Transcribe-2 options documented by Azure Speech."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MaiLocale = Literal[
    "af",
    "ar",
    "as",
    "az",
    "bg",
    "bn",
    "bs",
    "ca",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "fa",
    "fi",
    "fil",
    "fr",
    "gl",
    "gu",
    "he",
    "hi",
    "hu",
    "hy",
    "id",
    "is",
    "it",
    "ja",
    "kk",
    "kn",
    "ko",
    "lt",
    "lv",
    "mk",
    "ml",
    "mr",
    "ms",
    "nb",
    "ne",
    "nl",
    "or",
    "pa",
    "pl",
    "pt",
    "ro",
    "ru",
    "sk",
    "sl",
    "sv",
    "sw",
    "ta",
    "te",
    "th",
    "tr",
    "uk",
    "ur",
    "vi",
    "yue",
    "zh",
]
MaiPhraseHint = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class MaiTranscriptionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcribe_style: Literal["verbatim", "clean"] = "verbatim"
    timestamps: Literal["word", "segment", "none"] = "word"
    diarization: bool = Field(default=True, strict=True)
    locale: MaiLocale | None = None
    profanity_filter_mode: Literal["None", "Masked", "Removed", "Tags"] = "Masked"
    phrases: list[MaiPhraseHint] = Field(default_factory=list, max_length=500)
    phrase_biasing_weight: float | None = Field(
        default=None, ge=0, le=2, allow_inf_nan=False, strict=True
    )
