from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from classscribe.diagnostics import DiagnosticSnapshot
from classscribe.paths import AppPaths
from classscribe.system_check import CheckStatus, DependencyResult, SystemReport


def test_unified_doctor_reports_dependencies_diagnostics_and_bundle(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    import classscribe.doctor as doctor

    report = SystemReport(
        "Fedora fixture",
        (DependencyResult("Bubblewrap", CheckStatus.OK, "available"),),
    )
    snapshot = DiagnosticSnapshot(
        generated_at="2026-09-04T00:00:00+00:00",
        system={"distribution": "Fedora fixture"},
        gpu={"status": "unavailable"},
        components=(),
    )

    class Checker:
        def check(self) -> SystemReport:
            return report

    class Collector:
        def collect(self) -> DiagnosticSnapshot:
            return snapshot

    monkeypatch.setattr(doctor, "FedoraDependencyChecker", Checker)
    monkeypatch.setattr(doctor, "DiagnosticCollector", Collector)
    bundle = tmp_path / "diagnostics.zip"

    assert doctor.main(["--bundle", str(bundle)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dependencies"]["ready"] is True
    assert payload["system"]["distribution"] == "Fedora fixture"
    assert payload["configuration"]["runtime_offline"] is True
    assert payload["bundle"] == {"written": True, "path": str(bundle)}
    assert bundle.is_file() and bundle.stat().st_mode & 0o777 == 0o600

    failed = SystemReport(
        "Fedora fixture",
        (DependencyResult("Bubblewrap", CheckStatus.MISSING, "missing"),),
    )
    monkeypatch.setattr(
        doctor, "FedoraDependencyChecker", lambda: type("C", (), {"check": lambda _self: failed})()
    )
    assert doctor.main([]) == 1


def test_core_cli_health_config_and_server_paths(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    import classscribe.cli as cli

    assert cli.main(["--health-check"]) == 0
    assert json.loads(capsys.readouterr().out)["service"] == "classscribe-core"
    assert cli.main(["--check-config"]) == 0
    assert json.loads(capsys.readouterr().out)["config_version"] == 1

    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    calls: dict[str, Any] = {}

    class Tokens:
        def __init__(self, path: Path) -> None:
            calls["token_path"] = path

        def load_or_create(self) -> str:
            return "token"

    def create_app(**kwargs: Any) -> str:
        calls["app"] = kwargs
        return "application"

    def run(app: Any, **kwargs: Any) -> None:
        calls["run"] = (app, kwargs)

    monkeypatch.setattr("classscribe.cli.AppPaths.from_environment", lambda: paths)
    monkeypatch.setattr(cli, "TokenStore", Tokens)
    monkeypatch.setattr(cli, "resource_path", lambda _name: tmp_path / "web")
    monkeypatch.setattr(cli, "create_app", create_app)
    monkeypatch.setattr("classscribe.cli.uvicorn.run", run)

    assert cli.main([]) == 0
    assert calls["token_path"] == paths.config / "api-token"
    assert calls["app"].pop("config").server.port == calls["run"][1]["port"]
    assert calls["app"] == {
        "api_token": "token",
        "enable_scheduler_ipc": True,
        "runtime_paths": paths,
        "static_directory": tmp_path / "web",
    }
    assert calls["run"][0] == "application"
    assert calls["run"][1]["host"] == "127.0.0.1"
    assert calls["run"][1]["access_log"] is False


def test_cli_passes_selected_config_to_application(tmp_path: Path, monkeypatch: Any) -> None:
    import classscribe.cli as cli

    paths = AppPaths.from_environment({}, home=tmp_path)
    paths.ensure()
    (paths.config / "config.yaml").write_text("config_version: 1\nserver:\n  port: 8765\n")
    custom = tmp_path / "custom.yaml"
    custom.write_text("config_version: 1\nserver:\n  port: 8766\n")
    monkeypatch.setattr("classscribe.cli.AppPaths.from_environment", lambda: paths)
    monkeypatch.setattr(cli, "resource_path", lambda _: tmp_path)
    monkeypatch.setattr(cli, "create_app", lambda **kwargs: kwargs["config"])
    seen: list[int] = []

    def run(config: Any, **kwargs: Any) -> None:
        assert config.server.port == kwargs["port"]
        seen.append(config.server.port)

    monkeypatch.setattr("classscribe.cli.uvicorn.run", run)
    cli.main([])
    cli.main(["--config", str(custom)])
    assert seen == [8765, 8766]
