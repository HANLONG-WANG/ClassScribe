"""Resolve immutable application resources in source and installed layouts."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

SYSTEM_RESOURCE_ROOT = Path("/usr/share/classscribe")
SOURCE_RESOURCE_ROOT = Path(__file__).resolve().parents[2]


def resource_root(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    override = values.get("CLASSSCRIBE_RESOURCE_ROOT")
    if override:
        candidate = Path(override)
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("CLASSSCRIBE_RESOURCE_ROOT must be an absolute non-symlink directory")
        return candidate.resolve(strict=True)
    if (SOURCE_RESOURCE_ROOT / "config/model-registry.v1.yaml").is_file():
        return SOURCE_RESOURCE_ROOT
    if SYSTEM_RESOURCE_ROOT.is_dir() and not SYSTEM_RESOURCE_ROOT.is_symlink():
        return SYSTEM_RESOURCE_ROOT.resolve(strict=True)
    raise FileNotFoundError("ClassScribe immutable resources are not installed")


def resource_path(relative: str, environment: Mapping[str, str] | None = None) -> Path:
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("resource path must be safe and relative")
    root = resource_root(environment)
    candidate = root.joinpath(*Path(relative).parts)
    if (
        candidate.is_symlink()
        or not candidate.exists()
        or not candidate.resolve().is_relative_to(root)
    ):
        raise FileNotFoundError(f"ClassScribe resource is missing or unsafe: {relative}")
    return candidate
