from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.config import ConfigError, load_config, migrate_config
from classscribe.contracts import LanguageMode


def test_default_and_full_example_parse() -> None:
    default = load_config(environment={})
    example = load_config(Path("config/config.example.yaml"), environment={})
    assert default == example
    assert default.config_version == 1
    assert default.classroom.default_language is LanguageMode.JAPANESE
    assert default.privacy.runtime_offline is True
    assert default.hardware.one_heavy_worker is True
    assert default.classroom.auto_export == ("json", "markdown", "srt", "vtt")
    assert default.classroom.expected_speakers == "auto"
    assert default.classroom.prior_min == 1
    assert default.classroom.prior_typical == 2
    assert default.classroom.max_speakers == 12
    assert default.classroom.allow_overlap is True


def test_user_and_environment_override_precedence(tmp_path: Path) -> None:
    user = tmp_path / "config.yaml"
    user.write_text("config_version: 1\nserver:\n  port: 9000\n", encoding="utf-8")
    config = load_config(user, environment={"CLASSSCRIBE__SERVER__PORT": "9100"})
    assert config.server.port == 9100


def test_v0_migration_renames_legacy_fields(tmp_path: Path) -> None:
    user = tmp_path / "legacy.yaml"
    user.write_text("server:\n  bind: localhost\nprivacy:\n  offline: true\n", encoding="utf-8")
    config = load_config(user, environment={})
    assert config.config_version == 1
    assert config.server.host == "localhost"
    assert config.privacy.runtime_offline is True


def test_future_version_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unsupported"):
        migrate_config({"config_version": 2})


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("config_version: 1\nprivacy:\n  runtime_offline: false\n", "runtime_offline"),
        ("config_version: 1\nhardware:\n  one_heavy_worker: false\n", "one_heavy_worker"),
        ("config_version: 1\nserver:\n  host: 0.0.0.0\n", "host"),
        ("config_version: 1\nunknown: true\n", "unknown"),
    ],
)
def test_security_invariants_and_unknown_fields_are_rejected(
    yaml_text: str, message: str, tmp_path: Path
) -> None:
    user = tmp_path / "bad.yaml"
    user.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(user, environment={})


def test_environment_cannot_change_schema_version() -> None:
    with pytest.raises(ConfigError, match="cannot be overridden"):
        load_config(environment={"CLASSSCRIBE__CONFIG_VERSION": "1"})


@pytest.mark.parametrize(
    "classroom_yaml",
    [
        "structure_window_seconds: 239",
        "structure_window_seconds: 5401",
        "structure_overlap_seconds: 0",
        "structure_window_seconds: 240\n  structure_overlap_seconds: 240",
    ],
)
def test_structure_window_safety_limits_are_enforced(classroom_yaml: str, tmp_path: Path) -> None:
    user = tmp_path / "bad-window.yaml"
    user.write_text(f"config_version: 1\nclassroom:\n  {classroom_yaml}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"structure|greater than or equal|less than or equal"):
        load_config(user, environment={})


@pytest.mark.parametrize(
    "classroom_yaml",
    [
        "prior_min: 3\n  prior_typical: 2",
        "prior_typical: 3\n  speaker_context: ordinary_class",
        "max_speakers: 1\n  prior_typical: 2",
        "expected_speakers: 5\n  max_speakers: 4",
    ],
)
def test_invalid_speaker_policies_are_rejected(classroom_yaml: str, tmp_path: Path) -> None:
    user = tmp_path / "bad-speakers.yaml"
    user.write_text(f"config_version: 1\nclassroom:\n  {classroom_yaml}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"speaker|expected|prior_typical"):
        load_config(user, environment={})
