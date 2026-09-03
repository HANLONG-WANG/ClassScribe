"""Pinned upstream model-license disclosures used by install confirmation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelLicense(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str = Field(pattern=r"^[^\s]+/[^\s]+$")
    license_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,127}$")
    license_url: str = Field(pattern=r"^https://")
    requires_terms_acceptance: bool
    verified_as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def load_model_licenses(path: Path) -> dict[str, ModelLicense]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("model license inventory is missing or unsafe")
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported model license inventory")
    raw = value.get("models")
    if not isinstance(raw, list):
        raise ValueError("model license inventory models must be an array")
    models = tuple(ModelLicense.model_validate(item) for item in raw)
    result = {item.repository: item for item in models}
    if len(result) != len(models):
        raise ValueError("model license inventory repeats a repository")
    return result
