"""Deterministic display/export variants derived after the faithful simplified layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TextConverter(Protocol):
    def convert(self, text: str) -> str: ...


@dataclass(frozen=True, slots=True)
class TraditionalChineseVariant:
    text: str
    source_layer: str = "faithful_simplified"
    transformation: str = "opencc_s2t"


class TraditionalChineseConverter:
    def __init__(self, converter: TextConverter | None = None) -> None:
        if converter is None:
            from opencc import OpenCC  # type: ignore[import-untyped]

            converter = OpenCC("s2t")
        self._converter = converter

    def from_faithful_simplified(self, text: str) -> TraditionalChineseVariant:
        converted = self._converter.convert(text)
        source_ascii = "".join(character for character in text if character.isascii())
        converted_ascii = "".join(character for character in converted if character.isascii())
        if source_ascii != converted_ascii:
            raise ValueError("OpenCC display conversion may not alter embedded English/ASCII")
        return TraditionalChineseVariant(converted)
