"""Versioned model capability registry and atomic user-managed overrides."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ResponseCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_timestamps: bool
    word_timestamps: bool
    confidence: bool
    logprobs: bool
    punctuation: bool
    speakers: bool
    hotwords: bool


class ResourceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vram_class: Literal["tiny", "small", "medium", "large", "too_large"]
    estimated_vram_mb: int = Field(ge=0)
    measured_vram_mb: int | None = Field(default=None, ge=0)
    safety_margin_mb: int = Field(ge=512)


class InstallationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["not_installed", "installed", "unhealthy", "missing", "staged"]
    artifact_sha256: str | None = None
    local_revision: str | None = None

    @model_validator(mode="after")
    def validate_hashes(self) -> InstallationStatus:
        if self.artifact_sha256 is not None and not SHA256_RE.fullmatch(self.artifact_sha256):
            raise ValueError("artifact_sha256 must be a lowercase SHA-256")
        if self.local_revision is not None and not COMMIT_RE.fullmatch(self.local_revision):
            raise ValueError("local_revision must be a full commit SHA")
        if self.state == "installed" and (
            self.artifact_sha256 is None or self.local_revision is None
        ):
            raise ValueError("installed entries require artifact hash and local revision")
        return self


class BenchmarkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latest_run_id: str | None = None
    results: dict[str, dict[str, float | int | str | bool | None]] = Field(default_factory=dict)
    source: Literal["bootstrap_public_facts", "local_gold"] = "bootstrap_public_facts"


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repository: str = Field(pattern=r"^[^\s]+/[^\s]+$")
    revision: str
    worker: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    languages: tuple[str, ...] = Field(min_length=1)
    tasks: tuple[str, ...] = Field(min_length=1)
    modes: tuple[Literal["batch", "streaming", "final_decode"], ...] = Field(min_length=1)
    input_sample_rate: Literal[16000]
    input_channels: Literal[1]
    input_dtype: Literal["pcm_s16le"]
    runtime_backend: str = Field(min_length=1)
    dtype: Literal["float32", "float16", "bfloat16", "int8", "q8_0"]
    trust_remote_code: bool
    safe_operating_window_seconds: int = Field(ge=1, le=5400)
    vendor_claimed_max_seconds: int | None = Field(default=None, ge=1)
    responses: ResponseCapabilities
    resources: ResourceProfile
    dependency_lock: str = Field(pattern=r"^workers/[a-z0-9_]+/uv\.lock$")
    dependency_lock_sha256: str
    installation: InstallationStatus
    benchmark: BenchmarkMetadata
    enabled: bool
    experimental: bool = False
    known_defects: tuple[str, ...] = ()
    disable_reason: str | None = None

    @model_validator(mode="after")
    def validate_entry(self) -> ModelEntry:
        if not COMMIT_RE.fullmatch(self.revision):
            raise ValueError("revision must be a lowercase 40-character commit SHA")
        if not SHA256_RE.fullmatch(self.dependency_lock_sha256):
            raise ValueError("dependency_lock_sha256 must be a lowercase SHA-256")
        if not self.enabled and not self.disable_reason:
            raise ValueError("disabled model must record disable_reason")
        if self.enabled and self.disable_reason:
            raise ValueError("enabled model may not record disable_reason")
        if "streaming" in self.modes and "streaming" not in self.tasks:
            raise ValueError("streaming mode requires the streaming task capability")
        return self


class ModelRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    registry_revision: int = Field(ge=1)
    facts_as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    sources: tuple[str, ...] = Field(min_length=1)
    models: tuple[ModelEntry, ...] = Field(min_length=1)
    rankings: dict[str, tuple[str, ...]]
    history: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def validate_registry(self) -> ModelRegistry:
        ids = [item.id for item in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("model IDs must be unique")
        known = set(ids)
        for profile, ranking in self.rankings.items():
            if not profile or not ranking:
                raise ValueError("ranking profiles and candidate lists must not be empty")
            if len(ranking) != len(set(ranking)):
                raise ValueError(f"ranking {profile} contains duplicates")
            if missing := set(ranking) - known:
                raise ValueError(f"ranking {profile} refers to unknown models: {sorted(missing)}")
        return self

    def model(self, model_id: str) -> ModelEntry:
        try:
            return next(item for item in self.models if item.id == model_id)
        except StopIteration as exc:
            raise KeyError(model_id) from exc

    def candidates(
        self,
        profile: str,
        *,
        language: str | None = None,
        task: str | None = None,
        mode: str | None = None,
        include_disabled: bool = False,
        installed_only: bool = False,
    ) -> tuple[ModelEntry, ...]:
        ranking = self.rankings.get(profile)
        if ranking is None:
            raise KeyError(profile)
        ordered = (self.model(model_id) for model_id in ranking)
        return tuple(
            item
            for item in ordered
            if (include_disabled or item.enabled)
            and (not installed_only or item.installation.state == "installed")
            and (language is None or language in item.languages or "auto" in item.languages)
            and (task is None or task in item.tasks)
            and (mode is None or mode in item.modes)
        )


class RegistryStore:
    """Persist registry mutations atomically while retaining rollback history."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ModelRegistry:
        return load_registry(self.path)

    def initialize_from(self, bundled: Path) -> ModelRegistry:
        if not self.path.exists():
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            registry = load_registry(bundled)
            self._save(registry.model_dump(mode="json"))
        return self.load()

    def add(self, entry: ModelEntry) -> ModelRegistry:
        registry = self.load()
        if entry.id in {item.id for item in registry.models}:
            raise ValueError(f"model already exists: {entry.id}")
        data = registry.model_dump(mode="json")
        data["models"].append(entry.model_dump(mode="json"))
        return self._commit(data, "add", entry.id, {})

    def disable(self, model_id: str, reason: str) -> ModelRegistry:
        if not reason.strip():
            raise ValueError("disable reason must not be empty")
        return self._replace_model(
            model_id, {"enabled": False, "disable_reason": reason}, "disable"
        )

    def enable(self, model_id: str) -> ModelRegistry:
        return self._replace_model(model_id, {"enabled": True, "disable_reason": None}, "enable")

    def upgrade(self, model_id: str, revision: str) -> ModelRegistry:
        if not COMMIT_RE.fullmatch(revision):
            raise ValueError("upgrade revision must be a full commit SHA")
        return self._replace_model(
            model_id,
            {
                "revision": revision,
                "installation": {
                    "state": "not_installed",
                    "artifact_sha256": None,
                    "local_revision": None,
                },
                "benchmark": {
                    "latest_run_id": None,
                    "results": {},
                    "source": "bootstrap_public_facts",
                },
            },
            "upgrade",
        )

    def rollback(self, model_id: str) -> ModelRegistry:
        registry = self.load()
        for event in reversed(registry.history):
            if event.get("model_id") == model_id and event.get("action") == "upgrade":
                previous = event.get("before")
                if isinstance(previous, dict):
                    return self._replace_model(model_id, previous, "rollback")
        raise ValueError(f"no upgrade history for {model_id}")

    def set_ranking(self, profile: str, model_ids: Sequence[str]) -> ModelRegistry:
        registry = self.load()
        if not model_ids or len(model_ids) != len(set(model_ids)):
            raise ValueError("ranking must be non-empty and unique")
        known = {item.id for item in registry.models}
        if missing := set(model_ids) - known:
            raise ValueError(f"ranking contains unknown models: {sorted(missing)}")
        data = registry.model_dump(mode="json")
        before = data["rankings"].get(profile)
        data["rankings"][profile] = list(model_ids)
        return self._commit(data, "rank", profile, {"ranking": before})

    def rollback_ranking(self, profile: str) -> ModelRegistry:
        registry = self.load()
        for event in reversed(registry.history):
            if event.get("model_id") == profile and event.get("action") == "rank":
                previous = event.get("before")
                if not isinstance(previous, dict) or "ranking" not in previous:
                    continue
                data = registry.model_dump(mode="json")
                current = data["rankings"].get(profile)
                old_ranking = previous["ranking"]
                if old_ranking is None:
                    data["rankings"].pop(profile, None)
                elif isinstance(old_ranking, list) and old_ranking:
                    data["rankings"][profile] = old_ranking
                else:
                    raise ValueError("ranking history is invalid")
                return self._commit(
                    data,
                    "rank_rollback",
                    profile,
                    {"ranking": current},
                )
        raise ValueError(f"no ranking history for {profile}")

    def mark_installation(
        self,
        model_id: str,
        *,
        revision: str,
        artifact_sha256: str,
        measured_vram_mb: int | None,
    ) -> ModelRegistry:
        registry = self.load()
        entry = registry.model(model_id)
        if revision != entry.revision:
            raise ValueError("installed revision does not match registry revision")
        changes: dict[str, Any] = {
            "installation": {
                "state": "installed",
                "artifact_sha256": artifact_sha256,
                "local_revision": revision,
            }
        }
        if measured_vram_mb is not None:
            changes["resources"] = {
                **entry.resources.model_dump(mode="json"),
                "measured_vram_mb": measured_vram_mb,
            }
        return self._replace_model(model_id, changes, "install")

    def record_benchmark(
        self,
        model_id: str,
        *,
        run_id: str,
        dataset_id: str,
        metrics: Mapping[str, float | int | str | bool | None],
    ) -> ModelRegistry:
        if "quality_probability" in metrics:
            raise ValueError("quality_probability is introduced only by stage-12 calibration")
        registry = self.load()
        entry = registry.model(model_id)
        results = deepcopy(entry.benchmark.results)
        results[dataset_id] = dict(metrics)
        return self._replace_model(
            model_id,
            {
                "benchmark": {
                    "latest_run_id": run_id,
                    "results": results,
                    "source": "local_gold",
                }
            },
            "benchmark",
        )

    def record_calibrated_benchmark(
        self,
        model_id: str,
        *,
        run_id: str,
        dataset_id: str,
        metrics: Mapping[str, float | int | str | bool | None],
        calibration_artifact_sha256: str,
    ) -> ModelRegistry:
        if len(calibration_artifact_sha256) != 64:
            raise ValueError("calibration artifact SHA-256 must contain 64 characters")
        registry = self.load()
        entry = registry.model(model_id)
        if entry.installation.local_revision != entry.revision:
            raise ValueError("calibrated benchmark requires the pinned revision to be installed")
        results = deepcopy(entry.benchmark.results)
        results[dataset_id] = {
            **dict(metrics),
            "calibration_artifact_sha256": calibration_artifact_sha256,
            "model_revision": entry.revision,
        }
        return self._replace_model(
            model_id,
            {
                "benchmark": {
                    "latest_run_id": run_id,
                    "results": results,
                    "source": "local_gold",
                }
            },
            "calibrated_benchmark",
        )

    def _replace_model(
        self, model_id: str, changes: Mapping[str, Any], action: str
    ) -> ModelRegistry:
        registry = self.load()
        data = registry.model_dump(mode="json")
        for index, item in enumerate(data["models"]):
            if item["id"] == model_id:
                before = {key: deepcopy(item.get(key)) for key in changes}
                data["models"][index] = {**item, **deepcopy(dict(changes))}
                return self._commit(data, action, model_id, before)
        raise KeyError(model_id)

    def _commit(
        self, data: dict[str, Any], action: str, model_id: str, before: Mapping[str, Any]
    ) -> ModelRegistry:
        data["registry_revision"] = int(data["registry_revision"]) + 1
        data.setdefault("history", []).append(
            {
                "registry_revision": data["registry_revision"],
                "action": action,
                "model_id": model_id,
                "before": deepcopy(dict(before)),
            }
        )
        registry = ModelRegistry.model_validate(data)
        self._save(registry.model_dump(mode="json"))
        return registry

    def _save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(dict(data), handle, sort_keys=False, allow_unicode=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def load_registry(path: Path, *, repository_root: Path | None = None) -> ModelRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("registry document must be a mapping")
    registry = ModelRegistry.model_validate(raw)
    if repository_root is not None:
        for entry in registry.models:
            lock = repository_root / entry.dependency_lock
            if not lock.is_file():
                raise ValueError(f"dependency lock is missing: {entry.dependency_lock}")
            digest = hashlib.sha256(lock.read_bytes()).hexdigest()
            if digest != entry.dependency_lock_sha256:
                raise ValueError(f"dependency lock hash mismatch for {entry.id}")
    return registry
