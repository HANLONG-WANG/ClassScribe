"""Private temporary workspace lifecycle with sanitized failure reporting."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from classscribe_manifest_tool.credentials import (
    assert_hf_token_absent,
    redact_for_diagnostics,
)


@contextmanager
def private_temporary_workspace(
    *, token: str | None, parent: Path | None = None
) -> Iterator[Path]:
    """Create a fixed-name 0700 workspace and always attempt sanitized cleanup."""
    workspace = Path(
        tempfile.mkdtemp(
            prefix="classscribe-model-manifest-",
            dir=None if parent is None else parent,
        )
    )
    workspace.chmod(0o700)
    assert_hf_token_absent(workspace, token=token, destination="temporary path")
    operation_failure: RuntimeError | None = None
    try:
        yield workspace
    except Exception as error:
        diagnostic = redact_for_diagnostics(error, token=token)
        operation_failure = RuntimeError(f"manifest operation failed: {diagnostic}")
    try:
        shutil.rmtree(workspace)
    except Exception as error:
        diagnostic = redact_for_diagnostics(error, token=token)
        cleanup_failure = RuntimeError(f"temporary workspace cleanup failed: {diagnostic}")
        if operation_failure is not None:
            raise cleanup_failure from operation_failure
        raise cleanup_failure from None
    if operation_failure is not None:
        raise operation_failure from None

