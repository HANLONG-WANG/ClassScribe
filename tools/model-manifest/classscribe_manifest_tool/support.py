"""Offline worker support and installability status evaluation."""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

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
    """Load the shared model-to-worker support contract without network access."""

    path = root / SUPPORT_CONTRACT
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError("worker support contract is missing or unsafe") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or not resolved.is_relative_to(root)
    ):
        raise ValueError("worker support contract is missing or unsafe")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("worker support contract is not strict UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != {"schema_version", "models"}:
        raise ValueError("worker support contract has invalid fields")
    if value["schema_version"] != 1 or not isinstance(value["models"], list):
        raise ValueError("worker support contract has invalid structure")
    result: dict[str, str] = {}
    previous: str | None = None
    for raw in cast(list[object], value["models"]):
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


def validate_worker_support(
    support: Mapping[str, str], registry_workers: Mapping[str, str]
) -> None:
    """Require every support declaration to match a registry identity and worker."""

    for model_id, worker in support.items():
        if registry_workers.get(model_id) != worker:
            raise ValueError("worker support contract differs from model registry")


def worker_implemented(
    root: Path,
    *,
    model_id: str,
    worker: str,
    support: Mapping[str, str],
) -> bool:
    """Return true for an audited mapping backed by a complete safe worker tree."""

    if support.get(model_id) != worker:
        return False
    worker_root = root / "workers" / worker
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


def model_status(
    root: Path,
    *,
    model_id: str,
    worker: str,
    enabled: bool,
    experimental: bool,
    manifest_sha256: str | None,
    support: Mapping[str, str],
) -> dict[str, object]:
    """Return four independent status values and a stable install blocker."""

    has_manifest = manifest_sha256 is not None
    has_worker = worker_implemented(
        root, model_id=model_id, worker=worker, support=support
    )
    reason: str | None = None
    if not enabled or experimental:
        reason = "registry_policy_disabled"
    elif not has_worker:
        reason = "worker_not_implemented"
    elif not has_manifest:
        reason = "manifest_unavailable"
    return {
        "model_id": model_id,
        "manifest_available": has_manifest,
        "manifest_sha256": manifest_sha256,
        "worker_implemented": has_worker,
        "installable": reason is None,
        "install_block_reason": reason,
        "enabled": enabled,
    }
