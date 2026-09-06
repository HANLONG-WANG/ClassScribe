from __future__ import annotations

import json
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)
from huggingface_hub.hf_api import RepoFile, RepoFolder

from classscribe_manifest_tool.discovery import (
    DiscoveredFile,
    GatedRepositoryAccessError,
    RepositoryDiscovery,
    RepositoryUnavailableError,
    RevisionResolutionError,
    discover_repository_tree,
    validate_selection_against_discovery,
    write_discovery_report,
)
from classscribe_manifest_tool.selection import ModelFileSelection


@dataclass(frozen=True)
class FakeModelInfo:
    sha: str


class FakeTreeApi:
    def __init__(
        self,
        items: Iterable[RepoFile | RepoFolder],
        *,
        resolved_revision: str = "a" * 40,
        failure: Exception | None = None,
    ) -> None:
        self.items = tuple(items)
        self.resolved_revision = resolved_revision
        self.failure = failure
        self.info_calls: list[dict[str, object]] = []
        self.calls: list[dict[str, object]] = []

    def model_info(
        self,
        repo_id: str,
        *,
        revision: str,
        files_metadata: bool,
        token: bool,
    ) -> FakeModelInfo:
        self.info_calls.append(
            {
                "repo_id": repo_id,
                "revision": revision,
                "files_metadata": files_metadata,
                "token": token,
            }
        )
        if self.failure is not None:
            raise self.failure
        return FakeModelInfo(self.resolved_revision)

    def list_repo_tree(
        self,
        repo_id: str,
        *,
        recursive: bool,
        revision: str,
        repo_type: str,
        token: bool,
    ) -> Iterable[RepoFile | RepoFolder]:
        self.calls.append(
            {
                "repo_id": repo_id,
                "recursive": recursive,
                "revision": revision,
                "repo_type": repo_type,
                "token": token,
            }
        )
        return self.items


def test_discovery_recursively_lists_files_at_explicit_commit() -> None:
    revision = "a" * 40
    api = FakeTreeApi(
        [
            RepoFolder(path="nested", oid="folder-oid"),  # type: ignore[no-untyped-call]
            RepoFile(  # type: ignore[no-untyped-call]
                path="nested/weights.bin", size=20, oid="weight-oid"
            ),
            RepoFile(  # type: ignore[no-untyped-call]
                path="config.json", size=10, oid="config-oid"
            ),
        ]
    )

    result = discover_repository_tree(
        "owner/model", revision, token="hf_discovery_secret", api=api
    )

    assert api.info_calls == [
        {
            "repo_id": "owner/model",
            "revision": revision,
            "files_metadata": False,
            "token": False,
        }
    ]
    assert api.calls == [
        {
            "repo_id": "owner/model",
            "recursive": True,
            "revision": revision,
            "repo_type": "model",
            "token": False,
        }
    ]
    assert result.repository == "owner/model"
    assert result.revision == revision
    assert [item.path for item in result.files] == ["config.json", "nested/weights.bin"]
    assert [item.storage for item in result.files] == ["git", "git"]


def test_discovery_requires_full_commit_before_calling_api() -> None:
    api = FakeTreeApi([])

    try:
        discover_repository_tree("owner/model", "main", token=None, api=api)
    except ValueError as error:
        assert "full lowercase commit" in str(error)
    else:
        raise AssertionError("symbolic revision was accepted")

    assert api.calls == []


def test_discovery_rejects_revision_resolving_to_different_commit() -> None:
    api = FakeTreeApi([], resolved_revision="b" * 40)

    with pytest.raises(RevisionResolutionError, match="did not resolve"):
        discover_repository_tree("owner/model", "a" * 40, token=None, api=api)

    assert api.calls == []


@pytest.mark.parametrize(
    ("upstream_error", "expected_error", "message"),
    [
        (GatedRepoError, GatedRepositoryAccessError, "gated repository access was denied"),
        (RepositoryNotFoundError, RepositoryUnavailableError, "repository does not exist"),
        (RevisionNotFoundError, RevisionResolutionError, "revision does not exist"),
    ],
)
def test_discovery_rejects_missing_revision_repository_or_gated_access(
    upstream_error: type[HfHubHTTPError],
    expected_error: type[Exception],
    message: str,
) -> None:
    token = "hf_upstream_failure_secret"
    request = httpx.Request("GET", "https://huggingface.test/api/models/owner/model")
    response = httpx.Response(404, request=request)
    failure = upstream_error(f"upstream included {token}", response=response)
    api = FakeTreeApi([], failure=failure)

    with pytest.raises(expected_error, match=message) as captured:
        discover_repository_tree("owner/model", "a" * 40, token=token, api=api)

    assert token not in str(captured.value)
    assert api.calls == []


def test_discovery_report_is_private_deterministic_and_separate_from_bundle(
    tmp_path: Path,
) -> None:
    output = tmp_path / "review"
    discovery = RepositoryDiscovery(
        repository="owner/model",
        revision="a" * 40,
        files=(
            DiscoveredFile(
                path="config.json",
                size=10,
                storage="git",
                blob_id="git-oid",
                lfs_sha256=None,
                xet_hash=None,
            ),
        ),
    )

    first = write_discovery_report("alpha", discovery, output, token="hf_not_present")
    first_bytes = first.read_bytes()
    second = write_discovery_report("alpha", discovery, output, token="hf_not_present")
    value = json.loads(second.read_text(encoding="utf-8"))

    assert first == second == output / "alpha.discovery.json"
    assert second.read_bytes() == first_bytes
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(second.stat().st_mode) == 0o600
    assert value["purpose"] == "selection_review_only"
    assert "manifest_version" not in value
    assert "hf_not_present" not in second.read_text(encoding="utf-8")


def test_discovery_report_rejects_secret_in_content(tmp_path: Path) -> None:
    token = "hf_report_secret"
    discovery = RepositoryDiscovery(repository=token, revision="a" * 40, files=())

    with pytest.raises(ValueError, match="discovery report") as failure:
        write_discovery_report("alpha", discovery, tmp_path / "review", token=token)

    assert token not in str(failure.value)


def _selection(
    *, include: tuple[str, ...] = ("config.json",), exclude: tuple[str, ...] = ("README.md",)
) -> ModelFileSelection:
    return ModelFileSelection(
        include=include,
        kinds={path: "config" for path in include},
        exclude=exclude,
    )


def _discovery(*files: DiscoveredFile) -> RepositoryDiscovery:
    return RepositoryDiscovery(repository="owner/model", revision="a" * 40, files=files)


def test_selection_exactly_partitions_discovered_tree() -> None:
    config = DiscoveredFile("config.json", 10, "git", "config-oid", None, None)
    readme = DiscoveredFile("README.md", 20, "git", "readme-oid", None, None)

    assert validate_selection_against_discovery(_selection(), _discovery(readme, config)) == (
        config,
    )


@pytest.mark.parametrize(
    ("selection", "files", "match"),
    [
        (_selection(), (), "selection paths are missing"),
        (
            _selection(exclude=()),
            (
                DiscoveredFile("config.json", 10, "git", "config-oid", None, None),
                DiscoveredFile("README.md", 20, "git", "readme-oid", None, None),
            ),
            "unclassified extra paths",
        ),
        (
            _selection(exclude=()),
            (DiscoveredFile("config.json", -1, "git", "config-oid", None, None),),
            "sizes cannot be confirmed",
        ),
    ],
)
def test_selection_rejects_missing_extra_or_unknown_size(
    selection: ModelFileSelection,
    files: tuple[DiscoveredFile, ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        validate_selection_against_discovery(selection, _discovery(*files))
