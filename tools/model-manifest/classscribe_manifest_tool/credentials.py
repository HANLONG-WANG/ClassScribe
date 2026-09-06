"""Credential input boundary for gated release-time repository access."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Collection, Mapping
from pathlib import Path
from types import MappingProxyType

TOKEN_ENVIRONMENT_VARIABLE = "HF_TOKEN"
MAX_TOKEN_FILE_BYTES = 4096
REDACTED = "<redacted>"
_AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\]}]+"
)


def load_hf_token(
    *,
    token_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Load an optional token from exactly one approved source."""
    environment = os.environ if environ is None else environ
    environment_token = environment.get(TOKEN_ENVIRONMENT_VARIABLE)
    if token_file is not None and environment_token is not None:
        raise ValueError("HF token must be provided by either environment or file, not both")
    if token_file is not None:
        return _read_private_token_file(token_file)
    if environment_token is None:
        return None
    return _validate_token(environment_token)


def authorization_headers(token: str | None) -> Mapping[str, str]:
    """Return the only transport representation allowed to contain a token."""
    if token is None:
        return MappingProxyType({})
    validated = _validate_token(token)
    return MappingProxyType({"Authorization": f"Bearer {validated}"})


def redact_for_diagnostics(value: object, *, token: str | None) -> str:
    """Render diagnostic text with known and header-shaped credentials removed."""
    rendered = str(value)
    if token:
        rendered = rendered.replace(token, REDACTED)
    return _AUTHORIZATION_RE.sub(rf"\1{REDACTED}", rendered)


def assert_hf_token_absent(value: object, *, token: str | None, destination: str) -> None:
    """Fail closed before serializing a token into output or a temporary path."""
    if token is None:
        return
    if isinstance(value, bytes):
        contains_token = token.encode("utf-8") in value
    elif isinstance(value, (str, Path)):
        contains_token = token in str(value)
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as error:
            raise ValueError(f"cannot inspect {destination} for credentials") from error
        contains_token = token in rendered
    if contains_token:
        raise ValueError(f"refusing to write HF token into {destination}")


def require_gated_access_approval(
    repository: str,
    *,
    requires_terms_acceptance: bool,
    accepted_repositories: Collection[str],
    token: str | None,
) -> None:
    """Require a user's upstream acceptance assertion before gated access."""
    if not requires_terms_acceptance:
        return
    if repository not in accepted_repositories:
        raise PermissionError(
            "gated repository access requires the user to accept the upstream terms first "
            "and explicitly confirm that acceptance"
        )
    if token is None:
        raise PermissionError("gated repository access requires an authorized HF token")


def _read_private_token_file(path: Path) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("HF token file must be a readable regular non-symlink file") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("HF token file must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("HF token file permissions must be exactly 0600")
        if metadata.st_uid != os.getuid():
            raise ValueError("HF token file must be owned by the current user")
        if metadata.st_size > MAX_TOKEN_FILE_BYTES:
            raise ValueError("HF token file is too large")
        content = os.read(descriptor, MAX_TOKEN_FILE_BYTES + 1)
        if len(content) > MAX_TOKEN_FILE_BYTES:
            raise ValueError("HF token file is too large")
    finally:
        os.close(descriptor)
    try:
        token = content.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError as error:
        raise ValueError("HF token file must contain UTF-8 text") from error
    return _validate_token(token)


def _validate_token(token: str) -> str:
    if not token or len(token.encode("utf-8")) > MAX_TOKEN_FILE_BYTES:
        raise ValueError("HF token must be non-empty and bounded")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in token
    ):
        raise ValueError("HF token must not contain whitespace or control characters")
    return token
