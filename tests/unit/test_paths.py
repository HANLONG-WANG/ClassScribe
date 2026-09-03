from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from classscribe.paths import AppPaths, PathSecurityError, validate_restricted_directory
from classscribe.resources import resource_path, resource_root


def test_xdg_defaults_and_required_layout(tmp_path: Path) -> None:
    paths = AppPaths.from_environment({}, home=tmp_path)
    assert paths.config == tmp_path / ".config" / "classscribe"
    assert paths.data == tmp_path / ".local" / "share" / "classscribe"
    assert paths.cache == tmp_path / ".cache" / "classscribe"
    assert paths.state == tmp_path / ".local" / "state" / "classscribe"
    assert paths.runtime == paths.state / "run"

    paths.ensure()
    for directory in paths.required_directories():
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_absolute_xdg_overrides(tmp_path: Path) -> None:
    environment = {
        "XDG_CONFIG_HOME": str(tmp_path / "c"),
        "XDG_DATA_HOME": str(tmp_path / "d"),
        "XDG_CACHE_HOME": str(tmp_path / "k"),
        "XDG_STATE_HOME": str(tmp_path / "s"),
        "XDG_RUNTIME_DIR": str(tmp_path / "r"),
    }
    paths = AppPaths.from_environment(environment, home=tmp_path / "home")
    assert paths.runtime == tmp_path / "r" / "classscribe"
    paths.ensure()


def test_relative_xdg_override_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError, match="absolute"):
        AppPaths.from_environment({"XDG_DATA_HOME": "relative"}, home=tmp_path)


@pytest.mark.parametrize("parts", [("..", "escape"), ("/absolute",), ("",)])
def test_data_path_rejects_escape(parts: tuple[str, ...], tmp_path: Path) -> None:
    paths = AppPaths.from_environment({}, home=tmp_path)
    with pytest.raises(PathSecurityError):
        paths.data_path(*parts)


def test_data_path_rejects_symlink_escape(tmp_path: Path) -> None:
    paths = AppPaths.from_environment({}, home=tmp_path)
    paths.ensure()
    outside = tmp_path / "outside"
    outside.mkdir()
    (paths.data / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSecurityError, match="escapes"):
        paths.data_path("linked", "private.txt")


def test_restricted_directory_rejects_broad_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "unsafe"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(PathSecurityError, match="permissions"):
        validate_restricted_directory(directory)


def test_job_layout_uses_single_safe_identifier(tmp_path: Path) -> None:
    paths = AppPaths.from_environment({}, home=tmp_path)
    job_paths = paths.job_paths("job-01")
    assert job_paths[0] == paths.data / "jobs" / "job-01"
    assert {path.name for path in job_paths[1:]} == {
        "source",
        "derived",
        "candidates",
        "exports",
    }
    with pytest.raises(PathSecurityError):
        paths.job_paths("nested/job")


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX ownership check")
def test_current_user_owned_directory_is_accepted(tmp_path: Path) -> None:
    directory = tmp_path / "safe"
    directory.mkdir(mode=0o700)
    validate_restricted_directory(directory)


def test_immutable_resource_override_is_absolute_bounded_and_non_symlink(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    root.mkdir()
    (root / "asset.txt").write_text("asset", encoding="utf-8")
    environment = {"CLASSSCRIBE_RESOURCE_ROOT": str(root)}
    assert resource_root(environment) == root
    assert resource_path("asset.txt", environment).read_text(encoding="utf-8") == "asset"
    with pytest.raises(ValueError, match="safe and relative"):
        resource_path("../asset.txt", environment)
    linked = tmp_path / "linked"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        resource_root({"CLASSSCRIBE_RESOURCE_ROOT": str(linked)})
