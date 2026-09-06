"""Fixed-revision recursive repository tree discovery."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx
from huggingface_hub import HfApi
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)
from huggingface_hub.hf_api import RepoFile, RepoFolder

from classscribe_manifest_tool.credentials import (
    assert_hf_token_absent,
    authorization_headers,
    redact_for_diagnostics,
)
from classscribe_manifest_tool.selection import (
    MODEL_ID_RE,
    ModelFileSelection,
    validate_exact_path,
)

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[^\s/]+/[^\s/]+$")
DISCOVERY_REPORT_SUFFIX = ".discovery.json"


class RepositoryTreeApi(Protocol):
    """The narrow Hugging Face tree API used by discovery."""

    def model_info(
        self,
        repo_id: str,
        *,
        revision: str,
        files_metadata: bool,
        token: bool,
    ) -> object: ...

    def list_repo_tree(
        self,
        repo_id: str,
        *,
        recursive: bool,
        revision: str,
        repo_type: str,
        token: bool,
    ) -> Iterable[RepoFile | RepoFolder]: ...


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """Untrusted upstream metadata used only to curate a selection."""

    path: str
    size: int
    storage: Literal["git", "lfs", "xet"]
    blob_id: str
    lfs_sha256: str | None
    xet_hash: str | None


@dataclass(frozen=True, slots=True)
class RepositoryDiscovery:
    repository: str
    revision: str
    files: tuple[DiscoveredFile, ...]


class RepositoryDiscoveryError(RuntimeError):
    """A stable, sanitized repository discovery failure."""


class RepositoryUnavailableError(RepositoryDiscoveryError):
    """The requested repository identity does not exist or is unavailable."""


class RevisionResolutionError(RepositoryDiscoveryError):
    """The requested revision is missing or resolves to a different commit."""


class GatedRepositoryAccessError(RepositoryDiscoveryError, PermissionError):
    """The repository requires access that the current credential lacks."""


def discover_repository_tree(
    repository: str,
    revision: str,
    *,
    token: str | None,
    api: RepositoryTreeApi | None = None,
) -> RepositoryDiscovery:
    """Recursively enumerate regular files at one explicit full commit."""
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("repository must have an owner/name identity")
    if not COMMIT_RE.fullmatch(revision):
        raise ValueError("repository discovery revision must be a full lowercase commit")
    client: RepositoryTreeApi = api or HfApi(
        token=False,
        library_name="classscribe-model-manifest",
        library_version="1",
        headers=dict(authorization_headers(token)),
    )
    discovered: list[DiscoveredFile] = []
    try:
        information = client.model_info(
            repository,
            revision=revision,
            files_metadata=False,
            token=False,
        )
        resolved_revision = getattr(information, "sha", None)
        if resolved_revision != revision:
            raise RevisionResolutionError(
                "repository revision did not resolve to the requested commit"
            )
        for item in client.list_repo_tree(
            repository,
            recursive=True,
            revision=revision,
            repo_type="model",
            token=False,
        ):
            if not isinstance(item, RepoFile):
                continue
            validate_exact_path(item.path, "discovered repository tree")
            if item.size < 0 or not item.blob_id:
                raise ValueError(f"repository tree has invalid metadata for {item.path}")
            storage: Literal["git", "lfs", "xet"] = (
                "lfs" if item.lfs is not None else "xet" if item.xet_hash is not None else "git"
            )
            discovered.append(
                DiscoveredFile(
                    path=item.path,
                    size=item.size,
                    storage=storage,
                    blob_id=item.blob_id,
                    lfs_sha256=None if item.lfs is None else item.lfs.sha256,
                    xet_hash=item.xet_hash,
                )
            )
    except RevisionResolutionError:
        raise
    except GatedRepoError:
        raise GatedRepositoryAccessError(
            "gated repository access was denied; accept upstream terms and verify authorization"
        ) from None
    except RevisionNotFoundError:
        raise RevisionResolutionError("repository revision does not exist") from None
    except RepositoryNotFoundError:
        raise RepositoryUnavailableError("repository does not exist or is not accessible") from None
    except HfHubHTTPError as error:
        status = error.response.status_code
        if status in {401, 403}:
            raise GatedRepositoryAccessError(
                "repository access was denied; verify upstream terms and authorization"
            ) from None
        diagnostic = redact_for_diagnostics(error, token=token)
        raise RepositoryDiscoveryError(f"repository discovery failed: {diagnostic}") from None
    except httpx.HTTPError as error:
        diagnostic = redact_for_diagnostics(error, token=token)
        raise RepositoryDiscoveryError(f"repository discovery failed: {diagnostic}") from None
    discovered.sort(key=lambda item: item.path)
    paths = [item.path for item in discovered]
    if len(paths) != len(set(paths)):
        raise ValueError("repository tree repeats a file path")
    return RepositoryDiscovery(repository=repository, revision=revision, files=tuple(discovered))


def write_discovery_report(
    model_id: str,
    discovery: RepositoryDiscovery,
    output_directory: Path,
    *,
    token: str | None,
) -> Path:
    """Atomically write a private selection-review report outside the runtime bundle."""
    if not MODEL_ID_RE.fullmatch(model_id):
        raise ValueError("discovery report model ID is unsafe")
    _prepare_private_output_directory(output_directory)
    destination = output_directory / f"{model_id}{DISCOVERY_REPORT_SUFFIX}"
    if destination.is_symlink():
        raise ValueError("discovery report destination must not be a symlink")
    value = {
        "report_version": 1,
        "purpose": "selection_review_only",
        "model_id": model_id,
        "repository": discovery.repository,
        "revision": discovery.revision,
        "files": [asdict(item) for item in discovery.files],
    }
    assert_hf_token_absent(destination, token=token, destination="discovery report path")
    assert_hf_token_absent(value, token=token, destination="discovery report")
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=output_directory
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def validate_selection_against_discovery(
    selection: ModelFileSelection,
    discovery: RepositoryDiscovery,
) -> tuple[DiscoveredFile, ...]:
    """Require include and exclude to form an exact partition of the discovered tree."""
    by_path = {item.path: item for item in discovery.files}
    if len(by_path) != len(discovery.files):
        raise ValueError("repository discovery repeats a file path")
    discovered_paths = set(by_path)
    selected_paths = set(selection.include)
    excluded_paths = set(selection.exclude)
    missing = (selected_paths | excluded_paths) - discovered_paths
    if missing:
        raise ValueError(f"selection paths are missing from repository tree: {sorted(missing)}")
    unclassified = discovered_paths - selected_paths - excluded_paths
    if unclassified:
        raise ValueError(
            f"repository tree contains unclassified extra paths: {sorted(unclassified)}"
        )
    selected = tuple(by_path[path] for path in selection.include)
    unknown_sizes = [
        item.path
        for item in selected
        if isinstance(item.size, bool) or not isinstance(item.size, int) or item.size < 0
    ]
    if unknown_sizes:
        raise ValueError(f"selected file sizes cannot be confirmed: {unknown_sizes}")
    return selected


def _prepare_private_output_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("discovery output directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("discovery output must be a directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("discovery output directory permissions must be exactly 0700")
