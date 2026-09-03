"""Canonical 16 kHz sample-index timeline.

Persistent and inter-process time values use integer sample indices. Decimal seconds and
milliseconds exist only at input/output boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Final

SAMPLE_RATE: Final = 16_000
SAMPLES_PER_MILLISECOND: Final = 16
INT64_MAX: Final = 2**63 - 1
TIMESTAMP_PATTERN: Final = "HH:MM:SS.mmm"


class TimelineError(ValueError):
    """Raised when a value cannot be represented on the canonical timeline."""


def validate_sample_index(value: int, *, field: str = "sample_index") -> int:
    """Return a valid non-negative signed-int64 sample index.

    ``bool`` is rejected explicitly even though it subclasses ``int``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TimelineError(f"{field} must be an int64 sample index")
    if not 0 <= value <= INT64_MAX:
        raise TimelineError(f"{field} must be between 0 and {INT64_MAX}")
    return value


def milliseconds_to_samples(value: int | str | Decimal | Fraction) -> int:
    """Convert an exact decimal millisecond value to a sample index.

    Values between sample boundaries are rejected rather than silently rounded.
    """

    if isinstance(value, bool):
        raise TimelineError("milliseconds must be numeric")
    if isinstance(value, Fraction):
        if value < 0:
            raise TimelineError("milliseconds must be non-negative")
        rational_samples = value * SAMPLES_PER_MILLISECOND
        if rational_samples.denominator != 1:
            raise TimelineError("milliseconds do not fall on a 16 kHz sample boundary")
        return validate_sample_index(rational_samples.numerator)
    try:
        milliseconds = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TimelineError("milliseconds must be a finite decimal") from exc
    if not milliseconds.is_finite() or milliseconds < 0:
        raise TimelineError("milliseconds must be finite and non-negative")
    decimal_samples = milliseconds * SAMPLES_PER_MILLISECOND
    integral = decimal_samples.to_integral_value()
    if decimal_samples != integral:
        raise TimelineError("milliseconds do not fall on a 16 kHz sample boundary")
    return validate_sample_index(int(integral))


def samples_to_milliseconds(value: int) -> Fraction:
    """Return exact milliseconds as a rational number, with no float drift."""

    return Fraction(validate_sample_index(value), SAMPLES_PER_MILLISECOND)


def format_milliseconds(milliseconds: int) -> str:
    """Format an integer display timestamp as ``HH:MM:SS.mmm``."""

    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or milliseconds < 0:
        raise TimelineError("display milliseconds must be a non-negative integer")
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_sample_timestamp(value: int) -> str:
    """Format a sample index, rounding half up only at the display boundary."""

    exact_ms = samples_to_milliseconds(value)
    rounded_ms = (exact_ms.numerator * 2 + exact_ms.denominator) // (2 * exact_ms.denominator)
    return format_milliseconds(rounded_ms)


@dataclass(frozen=True, slots=True)
class AudioSpan:
    """A half-open absolute interval on the canonical audio timeline."""

    start_sample: int
    end_sample: int

    def __post_init__(self) -> None:
        validate_sample_index(self.start_sample, field="start_sample")
        validate_sample_index(self.end_sample, field="end_sample")
        if self.end_sample < self.start_sample:
            raise TimelineError("end_sample must be greater than or equal to start_sample")

    @property
    def duration_samples(self) -> int:
        return self.end_sample - self.start_sample

    def to_local(self, absolute_sample: int) -> int:
        value = validate_sample_index(absolute_sample)
        if not self.start_sample <= value <= self.end_sample:
            raise TimelineError("absolute sample lies outside the span")
        return value - self.start_sample

    def to_absolute(self, local_sample: int) -> int:
        value = validate_sample_index(local_sample, field="local_sample")
        if value > self.duration_samples:
            raise TimelineError("local sample lies outside the span")
        return validate_sample_index(self.start_sample + value)

    def overlaps(self, other: AudioSpan) -> bool:
        return self.start_sample < other.end_sample and other.start_sample < self.end_sample
