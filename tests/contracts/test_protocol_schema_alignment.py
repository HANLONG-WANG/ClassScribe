from __future__ import annotations

import json
from pathlib import Path

from classscribe.models import load_registry
from classscribe_protocol import STANDARD_METHODS

ROOT = Path(__file__).resolve().parents[2]


def test_worker_request_schema_and_python_method_enum_are_identical() -> None:
    schema = json.loads(
        (ROOT / "protocol/schema/v1/worker-request.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["properties"]["method"]["enum"]) == set(STANDARD_METHODS)
    pcm_rule = next(
        item
        for item in schema["allOf"]
        if item["if"]["properties"]["method"].get("const") == "transcribe_pcm"
    )
    required = set(pcm_rule["then"]["properties"]["params"]["required"])
    assert required == {
        "pcm_s16le",
        "absolute_start_sample",
        "core_start_sample",
        "end_sample",
        "sample_rate",
        "channels",
        "language",
        "rolling_context",
    }


def test_resident_schema_declares_language_specific_accuracy_routes() -> None:
    schema = json.loads(
        (ROOT / "protocol/schema/v1/resident-workers.schema.json").read_text(encoding="utf-8")
    )
    assert "accuracy_routes" in schema["required"]
    assert schema["properties"]["accuracy_routes"]["propertyNames"]["enum"] == [
        "zh",
        "ja",
        "en",
        "auto",
    ]


def test_disabled_funasr_does_not_advertise_unimplemented_streaming() -> None:
    entry = load_registry(ROOT / "config/model-registry.v1.yaml").model("fun_asr_nano_2512")
    assert entry.modes == ("batch",)
    assert "streaming" not in entry.tasks
