"""Audited model-ID to worker implementation support matrix."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from classscribe.models.registry import ModelEntry
from classscribe.resources import resource_root

SUPPORT_CONTRACT = Path("config/model-worker-support.v1.json")
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
REQUIRED_WORKER_FILES = (
    "adapter.py",
    "healthcheck.py",
    "pyproject.toml",
    "uv.lock",
    "worker.py",
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("worker support contract contains a duplicate JSON key")
        result[key] = value
    return result


def load_worker_support(root: Path) -> Mapping[str, str]:
    """Load the shared, audited model-to-worker support contract fail closed."""

    path = root / SUPPORT_CONTRACT
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError("worker support contract is missing or unsafe") from error
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or not resolved.is_relative_to(root):
        raise ValueError("worker support contract is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("worker support contract is not strict UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != {"schema_version", "models"}:
        raise ValueError("worker support contract has invalid fields")
    if value["schema_version"] != 1 or not isinstance(value["models"], list):
        raise ValueError("worker support contract has invalid structure")
    result: dict[str, str] = {}
    rows = cast(list[object], value["models"])
    previous: str | None = None
    for raw in rows:
        if not isinstance(raw, dict) or set(raw) != {"model_id", "worker"}:
            raise ValueError("worker support contract contains an invalid row")
        model_id = raw["model_id"]
        worker = raw["worker"]
        if not (
            isinstance(model_id, str)
            and isinstance(worker, str)
            and IDENTIFIER_RE.fullmatch(model_id)
            and IDENTIFIER_RE.fullmatch(worker)
        ):
            raise ValueError("worker support contract contains an invalid identity")
        if previous is not None and model_id <= previous:
            raise ValueError("worker support contract model IDs must be unique and sorted")
        result[model_id] = worker
        previous = model_id
    return MappingProxyType(result)


IMPLEMENTED_MODEL_WORKERS = load_worker_support(resource_root())


def worker_implemented(entry: ModelEntry, resource_root: Path) -> bool:
    """Return true only for audited model support backed by safe worker source files."""

    try:
        support = load_worker_support(resource_root)
    except ValueError:
        return False
    if support.get(entry.id) != entry.worker:
        return False
    worker_root = resource_root / "workers" / entry.worker
    if worker_root.is_symlink() or not worker_root.is_dir():
        return False
    try:
        return all(
            stat.S_ISREG((worker_root / filename).lstat().st_mode)
            and not (worker_root / filename).is_symlink()
            for filename in REQUIRED_WORKER_FILES
        )
    except OSError:
        return False


def worker_lock_matches(entry: ModelEntry, resource_root: Path) -> bool:
    """Verify the exact registry-pinned dependency lock without following symlinks."""

    lock = resource_root.joinpath(*Path(entry.dependency_lock).parts)
    try:
        metadata = lock.lstat()
        if lock.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            return False
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    except OSError:
        return False
    return digest == entry.dependency_lock_sha256


def model_installability(
    entry: ModelEntry,
    resource_root: Path,
    *,
    manifest_available: bool,
) -> tuple[bool, str | None]:
    """Return ordinary-install eligibility and one stable blocking reason code."""

    if not entry.enabled or entry.experimental:
        return False, "registry_policy_disabled"
    if not worker_implemented(entry, resource_root):
        return False, "worker_not_implemented"
    if not manifest_available:
        return False, "manifest_unavailable"
    if not worker_lock_matches(entry, resource_root):
        return False, "worker_lock_unavailable_or_mismatched"
    return True, None
