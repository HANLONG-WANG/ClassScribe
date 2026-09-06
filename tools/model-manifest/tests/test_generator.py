from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from huggingface_hub.hf_api import RepoFile, RepoFolder
from jsonschema import Draft202012Validator

from classscribe_manifest_tool.discovery import (
    DiscoveredFile,
    RepositoryDiscovery,
    discover_repository_tree,
    validate_selection_against_discovery,
)
from classscribe_manifest_tool.generator import (
    ClassifiedPayloadFile,
    GenerationMismatchError,
    GenerationRun,
    HashedPayloadFile,
    ManifestMetadata,
    _classify_payload_entry,
    build_model_manifest,
    canonical_bundle_bytes,
    canonical_manifest_bytes,
    classify_payload_files,
    download_selected_snapshot,
    generate_release_bundle,
    generation_workspace,
    hash_payload_files,
    read_source_date_epoch,
    remove_huggingface_metadata,
    require_identical_generation_runs,
    scan_payload,
    validate_component_source_discovery,
    validate_double_generation_outputs,
    validate_double_generation_payloads,
    validate_double_generation_revisions,
    validate_payload_matches_selection,
    validate_weight_index_closure,
)
from classscribe_manifest_tool.inputs import (
    ReleaseComponentSource,
    ReleaseComponentSourceFile,
    ReleaseInputs,
    ReleaseModel,
    load_release_inputs,
)
from classscribe_manifest_tool.selection import (
    FileSelectionIndex,
    ModelFileSelection,
)

ROOT = Path(__file__).resolve().parents[3]


class _GenerationApi:
    def __init__(self, items: Iterable[RepoFile | RepoFolder] | None = None) -> None:
        self.items = tuple(items) if items is not None else (
            RepoFile(  # type: ignore[no-untyped-call]
                path="config.json", size=6, oid="config-oid"
            ),
        )

    def model_info(
        self,
        repo_id: str,
        *,
        revision: str,
        files_metadata: bool,
        token: bool,
    ) -> object:
        del repo_id, files_metadata, token
        return type("ModelInfo", (), {"sha": revision})()

    def list_repo_tree(
        self,
        repo_id: str,
        *,
        recursive: bool,
        revision: str,
        repo_type: str,
        token: bool,
    ) -> Iterable[RepoFile | RepoFolder]:
        del repo_id, recursive, revision, repo_type, token
        return self.items


def test_generation_workspace_uses_private_system_temp_not_runtime_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg_data = tmp_path / "xdg-data"
    hf_home = tmp_path / "hf-home"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("HF_HOME", str(hf_home))
    token = "hf_workspace_secret"

    with generation_workspace(token=token) as workspace:
        captured = workspace
        assert workspace.parent == Path(tempfile.gettempdir())
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
        assert token not in str(workspace)
        assert not workspace.is_relative_to(xdg_data)
        assert not workspace.is_relative_to(hf_home)

    assert not captured.exists()


def test_snapshot_download_uses_commit_exact_allowlist_and_preserves_layout(
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    selection = ModelFileSelection(
        include=("config.json", "nested/weights.bin"),
        kinds={"config.json": "config", "nested/weights.bin": "model"},
        exclude=(),
    )
    calls: list[tuple[str, Mapping[str, object]]] = []

    def fake_download(repo_id: str, **kwargs: Any) -> str:
        calls.append((repo_id, kwargs))
        local_dir = Path(kwargs["local_dir"])
        for relative in kwargs["allow_patterns"]:
            target = local_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(relative.encode())
        return str(local_dir)

    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    payload = download_selected_snapshot(
        "owner/model",
        revision,
        selection,
        workspace,
        token="hf_download_secret",
        downloader=fake_download,
    )

    assert (payload / "config.json").read_bytes() == b"config.json"
    assert (payload / "nested/weights.bin").read_bytes() == b"nested/weights.bin"
    assert len(calls) == 1
    repository, arguments = calls[0]
    assert repository == "owner/model"
    assert arguments["revision"] == revision
    assert arguments["repo_type"] == "model"
    assert arguments["allow_patterns"] == ["config.json", "nested/weights.bin"]
    assert arguments["local_dir"] == workspace / "payload"
    assert arguments["cache_dir"] == workspace / "hub-cache"
    assert arguments["force_download"] is True
    assert arguments["local_files_only"] is False
    assert arguments["token"] is False
    assert arguments["headers"] == {"Authorization": "Bearer hf_download_secret"}


def test_huggingface_local_dir_metadata_is_removed_without_touching_payload(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    metadata = payload / ".cache/huggingface/download"
    metadata.mkdir(parents=True)
    (metadata / "config.json.metadata").write_text("metadata", encoding="utf-8")
    model = payload / "nested/weights.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"weights")

    remove_huggingface_metadata(payload)

    assert not (payload / ".cache").exists()
    assert model.read_bytes() == b"weights"


def test_huggingface_metadata_cleanup_preserves_unrelated_cache_entries(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    (payload / ".cache/huggingface").mkdir(parents=True)
    other = payload / ".cache/other"
    other.write_text("unexpected", encoding="utf-8")

    remove_huggingface_metadata(payload)

    assert other.read_text(encoding="utf-8") == "unexpected"
    assert not (payload / ".cache/huggingface").exists()


def test_huggingface_metadata_cleanup_rejects_symlink(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep"
    marker.write_text("keep", encoding="utf-8")
    (payload / ".cache").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        remove_huggingface_metadata(payload)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_payload_scanner_returns_sorted_regular_files_with_confirmed_limits(
    tmp_path: Path,
) -> None:
    payload = (tmp_path / "payload").resolve()
    nested = payload / "nested"
    nested.mkdir(parents=True)
    (nested / "weights.bin").write_bytes(b"weights")
    (payload / "config.json").write_bytes(b"config")

    files = scan_payload(
        payload,
        expected_sizes={"config.json": 6, "nested/weights.bin": 7},
    )

    assert [(item.path, item.size) for item in files] == [
        ("config.json", 6),
        ("nested/weights.bin", 7),
    ]


def test_payload_hashing_streams_actual_bytes_and_handles_empty_file(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    content = b"streamed payload bytes"
    (payload / "data.bin").write_bytes(content)
    (payload / "empty.bin").write_bytes(b"")
    scanned = scan_payload(
        payload,
        expected_sizes={"data.bin": len(content), "empty.bin": 0},
    )

    hashed = hash_payload_files(payload, scanned, chunk_size=3)

    assert [(item.path, item.size_bytes, item.sha256) for item in hashed] == [
        ("data.bin", len(content), hashlib.sha256(content).hexdigest()),
        ("empty.bin", 0, hashlib.sha256(b"").hexdigest()),
    ]


def test_git_lfs_and_xet_payloads_all_hash_downloaded_bytes(tmp_path: Path) -> None:
    revision = "a" * 40
    contents = {
        "git.bin": b"ordinary git bytes",
        "lfs.bin": b"resolved lfs bytes",
        "xet.bin": b"resolved xet bytes",
    }
    api = _GenerationApi(
        (
            RepoFile(  # type: ignore[no-untyped-call]
                path="git.bin", size=len(contents["git.bin"]), oid="git-blob-oid"
            ),
            RepoFile(  # type: ignore[no-untyped-call]
                path="lfs.bin",
                size=len(contents["lfs.bin"]),
                oid="lfs-pointer-git-oid",
                lfs={
                    "oid": "f" * 64,
                    "size": len(contents["lfs.bin"]),
                    "pointerSize": 128,
                },
            ),
            RepoFile(  # type: ignore[no-untyped-call]
                path="xet.bin",
                size=len(contents["xet.bin"]),
                oid="xet-pointer-git-oid",
                xetHash="upstream-xet-hash",
            ),
        )
    )
    discovery = discover_repository_tree(
        "owner/model", revision, token=None, api=api
    )
    selection = ModelFileSelection(
        include=("git.bin", "lfs.bin", "xet.bin"),
        kinds=MappingProxyType(
            {"git.bin": "model", "lfs.bin": "model", "xet.bin": "model"}
        ),
        exclude=(),
    )
    selected = validate_selection_against_discovery(selection, discovery)
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    for path, content in contents.items():
        (payload / path).write_bytes(content)

    hashed = hash_payload_files(
        payload,
        scan_payload(payload, expected_sizes={item.path: item.size for item in selected}),
    )

    assert [item.storage for item in selected] == ["git", "lfs", "xet"]
    assert {item.path: item.sha256 for item in hashed} == {
        path: hashlib.sha256(content).hexdigest() for path, content in contents.items()
    }
    assert next(item for item in selected if item.path == "lfs.bin").lfs_sha256 == "f" * 64
    assert next(item for item in selected if item.path == "xet.bin").xet_hash == (
        "upstream-xet-hash"
    )
    assert all(item.sha256 != "f" * 64 for item in hashed)


def test_payload_hashing_rejects_file_changed_after_scan(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    target = payload / "data.bin"
    target.write_bytes(b"first")
    scanned = scan_payload(payload, expected_sizes={"data.bin": 10})
    target.write_bytes(b"changed")

    with pytest.raises(ValueError, match="changed after scanning"):
        hash_payload_files(payload, scanned)


def test_payload_hashing_rejects_symlink_replacement_after_scan(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    target = payload / "data.bin"
    target.write_bytes(b"first")
    scanned = scan_payload(payload, expected_sizes={"data.bin": 10})
    target.unlink()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    target.symlink_to(outside)

    with pytest.raises(ValueError, match="cannot be safely opened"):
        hash_payload_files(payload, scanned)

    assert outside.read_bytes() == b"outside"


def test_weight_index_requires_all_referenced_shards_in_selection(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    shard = "model-00001-of-00001.safetensors"
    index = "model.safetensors.index.json"
    index_content = json.dumps(
        {"metadata": {}, "weight_map": {"encoder.weight": shard}}, sort_keys=True
    ).encode()
    (payload / index).write_bytes(index_content)
    (payload / shard).write_bytes(b"weights")
    selection = ModelFileSelection(
        include=(shard, index),
        kinds=MappingProxyType({shard: "model", index: "model"}),
        exclude=(),
    )
    scanned = scan_payload(
        payload,
        expected_sizes={index: len(index_content), shard: len(b"weights")},
    )

    validate_weight_index_closure(payload, scanned, selection)


def test_nested_weight_index_resolves_shards_relative_to_its_directory(
    tmp_path: Path,
) -> None:
    payload = (tmp_path / "payload").resolve()
    model_directory = payload / "nested-model"
    model_directory.mkdir(parents=True)
    shard = "nested-model/model-00001-of-00001.safetensors"
    index = "nested-model/model.safetensors.index.json"
    index_content = json.dumps(
        {
            "metadata": {},
            "weight_map": {
                "encoder.weight": "model-00001-of-00001.safetensors"
            },
        },
        sort_keys=True,
    ).encode()
    (payload / index).write_bytes(index_content)
    (payload / shard).write_bytes(b"weights")
    selection = ModelFileSelection(
        include=(shard, index),
        kinds=MappingProxyType({shard: "model", index: "model"}),
        exclude=(),
    )
    scanned = scan_payload(
        payload,
        expected_sizes={index: len(index_content), shard: len(b"weights")},
    )

    validate_weight_index_closure(payload, scanned, selection)


def test_nested_weight_index_rejects_parent_escape(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    model_directory = payload / "nested-model"
    model_directory.mkdir(parents=True)
    index = "nested-model/model.safetensors.index.json"
    index_content = json.dumps(
        {"weight_map": {"encoder.weight": "../outside.safetensors"}},
        sort_keys=True,
    ).encode()
    (payload / index).write_bytes(index_content)
    selection = ModelFileSelection(
        include=(index,),
        kinds=MappingProxyType({index: "model"}),
        exclude=(),
    )
    scanned = scan_payload(payload, expected_sizes={index: len(index_content)})

    with pytest.raises(ValueError, match="unsafe or non-exact"):
        validate_weight_index_closure(payload, scanned, selection)


def test_nested_weight_index_does_not_match_a_root_level_shard(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    model_directory = payload / "nested-model"
    model_directory.mkdir(parents=True)
    shard = "model-00001-of-00001.safetensors"
    index = "nested-model/model.safetensors.index.json"
    index_content = json.dumps(
        {"weight_map": {"encoder.weight": shard}},
        sort_keys=True,
    ).encode()
    (payload / index).write_bytes(index_content)
    (payload / shard).write_bytes(b"wrong directory")
    selection = ModelFileSelection(
        include=(shard, index),
        kinds=MappingProxyType({shard: "model", index: "model"}),
        exclude=(),
    )
    scanned = scan_payload(
        payload,
        expected_sizes={index: len(index_content), shard: len(b"wrong directory")},
    )

    with pytest.raises(ValueError, match="nested-model/model-00001"):
        validate_weight_index_closure(payload, scanned, selection)


def test_weight_index_rejects_a_referenced_shard_excluded_from_selection(
    tmp_path: Path,
) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    shard = "model-00001-of-00001.safetensors"
    index = "model.safetensors.index.json"
    index_content = json.dumps(
        {"weight_map": {"encoder.weight": shard}}, sort_keys=True
    ).encode()
    (payload / index).write_bytes(index_content)
    selection = ModelFileSelection(
        include=(index,),
        kinds=MappingProxyType({index: "model"}),
        exclude=(shard,),
    )
    scanned = scan_payload(payload, expected_sizes={index: len(index_content)})

    with pytest.raises(ValueError, match="shards outside selection"):
        validate_weight_index_closure(payload, scanned, selection)


def test_weight_index_rejects_unsafe_or_non_model_shard_references(
    tmp_path: Path,
) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    index = "model.safetensors.index.json"
    unsafe_content = json.dumps({"weight_map": {"weight": "../escape.bin"}}).encode()
    (payload / index).write_bytes(unsafe_content)
    selection = ModelFileSelection(
        include=(index,),
        kinds=MappingProxyType({index: "model"}),
        exclude=(),
    )
    scanned = scan_payload(payload, expected_sizes={index: len(unsafe_content)})
    with pytest.raises(ValueError, match="unsafe or non-exact"):
        validate_weight_index_closure(payload, scanned, selection)

    shard = "weights.safetensors"
    classified_content = json.dumps({"weight_map": {"weight": shard}}).encode()
    (payload / index).write_bytes(classified_content)
    (payload / shard).write_bytes(b"weights")
    selection = ModelFileSelection(
        include=(index, shard),
        kinds=MappingProxyType({index: "model", shard: "other"}),
        exclude=(),
    )
    scanned = scan_payload(
        payload,
        expected_sizes={index: len(classified_content), shard: len(b"weights")},
    )
    with pytest.raises(ValueError, match="classified as model"):
        validate_weight_index_closure(payload, scanned, selection)


def test_payload_scanner_rejects_symlink_without_following_it(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (payload / "weights.bin").symlink_to(outside)

    with pytest.raises(ValueError, match="contains a symlink"):
        scan_payload(payload, expected_sizes={"weights.bin": 7})

    assert outside.read_bytes() == b"outside"


def test_payload_scanner_rejects_hardlink(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    first = payload / "first.bin"
    first.write_bytes(b"same inode")
    os.link(first, payload / "second.bin")

    with pytest.raises(ValueError, match="contains a hardlink"):
        scan_payload(payload, expected_sizes={"first.bin": 10, "second.bin": 10})


def test_payload_scanner_rejects_fifo(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    fifo = payload / "special"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="contains a special file"):
        scan_payload(payload, expected_sizes={"special": 0})


@pytest.mark.parametrize(
    "file_type",
    [stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK],
    ids=["socket", "character-device", "block-device"],
)
def test_payload_entry_classifier_rejects_socket_and_devices(file_type: int) -> None:
    metadata = os.stat_result((file_type | 0o600, 1, 1, 1, 0, 0, 0, 0, 0, 0))

    with pytest.raises(ValueError, match="contains a special file"):
        _classify_payload_entry(metadata, "special")


def test_payload_scanner_rejects_noncanonical_path_and_size_overrun(tmp_path: Path) -> None:
    payload = (tmp_path / "payload").resolve()
    payload.mkdir()
    (payload / "weights.bin").write_bytes(b"too large")

    with pytest.raises(ValueError, match="absolute canonical"):
        scan_payload(payload / "nested/..", expected_sizes={"weights.bin": 9})
    with pytest.raises(ValueError, match="exceeds declared size"):
        scan_payload(payload, expected_sizes={"weights.bin": 1})


def test_hashed_payload_must_exactly_match_selection_and_discovery() -> None:
    selection = ModelFileSelection(
        include=("config.json", "weights.bin"),
        kinds={"config.json": "config", "weights.bin": "model"},
        exclude=(),
    )
    discovered = (
        DiscoveredFile("config.json", 6, "git", "config-oid", None, None),
        DiscoveredFile("weights.bin", 7, "lfs", "pointer-oid", "1" * 64, None),
    )
    config = HashedPayloadFile("config.json", 6, "a" * 64)
    weights = HashedPayloadFile("weights.bin", 7, "b" * 64)

    assert validate_payload_matches_selection(
        selection, discovered, (config, weights)
    ) == (config, weights)


@pytest.mark.parametrize(
    ("actual", "match"),
    [
        ((HashedPayloadFile("config.json", 6, "a" * 64),), "missing selected paths"),
        (
            (
                HashedPayloadFile("config.json", 6, "a" * 64),
                HashedPayloadFile("weights.bin", 7, "b" * 64),
                HashedPayloadFile("unexpected.txt", 1, "c" * 64),
            ),
            "contains extra paths",
        ),
        (
            (
                HashedPayloadFile("config.json", 5, "a" * 64),
                HashedPayloadFile("weights.bin", 7, "b" * 64),
            ),
            "sizes differ from discovery",
        ),
    ],
)
def test_hashed_payload_rejects_missing_extra_and_short_write(
    actual: tuple[HashedPayloadFile, ...], match: str
) -> None:
    selection = ModelFileSelection(
        include=("config.json", "weights.bin"),
        kinds={"config.json": "config", "weights.bin": "model"},
        exclude=(),
    )
    discovered = (
        DiscoveredFile("config.json", 6, "git", "config-oid", None, None),
        DiscoveredFile("weights.bin", 7, "lfs", "pointer-oid", "1" * 64, None),
    )

    with pytest.raises(ValueError, match=match):
        validate_payload_matches_selection(selection, discovered, actual)


def test_payload_kinds_are_taken_only_from_explicit_selection() -> None:
    selection = ModelFileSelection(
        include=("config.json", "modeling.py"),
        kinds={"config.json": "config", "modeling.py": "remote_code"},
        exclude=(),
    )
    files = (
        HashedPayloadFile("config.json", 6, "a" * 64),
        HashedPayloadFile("modeling.py", 7, "b" * 64),
    )

    classified = classify_payload_files(selection, files)

    assert [(item.path, item.kind) for item in classified] == [
        ("config.json", "config"),
        ("modeling.py", "remote_code"),
    ]


def test_payload_kind_must_not_default_when_selection_is_unclassified() -> None:
    selection = ModelFileSelection(
        include=("weights.bin",),
        kinds={},
        exclude=(),
    )
    files = (HashedPayloadFile("weights.bin", 7, "b" * 64),)

    with pytest.raises(ValueError, match="explicitly classify"):
        classify_payload_files(selection, files)


def _manifest_metadata(*, trust_remote_code: bool) -> ManifestMetadata:
    return ManifestMetadata(
        model_id="alpha",
        repository="owner/model",
        revision="a" * 40,
        worker="worker",
        license_id="Apache-2.0",
        license_url="https://huggingface.co/owner/model",
        requires_terms_acceptance=False,
        trust_remote_code=trust_remote_code,
        environment={
            "python": "3.12",
            "runtime_backend": "transformers_custom_code",
            "dtype": "float32",
            "worker_lock": "workers/worker/uv.lock",
            "worker_lock_sha256": "b" * 64,
        },
    )


def test_manifest_is_sorted_sized_and_canonically_serialized() -> None:
    files = (
        ClassifiedPayloadFile("config.json", 6, "c" * 64, "config"),
        ClassifiedPayloadFile("modeling.py", 7, "d" * 64, "remote_code"),
    )
    manifest = build_model_manifest(_manifest_metadata(trust_remote_code=True), files)

    encoded = canonical_manifest_bytes(manifest, token="hf_not_in_manifest")
    value = json.loads(encoded)

    assert manifest.installed_size_bytes == 13
    assert value["estimated_download_bytes"] == 13
    assert value["installed_size_bytes"] == 13
    assert [item["path"] for item in value["files"]] == ["config.json", "modeling.py"]
    assert encoded.endswith(b"\n") and not encoded.endswith(b"\n\n")
    assert b"\r" not in encoded
    assert encoded == canonical_manifest_bytes(manifest, token=None)
    assert "hf_not_in_manifest" not in encoded.decode()
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(value)


def test_manifest_serializes_strict_component_source_provenance() -> None:
    payload = b"derived payload"
    source = ReleaseComponentSource(
        repository="upstream/component",
        revision="e" * 40,
        relationship="derived",
        license_id="CC-BY-4.0",
        license_url="https://huggingface.co/upstream/component",
        requires_terms_acceptance=False,
        files=(
            ReleaseComponentSourceFile(
                installed_path="weights.bin",
                source_path="pytorch_model.bin",
                source_sha256="f" * 64,
                source_size_bytes=17,
            ),
        ),
    )
    files = (
        ClassifiedPayloadFile(
            "weights.bin", len(payload), hashlib.sha256(payload).hexdigest(), "model"
        ),
    )
    metadata = replace(
        _manifest_metadata(trust_remote_code=False), component_sources=(source,)
    )

    encoded = canonical_manifest_bytes(build_model_manifest(metadata, files), token=None)
    value = json.loads(encoded)

    assert value["component_sources"] == [
        {
            "repository": "upstream/component",
            "revision": "e" * 40,
            "relationship": "derived",
            "license_id": "CC-BY-4.0",
            "license_url": "https://huggingface.co/upstream/component",
            "requires_terms_acceptance": False,
            "files": [
                {
                    "installed_path": "weights.bin",
                    "source_path": "pytorch_model.bin",
                    "source_sha256": "f" * 64,
                    "source_size_bytes": 17,
                }
            ],
        }
    ]
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(value)
    copied = replace(source, relationship="copied")
    with pytest.raises(ValueError, match="differs from fixed source"):
        build_model_manifest(replace(metadata, component_sources=(copied,)), files)


def test_component_source_discovery_requires_fixed_lfs_identity() -> None:
    source = ReleaseComponentSource(
        repository="upstream/component",
        revision="e" * 40,
        relationship="derived",
        license_id="CC-BY-4.0",
        license_url="https://huggingface.co/upstream/component",
        requires_terms_acceptance=False,
        files=(
            ReleaseComponentSourceFile(
                installed_path="weights.bin",
                source_path="pytorch_model.bin",
                source_sha256="f" * 64,
                source_size_bytes=17,
            ),
        ),
    )
    valid_file = DiscoveredFile(
        path="pytorch_model.bin",
        size=17,
        storage="lfs",
        blob_id="pointer",
        lfs_sha256="f" * 64,
        xet_hash=None,
    )
    valid = RepositoryDiscovery(source.repository, source.revision, (valid_file,))

    validate_component_source_discovery(source, valid)
    with pytest.raises(ValueError, match="frozen identity"):
        validate_component_source_discovery(
            source, replace(valid, revision="a" * 40)
        )
    with pytest.raises(ValueError, match="size differs"):
        validate_component_source_discovery(
            source, replace(valid, files=(replace(valid_file, size=18),))
        )
    with pytest.raises(ValueError, match="LFS SHA-256 differs"):
        validate_component_source_discovery(
            source, replace(valid, files=(replace(valid_file, lfs_sha256="a" * 64),))
        )


def test_whisper_tiny_manifest_is_canonical_and_matches_frozen_inputs() -> None:
    manifest_path = (
        ROOT / "config/model-manifests/v1/whisper_tiny_reference.json"
    )
    encoded = manifest_path.read_bytes()
    value = json.loads(encoded)
    canonical = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    assert encoded == canonical
    assert hashlib.sha256(encoded).hexdigest() == (
        "2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8"
    )
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(value)

    inputs = load_release_inputs(
        registry_path=ROOT / "config/model-registry.v1.yaml",
        revisions_path=ROOT / "config/model-revisions.lock.json",
        licenses_path=ROOT / "config/model-licenses.v1.json",
        selection_path=ROOT / "config/model-file-selection.v1.yaml",
        require_complete_selection=False,
    )
    model = next(
        item for item in inputs.models if item.model_id == "whisper_tiny_reference"
    )
    selection = inputs.selections.selection(model.model_id)
    assert {
        "model_id": value["model_id"],
        "repository": value["repository"],
        "revision": value["revision"],
        "worker": value["worker"],
        "license_id": value["license_id"],
        "license_url": value["license_url"],
        "requires_terms_acceptance": value["requires_terms_acceptance"],
        "trust_remote_code": value["trust_remote_code"],
    } == {
        "model_id": model.model_id,
        "repository": model.repository,
        "revision": model.revision,
        "worker": model.worker,
        "license_id": model.license_id,
        "license_url": model.license_url,
        "requires_terms_acceptance": model.requires_terms_acceptance,
        "trust_remote_code": model.trust_remote_code,
    }
    assert value["environment"] == {
        "dtype": model.dtype,
        "python": "3.12",
        "runtime_backend": model.runtime_backend,
        "worker_lock": model.dependency_lock,
        "worker_lock_sha256": model.dependency_lock_sha256,
    }
    files = value["files"]
    assert [item["path"] for item in files] == list(selection.include)
    assert {item["path"]: item["kind"] for item in files} == dict(selection.kinds)
    assert value["installed_size_bytes"] == value["estimated_download_bytes"] == sum(
        item["size_bytes"] for item in files
    )


def test_manifest_requires_remote_code_and_trust_policy_to_match() -> None:
    files = (ClassifiedPayloadFile("modeling.py", 7, "d" * 64, "remote_code"),)

    with pytest.raises(ValueError, match="trust policy must match"):
        build_model_manifest(_manifest_metadata(trust_remote_code=False), files)


def test_manifest_requires_complete_environment_and_sorted_unique_files() -> None:
    incomplete = _manifest_metadata(trust_remote_code=False)
    incomplete = ManifestMetadata(
        model_id=incomplete.model_id,
        repository=incomplete.repository,
        revision=incomplete.revision,
        worker=incomplete.worker,
        license_id=incomplete.license_id,
        license_url=incomplete.license_url,
        requires_terms_acceptance=False,
        trust_remote_code=False,
        environment={"python": "3.12"},
    )
    model = ClassifiedPayloadFile("weights.bin", 7, "d" * 64, "model")

    with pytest.raises(ValueError, match="missing required"):
        build_model_manifest(incomplete, (model,))
    with pytest.raises(ValueError, match="Unicode-sorted"):
        build_model_manifest(
            _manifest_metadata(trust_remote_code=False),
            (model, ClassifiedPayloadFile("config.json", 6, "c" * 64, "config")),
        )


def test_double_generation_requires_both_runs_to_resolve_requested_commit() -> None:
    requested = "a" * 40
    first = RepositoryDiscovery("owner/model", requested, ())
    second = RepositoryDiscovery("owner/model", requested, ())

    validate_double_generation_revisions(requested, first, second)

    with pytest.raises(ValueError, match="different commits"):
        validate_double_generation_revisions(
            requested,
            first,
            RepositoryDiscovery("owner/model", "b" * 40, ()),
        )


def test_double_generation_requires_identical_payload_sizes_and_hashes() -> None:
    first = (
        ClassifiedPayloadFile("config.json", 6, "a" * 64, "config"),
        ClassifiedPayloadFile("weights.bin", 7, "b" * 64, "model"),
    )

    validate_double_generation_payloads(first, first)

    with pytest.raises(ValueError, match="file sets differ"):
        validate_double_generation_payloads(first, first[:1])
    with pytest.raises(ValueError, match="size or SHA-256 differs"):
        validate_double_generation_payloads(
            first,
            (
                first[0],
                ClassifiedPayloadFile("weights.bin", 7, "c" * 64, "model"),
            ),
        )


def test_source_date_epoch_controls_deterministic_bundle_bytes() -> None:
    epoch = read_source_date_epoch({"SOURCE_DATE_EPOCH": "1788451200"})
    first_manifests = {"beta": b"beta manifest\n", "alpha": b"alpha manifest\n"}
    second_manifests = {"alpha": b"alpha manifest\n", "beta": b"beta manifest\n"}

    first = canonical_bundle_bytes(
        registry_revision=1,
        facts_as_of="2026-09-03",
        manifests=first_manifests,
        generator_lock=b"frozen tool lock\n",
        source_date_epoch=epoch,
        token="hf_not_in_bundle",
    )
    second = canonical_bundle_bytes(
        registry_revision=1,
        facts_as_of="2026-09-03",
        manifests=second_manifests,
        generator_lock=b"frozen tool lock\n",
        source_date_epoch=epoch,
        token=None,
    )
    changed_epoch = canonical_bundle_bytes(
        registry_revision=1,
        facts_as_of="2026-09-03",
        manifests=first_manifests,
        generator_lock=b"frozen tool lock\n",
        source_date_epoch=epoch + 1,
        token=None,
    )

    validate_double_generation_outputs(first_manifests, second_manifests, first, second)
    assert first == second
    assert first != changed_epoch
    value = json.loads(first)
    assert [item["model_id"] for item in value["manifests"]] == ["alpha", "beta"]
    assert value["manifests"][0]["sha256"] == hashlib.sha256(
        b"alpha manifest\n"
    ).hexdigest()
    assert value["generator"]["lock_sha256"] == hashlib.sha256(
        b"frozen tool lock\n"
    ).hexdigest()
    schema = json.loads(
        (ROOT / "protocol/schema/v1/model-manifest-bundle.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(value)


def test_source_date_epoch_is_required_and_output_byte_drift_fails() -> None:
    with pytest.raises(ValueError, match="must be an explicit"):
        read_source_date_epoch({})
    with pytest.raises(ValueError, match="manifest bytes differ"):
        validate_double_generation_outputs(
            {"alpha": b"first\n"},
            {"alpha": b"second\n"},
            b"bundle\n",
            b"bundle\n",
        )
    with pytest.raises(ValueError, match="bundle bytes differ"):
        validate_double_generation_outputs(
            {"alpha": b"same\n"},
            {"alpha": b"same\n"},
            b"first bundle\n",
            b"second bundle\n",
        )


def test_generation_mismatch_stops_with_path_and_hash_only_report() -> None:
    first = GenerationRun(
        revision="a" * 40,
        payload=(ClassifiedPayloadFile("weights.bin", 7, "b" * 64, "model"),),
        manifests={"alpha": b"first manifest\n"},
        bundle=b"first bundle\n",
    )
    second = GenerationRun(
        revision="a" * 40,
        payload=(ClassifiedPayloadFile("weights.bin", 7, "c" * 64, "model"),),
        manifests={},
        bundle=b"second bundle\n",
    )

    with pytest.raises(GenerationMismatchError) as failure:
        require_identical_generation_runs("a" * 40, first, second)

    report = json.loads(failure.value.report)
    assert report == {
        "differences": [
            {
                "first_sha256": hashlib.sha256(b"first bundle\n").hexdigest(),
                "path": "bundle.v1.json",
                "second_sha256": hashlib.sha256(b"second bundle\n").hexdigest(),
            },
            {
                "first_sha256": hashlib.sha256(b"first manifest\n").hexdigest(),
                "path": "manifests/alpha.json",
                "second_sha256": None,
            },
            {
                "first_sha256": "b" * 64,
                "path": "payload/weights.bin",
                "second_sha256": "c" * 64,
            },
        ]
    }
    assert set(report) == {"differences"}
    assert all(
        set(item) == {"path", "first_sha256", "second_sha256"}
        for item in report["differences"]
    )


def test_identical_generation_runs_are_publishable() -> None:
    run = GenerationRun(
        revision="a" * 40,
        payload=(ClassifiedPayloadFile("weights.bin", 7, "b" * 64, "model"),),
        manifests={"alpha": b"manifest\n"},
        bundle=b"bundle\n",
    )

    require_identical_generation_runs("a" * 40, run, run)


def test_generation_revision_drift_also_uses_hash_only_report() -> None:
    first = GenerationRun("b" * 40, (), {}, b"bundle\n")
    second = GenerationRun("b" * 40, (), {}, b"bundle\n")

    with pytest.raises(GenerationMismatchError) as failure:
        require_identical_generation_runs("a" * 40, first, second)

    report = json.loads(failure.value.report)
    assert [item["path"] for item in report["differences"]] == [
        "resolved-revision/first",
        "resolved-revision/second",
    ]
    assert "a" * 40 not in str(failure.value)
    assert "b" * 40 not in str(failure.value)


def test_release_bundle_orchestration_generates_twice_from_clean_workspaces(
    tmp_path: Path,
) -> None:
    token = "hf_complete_generation_secret"
    tool_lock = tmp_path / "tools/model-manifest/uv.lock"
    tool_lock.parent.mkdir(parents=True)
    tool_lock.write_text("version = 1\n", encoding="utf-8")
    selection = ModelFileSelection(
        include=("config.json",),
        kinds=MappingProxyType({"config.json": "config"}),
        exclude=(),
    )
    inputs = ReleaseInputs(
        root=tmp_path,
        registry_revision=1,
        facts_as_of="2026-09-03",
        models=(
            ReleaseModel(
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
            ),
        ),
        selections=FileSelectionIndex(
            schema_version=1,
            models=MappingProxyType({"alpha": selection}),
        ),
    )
    workspaces: list[Path] = []

    def fake_download(repo_id: str, **kwargs: Any) -> str:
        assert repo_id == "owner/model"
        assert kwargs["headers"] == {"Authorization": f"Bearer {token}"}
        local_dir = Path(kwargs["local_dir"])
        workspaces.append(local_dir.parent)
        (local_dir / "config.json").write_bytes(b"config")
        metadata = local_dir / ".cache/huggingface"
        metadata.mkdir(parents=True)
        (metadata / "download.json").write_text("metadata", encoding="utf-8")
        return str(local_dir)

    manifests, bundle = generate_release_bundle(
        inputs,
        source_date_epoch=1788451200,
        token=token,
        accepted_repositories=(),
        api_factory=_GenerationApi,
        downloader=fake_download,
    )
    repeated_manifests, repeated_bundle = generate_release_bundle(
        inputs,
        source_date_epoch=1788451200,
        token=token,
        accepted_repositories=(),
        api_factory=_GenerationApi,
        downloader=fake_download,
    )
    changed_epoch_manifests, changed_epoch_bundle = generate_release_bundle(
        inputs,
        source_date_epoch=1788451201,
        token=token,
        accepted_repositories=(),
        api_factory=_GenerationApi,
        downloader=fake_download,
    )

    assert len(workspaces) == 6
    assert len(set(workspaces)) == 6
    assert all(not workspace.exists() for workspace in workspaces)
    assert set(manifests) == {"alpha"}
    assert manifests == repeated_manifests == changed_epoch_manifests
    assert bundle == repeated_bundle
    assert bundle != changed_epoch_bundle
    assert token.encode() not in b"".join(manifests.values())
    assert token.encode() not in bundle
    manifest = json.loads(manifests["alpha"])
    assert manifest["files"] == [
        {
            "kind": "config",
            "path": "config.json",
            "sha256": hashlib.sha256(b"config").hexdigest(),
            "size_bytes": 6,
        }
    ]
    bundle_value = json.loads(bundle)
    assert bundle_value["generated_at"] == "2026-09-03T16:00:00Z"
    assert bundle_value["manifests"] == [
        {
            "model_id": "alpha",
            "path": "alpha.json",
            "sha256": hashlib.sha256(manifests["alpha"]).hexdigest(),
        }
    ]


def test_release_bundle_validates_component_source_on_both_clean_runs(
    tmp_path: Path,
) -> None:
    tool_lock = tmp_path / "tools/model-manifest/uv.lock"
    tool_lock.parent.mkdir(parents=True)
    tool_lock.write_text("version = 1\n", encoding="utf-8")
    source = ReleaseComponentSource(
        repository="upstream/component",
        revision="e" * 40,
        relationship="derived",
        license_id="CC-BY-4.0",
        license_url="https://huggingface.co/upstream/component",
        requires_terms_acceptance=False,
        files=(
            ReleaseComponentSourceFile(
                installed_path="config.json",
                source_path="pytorch_model.bin",
                source_sha256="f" * 64,
                source_size_bytes=17,
            ),
        ),
    )
    selection = ModelFileSelection(
        include=("config.json",),
        kinds=MappingProxyType({"config.json": "config"}),
        exclude=(),
    )
    inputs = ReleaseInputs(
        root=tmp_path,
        registry_revision=1,
        facts_as_of="2026-09-03",
        models=(
            ReleaseModel(
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
                component_sources=(source,),
            ),
        ),
        selections=FileSelectionIndex(1, MappingProxyType({"alpha": selection})),
    )
    api_calls: list[tuple[str, str]] = []

    class ComponentApi(_GenerationApi):
        def model_info(
            self,
            repo_id: str,
            *,
            revision: str,
            files_metadata: bool,
            token: bool,
        ) -> object:
            api_calls.append((repo_id, revision))
            return super().model_info(
                repo_id,
                revision=revision,
                files_metadata=files_metadata,
                token=token,
            )

        def list_repo_tree(
            self,
            repo_id: str,
            *,
            recursive: bool,
            revision: str,
            repo_type: str,
            token: bool,
        ) -> Iterable[RepoFile | RepoFolder]:
            if repo_id == source.repository:
                return (
                    RepoFile(  # type: ignore[no-untyped-call]
                        path="pytorch_model.bin",
                        size=17,
                        oid="source-pointer",
                        lfs={"oid": "f" * 64, "size": 17, "pointerSize": 128},
                    ),
                )
            return super().list_repo_tree(
                repo_id,
                recursive=recursive,
                revision=revision,
                repo_type=repo_type,
                token=token,
            )

    def fake_download(_repo_id: str, **kwargs: Any) -> str:
        local_dir = Path(kwargs["local_dir"])
        (local_dir / "config.json").write_bytes(b"config")
        return str(local_dir)

    manifests, _bundle = generate_release_bundle(
        inputs,
        source_date_epoch=1788451200,
        token=None,
        accepted_repositories=(),
        api_factory=ComponentApi,
        downloader=fake_download,
    )

    assert api_calls == [
        ("owner/model", "a" * 40),
        ("upstream/component", "e" * 40),
        ("owner/model", "a" * 40),
        ("upstream/component", "e" * 40),
    ]
    assert json.loads(manifests["alpha"])["component_sources"][0]["repository"] == (
        "upstream/component"
    )


def test_release_bundle_orchestration_reports_payload_drift_by_path_and_hash(
    tmp_path: Path,
) -> None:
    token = "hf_drift_generation_secret"
    tool_lock = tmp_path / "tools/model-manifest/uv.lock"
    tool_lock.parent.mkdir(parents=True)
    tool_lock.write_text("version = 1\n", encoding="utf-8")
    selection = ModelFileSelection(
        include=("config.json",),
        kinds=MappingProxyType({"config.json": "config"}),
        exclude=(),
    )
    inputs = ReleaseInputs(
        root=tmp_path,
        registry_revision=1,
        facts_as_of="2026-09-03",
        models=(
            ReleaseModel(
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
            ),
        ),
        selections=FileSelectionIndex(1, MappingProxyType({"alpha": selection})),
    )
    contents = iter((b"config", b"change"))

    def drifting_download(_repo_id: str, **kwargs: Any) -> str:
        local_dir = Path(kwargs["local_dir"])
        (local_dir / "config.json").write_bytes(next(contents))
        return str(local_dir)

    with pytest.raises(GenerationMismatchError) as failure:
        generate_release_bundle(
            inputs,
            source_date_epoch=1788451200,
            token=token,
            accepted_repositories=(),
            api_factory=_GenerationApi,
            downloader=drifting_download,
        )

    report = json.loads(failure.value.report)
    assert token not in str(failure.value)
    assert token.encode() not in failure.value.report
    differences = {item["path"]: item for item in report["differences"]}
    assert set(differences) == {"manifests/alpha.json", "payload/config.json"}
    assert differences["payload/config.json"] == {
        "first_sha256": hashlib.sha256(b"config").hexdigest(),
        "path": "payload/config.json",
        "second_sha256": hashlib.sha256(b"change").hexdigest(),
    }
    manifest_difference = differences["manifests/alpha.json"]
    assert len(manifest_difference["first_sha256"]) == 64
    assert len(manifest_difference["second_sha256"]) == 64
    assert manifest_difference["first_sha256"] != manifest_difference["second_sha256"]
