from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from classscribe.models import ManifestBundleIndex
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def bundle_value() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "registry_revision": 1,
        "facts_as_of": "2026-09-03",
        "generated_at": "2026-09-04T00:00:00Z",
        "generator": {
            "name": "classscribe-model-manifest",
            "version": "1",
            "lock_sha256": "0" * 64,
        },
        "manifests": [
            {"model_id": "alpha", "path": "alpha.json", "sha256": "1" * 64},
            {"model_id": "beta", "path": "beta.json", "sha256": "2" * 64},
        ],
    }


def test_manifest_schema_preserves_upstream_layout_and_rejects_unsafe_paths() -> None:
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest.schema.json").read_text(encoding="utf-8")
    )
    files = schema["properties"]["files"]
    path_rule = files["items"]["properties"]["path"]
    pattern = re.compile(path_rule["pattern"])

    assert files["uniqueItems"] is True
    assert "preserved exactly from the upstream repository" in path_rule["description"]
    assert pattern.fullmatch("modeling.py")
    assert pattern.fullmatch("pkg/modeling.py")
    for unsafe in (
        "/absolute.py",
        "../escape.py",
        "pkg/../escape.py",
        "./modeling.py",
        "pkg//modeling.py",
        "pkg\\modeling.py",
        "control\x00.py",
    ):
        assert pattern.fullmatch(unsafe) is None
    assert path_rule["not"] == {"const": "supply-chain.json"}


def test_manifest_schema_requires_remote_code_kind_to_match_trust_flag() -> None:
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest.schema.json").read_text(encoding="utf-8")
    )
    trust_contract = schema["allOf"][0]

    assert trust_contract["if"]["properties"]["trust_remote_code"] == {"const": True}
    assert (
        trust_contract["then"]["properties"]["files"]["contains"]["properties"]["kind"]
        == {"const": "remote_code"}
    )
    assert (
        trust_contract["else"]["properties"]["files"]["not"]["contains"]["properties"][
            "kind"
        ]
        == {"const": "remote_code"}
    )


def test_manifest_schema_encodes_optional_strict_component_source_provenance() -> None:
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest.schema.json").read_text(encoding="utf-8")
    )
    sources = schema["properties"]["component_sources"]
    source = sources["items"]
    source_file = source["properties"]["files"]["items"]

    assert "component_sources" not in schema["required"]
    assert sources["uniqueItems"] is True
    assert source["additionalProperties"] is False
    assert set(source["required"]) == {
        "repository",
        "revision",
        "relationship",
        "license_id",
        "license_url",
        "requires_terms_acceptance",
        "files",
    }
    assert source["properties"]["relationship"]["enum"] == ["copied", "derived"]
    assert source_file["additionalProperties"] is False
    assert set(source_file["required"]) == {
        "installed_path",
        "source_path",
        "source_sha256",
        "source_size_bytes",
    }
    assert source_file["properties"]["installed_path"] == {
        "$ref": "#/$defs/safe_relative_path"
    }
    assert source_file["properties"]["source_path"] == {
        "$ref": "#/$defs/safe_relative_path"
    }
    assert source_file["properties"]["source_sha256"] == {"$ref": "#/$defs/sha256"}


def test_manifest_component_source_schema_patterns_reject_unsafe_identity() -> None:
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest.schema.json").read_text(encoding="utf-8")
    )
    source = schema["properties"]["component_sources"]["items"]
    repository_pattern = re.compile(source["properties"]["repository"]["pattern"])
    revision_pattern = re.compile(source["properties"]["revision"]["pattern"])
    path_pattern = re.compile(schema["$defs"]["safe_relative_path"]["pattern"])
    sha_pattern = re.compile(schema["$defs"]["sha256"]["pattern"])

    assert repository_pattern.fullmatch("upstream/component")
    assert repository_pattern.fullmatch("missing-slash") is None
    assert repository_pattern.fullmatch("too/many/slashes") is None
    assert revision_pattern.fullmatch("a" * 40)
    assert revision_pattern.fullmatch("A" * 40) is None
    assert path_pattern.fullmatch("embedding/pytorch_model.bin")
    assert path_pattern.fullmatch("embedding/../escape.bin") is None
    assert path_pattern.fullmatch("embedding\\model.bin") is None
    assert sha_pattern.fullmatch("b" * 64)
    assert sha_pattern.fullmatch("B" * 64) is None


def test_bundle_schema_encodes_strict_fields_sha_and_single_level_paths() -> None:
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest-bundle.schema.json").read_text(
            encoding="utf-8"
        )
    )
    entry = schema["$defs"]["manifest_entry"]
    path_rule = entry["properties"]["path"]
    path_pattern = re.compile(path_rule["pattern"])
    sha_pattern = re.compile(schema["$defs"]["sha256"]["pattern"])

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "registry_revision",
        "facts_as_of",
        "generated_at",
        "generator",
        "manifests",
    }
    assert entry["additionalProperties"] is False
    assert schema["properties"]["manifests"]["uniqueItems"] is True
    assert path_pattern.fullmatch("alpha.json")
    assert path_pattern.fullmatch("nested/alpha.json") is None
    assert path_pattern.fullmatch("../alpha.json") is None
    assert path_rule["not"] == {"const": "bundle.v1.json"}
    assert sha_pattern.fullmatch("a" * 64)
    assert sha_pattern.fullmatch("A" * 64) is None


def test_bundle_model_accepts_valid_frozen_index() -> None:
    bundle = ManifestBundleIndex.model_validate(bundle_value())

    assert tuple(entry.model_id for entry in bundle.manifests) == ("alpha", "beta")
    assert bundle.model_config["frozen"] is True
    with pytest.raises(ValidationError, match="frozen"):
        bundle.registry_revision = 2


def test_bundle_model_rejects_duplicate_ids_and_paths() -> None:
    duplicate_id = bundle_value()
    duplicate_id["manifests"][1] = {
        "model_id": "alpha",
        "path": "alpha-copy.json",
        "sha256": "2" * 64,
    }
    with pytest.raises(ValidationError, match="model IDs must be unique"):
        ManifestBundleIndex.model_validate(duplicate_id)

    duplicate_path = bundle_value()
    duplicate_path["manifests"][1]["path"] = "alpha.json"
    with pytest.raises(ValidationError, match="manifest paths must be unique"):
        ManifestBundleIndex.model_validate(duplicate_path)


def test_bundle_model_rejects_sorting_error_and_invalid_sha() -> None:
    unsorted = bundle_value()
    unsorted["manifests"].reverse()
    with pytest.raises(ValidationError, match="sorted by model_id"):
        ManifestBundleIndex.model_validate(unsorted)

    for location in ("generator", "manifest"):
        invalid_sha = bundle_value()
        if location == "generator":
            invalid_sha["generator"]["lock_sha256"] = "A" * 64
        else:
            invalid_sha["manifests"][0]["sha256"] = "short"
        with pytest.raises(ValidationError, match="string_pattern_mismatch"):
            ManifestBundleIndex.model_validate(invalid_sha)


@pytest.mark.parametrize(
    "path", ["nested/alpha.json", "../alpha.json", "/alpha.json", "bundle.v1.json"]
)
def test_bundle_model_rejects_non_single_level_or_escape_path(path: str) -> None:
    value = bundle_value()
    value["manifests"][0]["path"] = path

    with pytest.raises(ValidationError):
        ManifestBundleIndex.model_validate(value)


def test_bundle_model_rejects_missing_and_extra_fields() -> None:
    missing = bundle_value()
    missing.pop("generated_at")
    with pytest.raises(ValidationError, match="Field required"):
        ManifestBundleIndex.model_validate(missing)

    extra = deepcopy(bundle_value())
    extra["local_cache"] = "/private/cache"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ManifestBundleIndex.model_validate(extra)
