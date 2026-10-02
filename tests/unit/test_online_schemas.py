import pytest
from classscribe.api.schemas import ClipCreate, JobCreate
from classscribe.contracts import LanguageMode
from pydantic import ValidationError


def test_old_job_defaults_to_local() -> None:
    assert JobCreate(recording_id="recording", language=LanguageMode.JAPANESE).provider == "local"
    assert (
        JobCreate(
            recording_id="recording", language=LanguageMode.JAPANESE, provider="azure_mai"
        ).provider
        == "azure_mai"
    )


@pytest.mark.parametrize(
    "start,end", [(True, 10), (0, False), (0.5, 10), (-1, 10), (10, 10), (11, 10)]
)
def test_clip_rejects_invalid_sample_coordinates(start: object, end: object) -> None:
    with pytest.raises(ValidationError):
        ClipCreate.model_validate(
            {"submission_key": "request", "start_sample": start, "end_sample": end}
        )


def test_clip_accepts_integer_sample_coordinates() -> None:
    clip = ClipCreate(submission_key="request", start_sample=123, end_sample=16001)
    assert clip.end_sample - clip.start_sample == 15878
