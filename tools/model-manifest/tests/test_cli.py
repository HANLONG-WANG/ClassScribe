from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from classscribe_manifest_tool import cli
from classscribe_manifest_tool.discovery import DiscoveredFile, RepositoryDiscovery
from classscribe_manifest_tool.inputs import ReleaseInputs, ReleaseModel
from classscribe_manifest_tool.selection import FileSelectionIndex, ModelFileSelection
from classscribe_manifest_tool.verifier import BundleVerificationResult

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("arguments", "handler"),
    [
        (
            [
                "discover",
                "--registry",
                "registry.yaml",
                "--selection",
                "selection.yaml",
                "--output",
                "reports",
            ],
            "_run_discover",
        ),
        (
            [
                "generate",
                "--registry",
                "registry.yaml",
                "--revisions",
                "revisions.json",
                "--licenses",
                "licenses.json",
                "--selection",
                "selection.yaml",
                "--output",
                "bundle",
            ],
            "_run_generate",
        ),
        (["verify", "--bundle", "bundle.v1.json"], "_run_verify"),
        (
            [
                "status",
                "--registry",
                "registry.yaml",
                "--selection",
                "selection.yaml",
            ],
            "_run_status",
        ),
    ],
)
def test_parser_exposes_release_commands(arguments: list[str], handler: str) -> None:
    parsed = cli.build_parser().parse_args(arguments)

    assert parsed.command == arguments[0]
    assert parsed.handler.__name__ == handler


def test_status_command_checks_every_production_registry_model_without_network(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "status",
            "--registry",
            str(ROOT / "config/model-registry.v1.yaml"),
            "--selection",
            str(ROOT / "config/model-file-selection.v1.yaml"),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["status"] == "checked"
    assert output["bundle"] == "missing"
    assert len(output["models"]) == 20
    assert [item["model_id"] for item in output["models"]] == sorted(
        item["model_id"] for item in output["models"]
    )
    assert all(item["manifest_available"] is False for item in output["models"])
    models = {item["model_id"]: item for item in output["models"]}
    assert {
        key: models["moss_td_0_9b"][key]
        for key in ("manifest_available", "worker_implemented", "installable", "enabled")
    } == {
        "manifest_available": False,
        "worker_implemented": True,
        "installable": False,
        "enabled": True,
    }
    assert {
        key: models["granite_speech_5_0_turboctc_470m"][key]
        for key in ("manifest_available", "worker_implemented", "installable", "enabled")
    } == {
        "manifest_available": False,
        "worker_implemented": False,
        "installable": False,
        "enabled": True,
    }
    assert {
        key: models["whisper_tiny_reference"][key]
        for key in ("manifest_available", "worker_implemented", "installable", "enabled")
    } == {
        "manifest_available": False,
        "worker_implemented": True,
        "installable": False,
        "enabled": False,
    }


def test_discover_command_uses_fixed_revision_and_writes_review_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    revision = "a" * 40
    selection = ModelFileSelection(
        include=("config.json",),
        kinds=MappingProxyType({"config.json": "config"}),
        exclude=(),
    )
    alpha = ReleaseModel(
        model_id="alpha",
        repository="owner/model",
        revision=revision,
        worker="worker",
        runtime_backend="fixture_backend",
        dtype="float32",
        trust_remote_code=False,
        dependency_lock="workers/worker/uv.lock",
        dependency_lock_sha256="b" * 64,
        license_id="Apache-2.0",
        license_url="https://huggingface.co/owner/model",
        requires_terms_acceptance=False,
        enabled=True,
    )
    inputs = ReleaseInputs(
        root=tmp_path,
        registry_revision=1,
        facts_as_of="2026-09-03",
        models=(
            alpha,
            replace(alpha, model_id="beta", repository="owner/other"),
        ),
        selections=FileSelectionIndex(1, MappingProxyType({"alpha": selection})),
    )
    discovery = RepositoryDiscovery(
        "owner/model",
        revision,
        (DiscoveredFile("config.json", 6, "git", "config-oid", None, None),),
    )
    calls: list[tuple[str, str, str | None]] = []

    def fake_discover(
        repository: str, requested_revision: str, *, token: str | None
    ) -> RepositoryDiscovery:
        calls.append((repository, requested_revision, token))
        return discovery

    monkeypatch.setattr(cli, "load_hf_token", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_release_inputs_for_discovery", lambda _args: inputs)
    monkeypatch.setattr(cli, "discover_repository_tree", fake_discover)
    output = tmp_path / "discovery"

    result = cli.main(
        [
            "discover",
            "--registry",
            str(tmp_path / "config/model-registry.v1.yaml"),
            "--selection",
            str(tmp_path / "config/model-file-selection.v1.yaml"),
            "--output",
            str(output),
            "--model-id",
            "alpha",
        ]
    )

    reported = json.loads(capsys.readouterr().out)
    assert result == 0
    assert calls == [("owner/model", revision, None)]
    assert reported == {
        "status": "discovered",
        "model_count": 1,
        "reports": ["alpha.discovery.json"],
    }
    report = json.loads((output / "alpha.discovery.json").read_text(encoding="utf-8"))
    assert report["revision"] == revision
    assert report["purpose"] == "selection_review_only"


@pytest.mark.parametrize(
    ("requested", "message"),
    [
        (("alpha", "alpha"), "discovery model IDs must be unique"),
        (("missing",), "discovery model IDs are not in the registry"),
    ],
)
def test_discover_model_filter_rejects_invalid_ids_before_network(
    requested: tuple[str, ...],
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = ReleaseModel(
        model_id="alpha",
        repository="owner/model",
        revision="a" * 40,
        worker="worker",
        runtime_backend="fixture_backend",
        dtype="float32",
        trust_remote_code=False,
        dependency_lock="workers/worker/uv.lock",
        dependency_lock_sha256="b" * 64,
        license_id="Apache-2.0",
        license_url="https://huggingface.co/owner/model",
        requires_terms_acceptance=False,
        enabled=True,
    )
    inputs = ReleaseInputs(
        root=tmp_path,
        registry_revision=1,
        facts_as_of="2026-09-03",
        models=(model,),
        selections=FileSelectionIndex(1, MappingProxyType({})),
    )
    network_calls: list[str] = []
    monkeypatch.setattr(cli, "load_hf_token", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_release_inputs_for_discovery", lambda _args: inputs)
    monkeypatch.setattr(
        cli,
        "discover_repository_tree",
        lambda repository, *_args, **_kwargs: network_calls.append(repository),
    )

    arguments = [
        "discover",
        "--registry",
        str(tmp_path / "config/model-registry.v1.yaml"),
        "--selection",
        str(tmp_path / "config/model-file-selection.v1.yaml"),
        "--output",
        str(tmp_path / "reports"),
    ]
    for model_id in requested:
        arguments.extend(("--model-id", model_id))
    result = cli.main(arguments)

    captured = capsys.readouterr()
    assert result == 2
    assert message in captured.err
    assert network_calls == []


def test_generate_command_wires_frozen_inputs_double_generation_write_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = argparse.Namespace(root=tmp_path)
    manifests = {"alpha": b"manifest\n"}
    bundle = b"bundle\n"
    output = tmp_path / "config/model-manifests/v1"
    calls: dict[str, object] = {}

    def fake_load_release_inputs(**kwargs: object) -> argparse.Namespace:
        calls["inputs"] = kwargs
        return inputs

    def fake_generate_release_bundle(
        loaded_inputs: object, **kwargs: object
    ) -> tuple[dict[str, bytes], bytes]:
        calls["generate"] = (loaded_inputs, kwargs)
        return manifests, bundle

    def fake_write_release_bundle(
        directory: Path,
        generated_manifests: object,
        generated_bundle: bytes,
        *,
        token: str | None,
    ) -> None:
        calls["write"] = (directory, generated_manifests, generated_bundle, token)

    verification = BundleVerificationResult("a" * 64, 1, {"alpha": "b" * 64})
    monkeypatch.setattr(cli, "load_hf_token", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "load_release_inputs", fake_load_release_inputs)
    monkeypatch.setattr(cli, "read_source_date_epoch", lambda _environment: 1788451200)
    monkeypatch.setattr(cli, "generate_release_bundle", fake_generate_release_bundle)
    monkeypatch.setattr(cli, "write_release_bundle", fake_write_release_bundle)
    monkeypatch.setattr(cli, "verify_bundle", lambda path, *, root: verification)

    result = cli.main(
        [
            "generate",
            "--registry",
            str(tmp_path / "config/model-registry.v1.yaml"),
            "--revisions",
            str(tmp_path / "config/model-revisions.lock.json"),
            "--licenses",
            str(tmp_path / "config/model-licenses.v1.json"),
            "--selection",
            str(tmp_path / "config/model-file-selection.v1.yaml"),
            "--output",
            str(output),
        ]
    )

    reported = json.loads(capsys.readouterr().out)
    assert result == 0
    assert reported == verification.as_dict()
    assert calls["generate"] == (
        inputs,
        {
            "source_date_epoch": 1788451200,
            "token": None,
            "accepted_repositories": [],
        },
    )
    assert calls["write"] == (output, manifests, bundle, None)


def test_verify_command_dispatches_to_offline_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = tmp_path / "config/model-manifests/v1/bundle.v1.json"
    verification = BundleVerificationResult("a" * 64, 1, {"alpha": "b" * 64})
    seen: list[Path] = []

    def fake_verify(path: Path) -> BundleVerificationResult:
        seen.append(path)
        return verification

    monkeypatch.setattr(cli, "verify_bundle", fake_verify)

    result = cli.main(["verify", "--bundle", str(bundle)])

    assert result == 0
    assert seen == [bundle]
    assert json.loads(capsys.readouterr().out) == verification.as_dict()


def test_cli_error_output_and_logs_redact_environment_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "hf_cli_diagnostic_secret"
    monkeypatch.setenv("HF_TOKEN", token)

    def fail_inputs(**_kwargs: object) -> object:
        raise RuntimeError(f"failure included {token} Authorization: Bearer {token}")

    monkeypatch.setattr(cli, "load_release_inputs", fail_inputs)

    result = cli.main(
        [
            "generate",
            "--registry",
            str(tmp_path / "config/model-registry.v1.yaml"),
            "--revisions",
            str(tmp_path / "config/model-revisions.lock.json"),
            "--licenses",
            str(tmp_path / "config/model-licenses.v1.json"),
            "--selection",
            str(tmp_path / "config/model-file-selection.v1.yaml"),
            "--output",
            str(tmp_path / "config/model-manifests/v1"),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    assert result == 2
    assert captured.out == ""
    assert token not in combined
    assert "<redacted>" in captured.err
