"""Strict loader for human-curated, exact model payload selections."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal, cast

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

SELECTION_KINDS = frozenset({"model", "tokenizer", "config", "remote_code", "other"})
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
MAX_SELECTION_PATH_LENGTH = 1024
RESERVED_PATH = "supply-chain.json"
GLOB_CHARACTERS = frozenset("*?[]")

type SelectionKind = Literal["model", "tokenizer", "config", "remote_code", "other"]


@dataclass(frozen=True, slots=True)
class ModelFileSelection:
    """One model's exact, reviewed payload closure."""

    include: tuple[str, ...]
    kinds: Mapping[str, SelectionKind]
    exclude: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileSelectionIndex:
    """Immutable selection inventory loaded from the v1 YAML contract."""

    schema_version: int
    models: Mapping[str, ModelFileSelection]

    def selection(self, model_id: str) -> ModelFileSelection:
        try:
            return self.models[model_id]
        except KeyError as error:
            raise KeyError(f"model selection is not frozen: {model_id}") from error


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects shadowed configuration keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_file_selection(path: Path) -> FileSelectionIndex:
    """Load a non-symlink YAML selection file and validate its full v1 contract."""
    raw = load_yaml_mapping(path, label="model file selection")
    _require_exact_keys(raw, {"schema_version", "models"}, "selection index")
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise ValueError("model file selection schema_version must be 1")
    raw_models = raw["models"]
    if not isinstance(raw_models, dict):
        raise ValueError("model file selection models must be a mapping")
    model_ids = list(raw_models)
    if not all(
        isinstance(model_id, str) and MODEL_ID_RE.fullmatch(model_id)
        for model_id in model_ids
    ):
        raise ValueError("model file selection contains an unsafe model ID")
    if model_ids != sorted(model_ids):
        raise ValueError("model file selections must be sorted by model ID")
    models = {
        model_id: _parse_model_selection(model_id, raw_models[model_id])
        for model_id in model_ids
    }
    return FileSelectionIndex(1, MappingProxyType(models))


def load_yaml_mapping(path: Path, *, label: str) -> dict[object, object]:
    """Load a safe YAML mapping while rejecting symlinks and duplicate keys."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping")
    return raw


def _parse_model_selection(model_id: str, raw: object) -> ModelFileSelection:
    if not isinstance(raw, dict):
        raise ValueError(f"selection for {model_id} must be a mapping")
    _require_exact_keys(raw, {"include", "kinds", "exclude"}, f"selection for {model_id}")
    include = _exact_path_list(raw["include"], f"include for {model_id}", require_nonempty=True)
    exclude = _exact_path_list(raw["exclude"], f"exclude for {model_id}")
    raw_kinds = raw["kinds"]
    if not isinstance(raw_kinds, dict) or not all(
        isinstance(path, str) and isinstance(kind, str) for path, kind in raw_kinds.items()
    ):
        raise ValueError(f"kinds for {model_id} must be a string mapping")
    if set(raw_kinds) != set(include):
        raise ValueError(f"kinds for {model_id} must classify every include path exactly once")
    invalid_kinds = set(raw_kinds.values()) - SELECTION_KINDS
    if invalid_kinds:
        raise ValueError(f"selection for {model_id} contains an invalid file kind")
    if set(include) & set(exclude):
        raise ValueError(f"include and exclude overlap for {model_id}")
    kinds = MappingProxyType(
        {path: cast(SelectionKind, raw_kinds[path]) for path in include}
    )
    return ModelFileSelection(include=include, kinds=kinds, exclude=exclude)


def _exact_path_list(raw: object, label: str, *, require_nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(path, str) for path in raw):
        raise ValueError(f"{label} must be an array of exact paths")
    paths = tuple(raw)
    if require_nonempty and not paths:
        raise ValueError(f"{label} must not be empty")
    if paths != tuple(sorted(paths)):
        raise ValueError(f"{label} must be sorted")
    if len(paths) != len(set(paths)):
        raise ValueError(f"{label} must not contain duplicate paths")
    for path in paths:
        validate_exact_path(path, label)
    return paths


def validate_exact_path(path: str, label: str = "path") -> None:
    """Require one canonical, safe, non-pattern POSIX repository path."""
    relative = PurePosixPath(path)
    if (
        not path
        or len(path) > MAX_SELECTION_PATH_LENGTH
        or relative.is_absolute()
        or ".." in relative.parts
        or path != relative.as_posix()
        or "\\" in path
        or any(character in GLOB_CHARACTERS for character in path)
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or path == RESERVED_PATH
    ):
        raise ValueError(f"{label} contains an unsafe or non-exact path: {path!r}")


def _require_exact_keys(raw: dict[object, object], expected: set[str], label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"{label} must contain exactly {sorted(expected)}")
