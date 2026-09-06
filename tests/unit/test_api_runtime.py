from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.api import runtime
from classscribe.resources import resource_path as actual_resource_path


def _set_fresh_xdg(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    for name, directory in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_RUNTIME_DIR", "runtime"),
    ):
        monkeypatch.setenv(name, str(root / directory))
    monkeypatch.delenv("CLASSSCRIBE_RESOURCE_ROOT", raising=False)


def test_default_service_loads_the_verified_production_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fresh_xdg(monkeypatch, tmp_path)

    service = runtime.build_default_service()

    assert service.manifest_bundle is not None
    assert len(service.manifest_bundle.index.manifests) == 20
    models = {item["id"]: item for item in service.models()}
    assert models["moss_td_0_9b"]["manifest_available"] is True
    assert models["pyannote_community_1"]["component_source_count"] == 1
    assert models["firered_asr2_llm"]["installable"] is False


def test_default_service_fails_before_xdg_writes_when_bundle_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fresh_xdg(monkeypatch, tmp_path)

    def missing_bundle(relative: str) -> Path:
        if relative == "config/model-manifests/v1/bundle.v1.json":
            raise FileNotFoundError("production bundle missing")
        return actual_resource_path(relative)

    monkeypatch.setattr(runtime, "resource_path", missing_bundle)

    with pytest.raises(FileNotFoundError, match="production bundle missing"):
        runtime.build_default_service()
    assert not (tmp_path / "data").exists()


def test_default_service_fails_before_xdg_writes_when_bundle_is_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fresh_xdg(monkeypatch, tmp_path)

    def reject_tampered_bundle(*_args: object, **_kwargs: object) -> None:
        raise ValueError("bundle manifest member SHA-256 differs from index")

    monkeypatch.setattr(runtime, "load_builtin_manifest_bundle", reject_tampered_bundle)

    with pytest.raises(ValueError, match="member SHA-256 differs"):
        runtime.build_default_service()
    assert not (tmp_path / "data").exists()
