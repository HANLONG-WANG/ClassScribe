from __future__ import annotations

from decimal import Decimal

import pytest
from classscribe.timeline import (
    INT64_MAX,
    SAMPLE_RATE,
    AudioSpan,
    TimelineError,
    format_sample_timestamp,
    milliseconds_to_samples,
    samples_to_milliseconds,
    validate_sample_index,
)
from hypothesis import given
from hypothesis import strategies as st

NINETY_MINUTE_SAMPLES = 90 * 60 * SAMPLE_RATE


def test_ninety_minute_round_trip_has_no_drift() -> None:
    samples = NINETY_MINUTE_SAMPLES
    assert samples == 86_400_000
    assert samples_to_milliseconds(samples) == 5_400_000
    assert milliseconds_to_samples(samples_to_milliseconds(samples)) == samples
    assert format_sample_timestamp(samples) == "01:30:00.000"


@given(st.integers(min_value=0, max_value=NINETY_MINUTE_SAMPLES))
def test_every_sample_in_ninety_minutes_round_trips_exactly(sample: int) -> None:
    assert milliseconds_to_samples(samples_to_milliseconds(sample)) == sample


def test_many_millisecond_steps_do_not_accumulate_float_drift() -> None:
    samples = 0
    for _ in range(90 * 60 * 10):
        samples += milliseconds_to_samples(100)
    assert samples == NINETY_MINUTE_SAMPLES


@pytest.mark.parametrize("value", [-1, INT64_MAX + 1, True, 1.0])
def test_invalid_sample_index_is_rejected(value: object) -> None:
    with pytest.raises(TimelineError):
        validate_sample_index(value)  # type: ignore[arg-type]


def test_sub_sample_milliseconds_are_rejected() -> None:
    with pytest.raises(TimelineError, match="sample boundary"):
        milliseconds_to_samples(Decimal("0.01"))


def test_audio_span_uses_absolute_offsets() -> None:
    span = AudioSpan(start_sample=32_000, end_sample=48_000)
    assert span.duration_samples == 16_000
    assert span.to_absolute(8_000) == 40_000
    assert span.to_local(40_000) == 8_000
    assert span.overlaps(AudioSpan(47_999, 49_000))
    assert not span.overlaps(AudioSpan(48_000, 49_000))


def test_reverse_span_and_outside_offsets_are_rejected() -> None:
    with pytest.raises(TimelineError):
        AudioSpan(2, 1)
    span = AudioSpan(10, 20)
    with pytest.raises(TimelineError):
        span.to_local(9)
    with pytest.raises(TimelineError):
        span.to_absolute(11)
