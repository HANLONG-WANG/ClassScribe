from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from classscribe_manifest_tool import generator


def generation(text: str) -> tuple[dict[str, bytes], bytes]:
    members = {"a": json.dumps({"version": text}).encode()}
    index = json.dumps(
        {
            "manifests": [
                {
                    "model_id": "a",
                    "path": "a.json",
                    "sha256": hashlib.sha256(members["a"]).hexdigest(),
                }
            ]
        }
    ).encode()
    return members, index


@pytest.mark.parametrize("failed_member", ["a.json", "bundle.v1.json"])
def test_failed_generation_keeps_complete_old_bundle(
    tmp_path: Path,
    monkeypatch: Any,
    failed_member: str,
) -> None:
    output = tmp_path / "v1"
    old_members, old_index = generation("old")
    generator.write_release_bundle(output, old_members, old_index, token=None)
    new_members, new_index = generation("new")
    write = generator._atomic_write

    def fail(path: Path, content: bytes) -> None:
        if path.name == failed_member:
            raise OSError("simulated full disk")
        write(path, content)

    monkeypatch.setattr(generator, "_atomic_write", fail)
    with pytest.raises(OSError, match="full disk"):
        generator.write_release_bundle(output, new_members, new_index, token=None)
    assert (output / "bundle.v1.json").read_bytes() == old_index
    assert (output / "a.json").read_bytes() == old_members["a"]
    assert not list(tmp_path.glob(".bundle-staging-*"))


def test_atomic_directory_update_and_invalid_index_rejection(tmp_path: Path) -> None:
    output = tmp_path / "v1"
    old_members, old_index = generation("old")
    generator.write_release_bundle(output, old_members, old_index, token=None)
    previous_inode = output.stat().st_ino
    new_members, new_index = generation("new")
    generator.write_release_bundle(output, new_members, new_index, token=None)
    assert output.stat().st_ino != previous_inode
    assert (output / "bundle.v1.json").read_bytes() == new_index
    assert (output / "a.json").read_bytes() == new_members["a"]
    with pytest.raises(ValueError, match="member bytes"):
        generator.write_release_bundle(output, old_members, new_index, token=None)
    assert (output / "bundle.v1.json").read_bytes() == new_index
    assert not list(tmp_path.glob(".bundle-staging-*"))


def test_interrupted_staging_preserves_old_bundle(tmp_path: Path, monkeypatch: Any) -> None:
    output = tmp_path / "v1"
    old_members, old_index = generation("old")
    generator.write_release_bundle(output, old_members, old_index, token=None)

    def interrupt(_path: Path, _content: bytes) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(generator, "_atomic_write", interrupt)
    new_members, new_index = generation("new")
    with pytest.raises(KeyboardInterrupt):
        generator.write_release_bundle(output, new_members, new_index, token=None)
    assert (output / "bundle.v1.json").read_bytes() == old_index
    assert (output / "a.json").read_bytes() == old_members["a"]
    assert not list(tmp_path.glob(".bundle-staging-*"))
