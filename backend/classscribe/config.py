"""Versioned, layered ClassScribe configuration."""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from classscribe.contracts import LanguageMode
from classscribe.resources import SOURCE_RESOURCE_ROOT, SYSTEM_RESOURCE_ROOT

CURRENT_CONFIG_VERSION = 1
ENV_PREFIX = "CLASSSCRIBE__"
SOURCE_DEFAULT_CONFIG_PATH = SOURCE_RESOURCE_ROOT / "config" / "default.yaml"
SYSTEM_DEFAULT_CONFIG_PATH = SYSTEM_RESOURCE_ROOT / "default.yaml"
DEFAULT_CONFIG_PATH = (
    SOURCE_DEFAULT_CONFIG_PATH
    if SOURCE_DEFAULT_CONFIG_PATH.is_file()
    else SYSTEM_DEFAULT_CONFIG_PATH
)


class ConfigError(ValueError):
    """Raised when configuration loading, migration, or validation fails."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServerConfig(StrictModel):
    host: Literal["127.0.0.1", "::1", "localhost"] = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    require_token: Literal[True] = True


class PrivacyConfig(StrictModel):
    runtime_offline: Literal[True] = True
    save_dictation_audio: bool = False
    log_transcript_text: bool = False


class HardwareConfig(StrictModel):
    gpu_device: int = Field(default=0, ge=0)
    max_vram_mb: int = Field(default=10_500, ge=1)
    one_heavy_worker: Literal[True] = True
    ibus_priority: int = Field(default=0, ge=0)


class ClassroomConfig(StrictModel):
    default_language: LanguageMode = LanguageMode.JAPANESE
    default_profile: str = "auto_best"
    structure_model: str = "moss_td_0_9b"
    structure_window_seconds: int = Field(default=720, ge=240, le=5400)
    structure_overlap_seconds: int = Field(default=4, ge=1)
    text_segment_target_seconds: int = Field(default=18, ge=1)
    text_segment_max_seconds: int = Field(default=30, ge=1)
    text_overlap_seconds: float = Field(default=1.0, ge=0)
    speaker_context: Literal["ordinary_class", "group_discussion"] = "ordinary_class"
    expected_speakers: int | Literal["auto"] = "auto"
    prior_min: int = Field(default=1, ge=1)
    prior_typical: int = Field(default=2, ge=1)
    max_speakers: int = Field(default=12, ge=1, le=12)
    allow_overlap: bool = True
    auto_export: tuple[Literal["json", "markdown", "srt", "vtt"], ...] = (
        "json",
        "markdown",
        "srt",
        "vtt",
    )

    @model_validator(mode="after")
    def validate_windows_and_speakers(self) -> ClassroomConfig:
        if self.structure_overlap_seconds >= self.structure_window_seconds:
            raise ValueError("structure overlap must be smaller than the window")
        if self.text_segment_target_seconds > self.text_segment_max_seconds:
            raise ValueError("text segment target must not exceed its maximum")
        if self.text_overlap_seconds >= self.text_segment_max_seconds:
            raise ValueError("text overlap must be smaller than the segment maximum")
        if not self.prior_min <= self.prior_typical <= self.max_speakers:
            raise ValueError(
                "speaker priors must satisfy prior_min <= prior_typical <= max_speakers"
            )
        if self.speaker_context == "ordinary_class" and self.prior_typical > 2:
            raise ValueError("ordinary_class prior_typical must be 1 or 2")
        if isinstance(self.expected_speakers, int) and not (
            self.prior_min <= self.expected_speakers <= self.max_speakers
        ):
            raise ValueError("expected_speakers must be auto or within the configured bounds")
        return self


class QualityConfig(StrictModel):
    second_model_threshold: float = Field(default=0.82, ge=0, le=1)
    third_model_threshold: float = Field(default=0.62, ge=0, le=1)
    reject_repetition: Literal[True] = True
    reject_script_mismatch: Literal[True] = True
    require_alignment_gate: Literal[True] = True
    unresolved_policy: Literal["inaudible_marker"] = "inaudible_marker"

    @model_validator(mode="after")
    def validate_threshold_order(self) -> QualityConfig:
        if self.third_model_threshold > self.second_model_threshold:
            raise ValueError("third-model threshold must not exceed second-model threshold")
        return self


class IBusConfig(StrictModel):
    enabled: bool = True
    activation: Literal["hold", "toggle"] = "hold"
    show_interim: bool = True
    default_language: LanguageMode = LanguageMode.JAPANESE
    default_profile: str = "balanced"
    stream_chunk_ms: int = Field(default=560, ge=1)
    semantic_endpoint_silence_ms: int = Field(default=650, ge=1)
    hard_chunk_seconds: int = Field(default=25, ge=1)
    overlap_seconds: float = Field(default=2.0, ge=0)
    rolling_context_segments: int = Field(default=3, ge=0)
    stable_prefix_commit_after_seconds: int = Field(default=45, ge=1, le=60)

    @model_validator(mode="after")
    def validate_chunking(self) -> IBusConfig:
        if self.stream_chunk_ms not in {80, 160, 320, 560, 1120}:
            raise ValueError("IBus stream chunk must be 80, 160, 320, 560, or 1120 ms")
        if not 300 <= self.semantic_endpoint_silence_ms <= 2000:
            raise ValueError("IBus endpoint silence must be 300..2000 ms")
        if not 20 <= self.hard_chunk_seconds <= 30:
            raise ValueError("IBus hard chunk must be 20..30 seconds")
        if not 1.5 <= self.overlap_seconds <= 2.5:
            raise ValueError("IBus hard-chunk overlap must be 1.5..2.5 seconds")
        if not 1 <= self.rolling_context_segments <= 5:
            raise ValueError("IBus rolling context must retain 1..5 segments")
        return self


class LanguageProfile(StrictModel):
    primary: str
    fallbacks: tuple[str, ...] = ()
    punctuation: str

    @model_validator(mode="after")
    def primary_is_not_fallback(self) -> LanguageProfile:
        if self.primary in self.fallbacks:
            raise ValueError("primary model may not also be a fallback")
        if len(self.fallbacks) != len(set(self.fallbacks)):
            raise ValueError("fallback models must be unique")
        return self


class ClassroomProfiles(StrictModel):
    zh: LanguageProfile
    ja: LanguageProfile
    en: LanguageProfile


class ProfilesConfig(StrictModel):
    classroom: ClassroomProfiles


class AppConfig(StrictModel):
    config_version: Literal[1] = 1
    server: ServerConfig
    privacy: PrivacyConfig
    hardware: HardwareConfig
    classroom: ClassroomConfig
    quality: QualityConfig
    ibus: IBusConfig
    profiles: ProfilesConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read YAML configuration {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ConfigError(f"configuration root must be a string-keyed mapping: {path}")
    return raw


def _deep_merge(base: MutableMapping[str, Any], override: Mapping[str, Any]) -> None:
    for key, value in override.items():
        current = base.get(key)
        if isinstance(current, MutableMapping) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            base[key] = copy.deepcopy(value)


def migrate_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate supported historical user configuration to the current schema."""

    migrated = copy.deepcopy(dict(raw))
    version = migrated.get("config_version", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError("config_version must be an integer")
    if version < 0 or version > CURRENT_CONFIG_VERSION:
        raise ConfigError(f"unsupported config_version: {version}")
    if version == 0:
        server = migrated.get("server")
        if isinstance(server, dict) and "bind" in server:
            if "host" in server:
                raise ConfigError("legacy server.bind conflicts with server.host")
            server["host"] = server.pop("bind")
        privacy = migrated.get("privacy")
        if isinstance(privacy, dict) and "offline" in privacy:
            if "runtime_offline" in privacy:
                raise ConfigError("legacy privacy.offline conflicts with privacy.runtime_offline")
            privacy["runtime_offline"] = privacy.pop("offline")
        migrated["config_version"] = 1
    return migrated


def _environment_overrides(environment: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, raw_value in sorted(environment.items()):
        if not name.startswith(ENV_PREFIX):
            continue
        path = [part.lower() for part in name[len(ENV_PREFIX) :].split("__") if part]
        if not path:
            raise ConfigError(f"empty environment override path: {name}")
        try:
            value = yaml.safe_load(raw_value)
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid environment override {name}: {exc}") from exc
        cursor: dict[str, Any] = result
        for part in path[:-1]:
            nested = cursor.setdefault(part, {})
            if not isinstance(nested, dict):
                raise ConfigError(f"conflicting environment override path: {name}")
            cursor = nested
        cursor[path[-1]] = value
    return result


def load_config(
    user_path: Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load defaults, then user YAML, then ``CLASSSCRIBE__`` overrides."""

    merged = migrate_config(_read_yaml(DEFAULT_CONFIG_PATH))
    if user_path is not None and user_path.exists():
        _deep_merge(merged, migrate_config(_read_yaml(user_path)))
    overrides = _environment_overrides(os.environ if environment is None else environment)
    if "config_version" in overrides:
        raise ConfigError("config_version cannot be overridden through the environment")
    _deep_merge(merged, overrides)
    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
