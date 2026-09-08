"""Language-aware subtitle cues derived from final token timing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from classscribe.exports.models import ExportLayer, ExportSegment, ExportToken, SubtitleCue
from classscribe.timeline import SAMPLE_RATE

_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_UNITS = frozenset(
    {
        "%",
        "°C",
        "kg",
        "g",
        "mg",
        "km",
        "m",
        "cm",
        "mm",
        "s",
        "ms",
        "Hz",
        "kHz",
        "MHz",
        "GB",
        "MB",
        "個",
        "人",
        "年",
        "月",
        "日",
        "秒",
        "分",
    }
)
_SENTENCE_END = frozenset(".?!\u3002\uff1f\uff01")


@dataclass(frozen=True, slots=True)
class _Unit:
    tokens: tuple[ExportToken, ...]

    @property
    def start(self) -> int:
        return self.tokens[0].span.start_sample

    @property
    def end(self) -> int:
        return self.tokens[-1].span.end_sample


def build_subtitle_cues(
    segments: tuple[ExportSegment, ...], layer: ExportLayer
) -> tuple[SubtitleCue, ...]:
    cues: list[SubtitleCue] = []
    for segment in segments:
        text, _ = segment.text_for(layer)
        tokens = segment.tokens_for(layer)
        if not text.strip():
            continue
        if not tokens:
            cues.append(
                SubtitleCue(
                    segment.span.start_sample,
                    segment.span.end_sample,
                    _wrap_text(text, segment.language),
                    (segment.segment_id,),
                )
            )
            continue
        units = _protected_units(tokens)
        maximum = 16 if segment.language == "en" else 36
        chunks: list[list[_Unit]] = []
        current: list[_Unit] = []
        for unit in units:
            proposed = [*current, unit]
            if current and (
                _measure(proposed, segment.language) > maximum
                or _reading_speed_exceeded(proposed, segment.language)
            ):
                chunks.append(current)
                current = [unit]
            else:
                current = proposed
            if current and _ends_sentence(current):
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)
        if not chunks:
            chunks = [list(units)]
        rendered_all = _render_tokens(tokens, segment.language)
        if _comparable(rendered_all) != _comparable(text):
            chunks = [list(units)]
        for chunk in chunks:
            rendered = _render_tokens(
                tuple(token for unit in chunk for token in unit.tokens), segment.language
            )
            lines = _wrap_units(chunk, segment.language)
            if len(chunks) == 1 and _comparable(rendered) != _comparable(text):
                lines = _wrap_text(text, segment.language)
            cues.append(
                SubtitleCue(
                    chunk[0].start,
                    chunk[-1].end,
                    lines,
                    (segment.segment_id,),
                )
            )
    return tuple(cues)


def _protected_units(tokens: tuple[ExportToken, ...]) -> tuple[_Unit, ...]:
    units: list[_Unit] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        grouped = [token]
        if token.protected_group:
            index += 1
            while index < len(tokens) and tokens[index].protected_group == token.protected_group:
                grouped.append(tokens[index])
                index += 1
        elif _NUMBER.fullmatch(token.text) and index + 1 < len(tokens):
            following = tokens[index + 1]
            if following.text in _UNITS:
                grouped.append(following)
                index += 2
            else:
                index += 1
        else:
            index += 1
        units.append(_Unit(tuple(grouped)))
    return tuple(units)


def _wrap_units(units: list[_Unit], language: str) -> tuple[str, ...]:
    line_limit = 8 if language == "en" else 18
    if _measure(units, language) <= line_limit:
        return (_render_units(units, language),)
    best = min(
        range(1, len(units)),
        key=lambda position: abs(
            _measure(units[:position], language) - _measure(units[position:], language)
        ),
        default=1,
    )
    return (_render_units(units[:best], language), _render_units(units[best:], language))


def _wrap_text(text: str, language: str) -> tuple[str, ...]:
    if language == "en":
        words = text.split()
        if len(words) <= 8:
            return (text.strip(),)
        middle = max(1, len(words) // 2)
        return (" ".join(words[:middle]), " ".join(words[middle:]))
    if len(text) <= 18:
        return (text.strip(),)
    middle = min(len(text) - 1, max(1, len(text) // 2))
    return (text[:middle].strip(), text[middle:].strip())


def _measure(units: list[_Unit], language: str) -> int:
    if language == "en":
        return sum(1 for unit in units for token in unit.tokens if token.text.strip())
    return len(_comparable(_render_units(units, language)))


def _reading_speed_exceeded(units: list[_Unit], language: str) -> bool:
    if not units:
        return False
    duration = (units[-1].end - units[0].start) / SAMPLE_RATE
    if duration <= 0:
        return True
    rendered = _render_units(units, language)
    characters_per_second = len(_comparable(rendered)) / duration
    if language == "en":
        words_per_second = _measure(units, language) / duration
        return characters_per_second > 20 or words_per_second > 3.5
    return characters_per_second > 15


def _render_units(units: list[_Unit], language: str) -> str:
    return _render_tokens(tuple(token for unit in units for token in unit.tokens), language)


def _render_tokens(tokens: tuple[ExportToken, ...], language: str) -> str:
    if language != "en":
        return "".join(token.text for token in tokens)
    output = ""
    for token in tokens:
        if not output or token.text[:1] in ",.!?;:%)]}" or output[-1:] in "([{$":
            output += token.text
        else:
            output += " " + token.text
    return output


def _ends_sentence(units: list[_Unit]) -> bool:
    return units[-1].tokens[-1].text[-1:] in _SENTENCE_END


def _comparable(text: str) -> str:
    return "".join(character for character in text if not character.isspace())
