from __future__ import annotations

import json
from pathlib import Path

import pytest

from classscribe_manifest_tool.credentials import (
    REDACTED,
    assert_hf_token_absent,
    authorization_headers,
    load_hf_token,
    redact_for_diagnostics,
    require_gated_access_approval,
)

ROOT = Path(__file__).resolve().parents[3]


def test_hf_token_can_be_loaded_from_environment() -> None:
    assert load_hf_token(environ={"HF_TOKEN": "hf_environment"}) == "hf_environment"
    assert load_hf_token(environ={}) is None


def test_hf_token_can_be_loaded_from_private_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("hf_private\n", encoding="utf-8")
    token_file.chmod(0o600)

    assert load_hf_token(token_file=token_file, environ={}) == "hf_private"


def test_hf_token_rejects_ambiguous_sources(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("hf_private", encoding="utf-8")
    token_file.chmod(0o600)

    with pytest.raises(ValueError, match="either environment or file"):
        load_hf_token(token_file=token_file, environ={"HF_TOKEN": "hf_environment"})


def test_hf_token_rejects_non_private_permissions(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("hf_private", encoding="utf-8")
    token_file.chmod(0o640)

    with pytest.raises(ValueError, match="exactly 0600"):
        load_hf_token(token_file=token_file, environ={})


def test_hf_token_rejects_symlink(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("hf_private", encoding="utf-8")
    token_file.chmod(0o600)
    link = tmp_path / "token-link"
    link.symlink_to(token_file)

    with pytest.raises(ValueError, match="non-symlink"):
        load_hf_token(token_file=link, environ={})


@pytest.mark.parametrize("token", ["", "hf token", "hf_token\nembedded"])
def test_hf_token_rejects_empty_or_whitespace_values(token: str) -> None:
    with pytest.raises(ValueError):
        load_hf_token(environ={"HF_TOKEN": token})


def test_hf_token_transport_representation_is_only_authorization_header() -> None:
    token = "hf_transport_secret"

    assert dict(authorization_headers(token)) == {"Authorization": f"Bearer {token}"}
    assert dict(authorization_headers(None)) == {}


def test_hf_token_is_redacted_from_diagnostics() -> None:
    token = "hf_diagnostic_secret"
    failure = RuntimeError(
        f"request failed token={token} Authorization: Bearer {token}"
    )

    diagnostic = redact_for_diagnostics(failure, token=token)

    assert token not in diagnostic
    assert diagnostic.count(REDACTED) == 2


@pytest.mark.parametrize("destination", ["manifest", "bundle", "temporary path"])
def test_hf_token_cannot_be_serialized_to_outputs(destination: str) -> None:
    token = "hf_output_secret"

    with pytest.raises(ValueError, match=f"into {destination}") as failure:
        assert_hf_token_absent(
            {"nested": ["public", token]}, token=token, destination=destination
        )

    assert token not in str(failure.value)


def test_hf_token_absence_check_accepts_public_output() -> None:
    assert_hf_token_absent(
        {"repository": "owner/model"},
        token="hf_output_secret",
        destination="manifest",
    )


def test_gated_access_requires_explicit_upstream_acceptance_and_token() -> None:
    repository = "pyannote/speaker-diarization-community-1"

    with pytest.raises(PermissionError, match="accept the upstream terms first"):
        require_gated_access_approval(
            repository,
            requires_terms_acceptance=True,
            accepted_repositories=(),
            token="hf_authorized",
        )
    with pytest.raises(PermissionError, match="authorized HF token"):
        require_gated_access_approval(
            repository,
            requires_terms_acceptance=True,
            accepted_repositories=(repository,),
            token=None,
        )

    require_gated_access_approval(
        repository,
        requires_terms_acceptance=True,
        accepted_repositories=(repository,),
        token="hf_authorized",
    )


def test_public_repository_does_not_require_gated_approval() -> None:
    require_gated_access_approval(
        "owner/public",
        requires_terms_acceptance=False,
        accepted_repositories=(),
        token=None,
    )


def test_license_inventory_marks_only_current_pyannote_repository_as_gated() -> None:
    inventory = json.loads(
        (ROOT / "config/model-licenses.v1.json").read_text(encoding="utf-8")
    )
    gated = {
        item["repository"]
        for item in inventory["models"]
        if item["requires_terms_acceptance"]
    }

    assert gated == {"pyannote/speaker-diarization-community-1"}
