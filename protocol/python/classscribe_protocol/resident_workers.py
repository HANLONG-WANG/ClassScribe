"""Validated handoff from the core worker supervisor to dictationd."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classscribe_protocol.version import PROTOCOL_VERSION

MAX_RESIDENT_MANIFEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ResidentWorkerRoute:
    model_id: str
    model_revision: str
    socket_path: Path

    @classmethod
    def from_dict(cls, value: object) -> ResidentWorkerRoute:
        if not isinstance(value, dict):
            raise ValueError("resident worker route must be an object")
        model_id = value.get("model_id")
        revision = value.get("model_revision")
        socket = value.get("socket_path")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("resident worker route requires a model ID")
        if (
            not isinstance(revision, str)
            or len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            raise ValueError("resident worker route requires a pinned revision")
        if not isinstance(socket, str) or not Path(socket).is_absolute():
            raise ValueError("resident worker route socket must be absolute")
        return cls(model_id, revision, Path(socket))

    def as_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "socket_path": str(self.socket_path),
        }


@dataclass(frozen=True, slots=True)
class ResidentAccuracyRoute:
    """Language-specific final decoder, resident or available by hot switch."""

    model_id: str
    model_revision: str
    socket_path: Path | None
    expected_load_ms: float | None

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("accuracy route requires a model ID")
        if len(self.model_revision) != 40 or any(
            character not in "0123456789abcdef" for character in self.model_revision
        ):
            raise ValueError("accuracy route requires a pinned revision")
        if self.socket_path is not None and not self.socket_path.is_absolute():
            raise ValueError("accuracy route socket must be absolute")
        if self.socket_path is not None and self.expected_load_ms is not None:
            raise ValueError("resident accuracy routes must not advertise load latency")
        if self.socket_path is None and (
            self.expected_load_ms is None or self.expected_load_ms <= 0
        ):
            raise ValueError("hot-switch accuracy routes require a positive load estimate")

    @classmethod
    def from_dict(cls, value: object) -> ResidentAccuracyRoute:
        if not isinstance(value, dict):
            raise ValueError("accuracy route must be an object")
        if unknown := value.keys() - {
            "model_id",
            "model_revision",
            "socket_path",
            "expected_load_ms",
        }:
            raise ValueError(f"unknown accuracy route fields: {sorted(unknown)}")
        model_id = value.get("model_id")
        revision = value.get("model_revision")
        load_ms = value.get("expected_load_ms")
        if not isinstance(model_id, str) or not isinstance(revision, str):
            raise ValueError("accuracy route identity is invalid")
        if load_ms is not None and (
            isinstance(load_ms, bool) or not isinstance(load_ms, (int, float))
        ):
            raise ValueError("accuracy load estimate must be numeric or null")
        return cls(
            model_id,
            revision,
            _optional_absolute_path(value.get("socket_path"), "accuracy socket"),
            float(load_ms) if load_ms is not None else None,
        )

    def as_dict(self) -> dict[str, str | float | None]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "socket_path": str(self.socket_path) if self.socket_path is not None else None,
            "expected_load_ms": self.expected_load_ms,
        }


@dataclass(frozen=True, slots=True)
class ResidentWorkerManifest:
    ready: bool
    default_model_id: str | None
    routes: tuple[ResidentWorkerRoute, ...]
    profile_routes: dict[str, str]
    vad_socket: Path | None
    lid_socket: Path | None
    accuracy_socket: Path | None
    errors: tuple[str, ...]
    generated_at: str
    accuracy_routes: dict[str, ResidentAccuracyRoute] = field(default_factory=dict)
    protocol_version: int = PROTOCOL_VERSION
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION or self.schema_version != 1:
            raise ValueError("unsupported resident worker manifest version")
        route_ids = [item.model_id for item in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("resident worker routes repeat a model ID")
        if self.default_model_id is not None and self.default_model_id not in route_ids:
            raise ValueError("default resident model has no route")
        if any(model_id not in route_ids for model_id in self.profile_routes.values()):
            raise ValueError("resident profile points to an unavailable route")
        if any(language not in {"zh", "ja", "en", "auto"} for language in self.accuracy_routes):
            raise ValueError("accuracy route language is invalid")
        route_by_id = {item.model_id: item for item in self.routes}
        for accuracy in self.accuracy_routes.values():
            if accuracy.socket_path is None:
                continue
            resident = route_by_id.get(accuracy.model_id)
            if resident is None or resident.socket_path != accuracy.socket_path:
                raise ValueError("resident accuracy route has no matching worker route")
        if any(
            len(key.split(".")) != 3
            or key.split(".")[0] != "ibus"
            or key.split(".")[1] not in {"zh", "ja", "en", "auto"}
            or key.split(".")[2] not in {"fast", "balanced", "accuracy"}
            for key in self.profile_routes
        ):
            raise ValueError("resident profile route key is invalid")
        if self.ready and (self.default_model_id is None or self.vad_socket is None):
            raise ValueError("ready dictation requires default ASR and VAD sockets")
        if not self.generated_at:
            raise ValueError("resident worker manifest requires a generation time")

    @classmethod
    def from_dict(cls, value: object) -> ResidentWorkerManifest:
        if not isinstance(value, dict):
            raise ValueError("resident worker manifest must be an object")
        expected = {
            "schema_version",
            "protocol_version",
            "ready",
            "default_model_id",
            "routes",
            "profile_routes",
            "vad_socket",
            "lid_socket",
            "accuracy_socket",
            "accuracy_routes",
            "errors",
            "generated_at",
        }
        if unknown := value.keys() - expected:
            raise ValueError(f"unknown resident worker manifest fields: {sorted(unknown)}")
        routes = value.get("routes")
        profiles = value.get("profile_routes")
        accuracy_routes = value.get("accuracy_routes")
        errors = value.get("errors")
        if (
            not isinstance(routes, list)
            or not isinstance(profiles, dict)
            or not isinstance(accuracy_routes, dict)
        ):
            raise ValueError("resident worker routes/profiles have invalid types")
        if not isinstance(errors, list) or any(not isinstance(item, str) for item in errors):
            raise ValueError("resident worker errors must be strings")
        if any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in profiles.items()
        ):
            raise ValueError("resident worker profiles must be a string map")
        ready = value.get("ready")
        default = value.get("default_model_id")
        protocol_version = value.get("protocol_version")
        schema_version = value.get("schema_version")
        generated_at = value.get("generated_at")
        if not isinstance(ready, bool) or (default is not None and not isinstance(default, str)):
            raise ValueError("resident worker readiness/default has an invalid type")
        if (
            not isinstance(protocol_version, int)
            or isinstance(protocol_version, bool)
            or not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
        ):
            raise ValueError("resident worker manifest versions must be integers")
        if not isinstance(generated_at, str) or not generated_at:
            raise ValueError("resident worker manifest requires a generation time")
        return cls(
            ready=ready,
            default_model_id=default,
            routes=tuple(ResidentWorkerRoute.from_dict(item) for item in routes),
            profile_routes={str(key): str(item) for key, item in profiles.items()},
            vad_socket=_optional_absolute_path(value.get("vad_socket"), "vad_socket"),
            lid_socket=_optional_absolute_path(value.get("lid_socket"), "lid_socket"),
            accuracy_socket=_optional_absolute_path(
                value.get("accuracy_socket"), "accuracy_socket"
            ),
            errors=tuple(errors),
            generated_at=generated_at,
            accuracy_routes={
                str(key): ResidentAccuracyRoute.from_dict(item)
                for key, item in accuracy_routes.items()
            },
            protocol_version=protocol_version,
            schema_version=schema_version,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "ready": self.ready,
            "default_model_id": self.default_model_id,
            "routes": [item.as_dict() for item in self.routes],
            "profile_routes": dict(self.profile_routes),
            "vad_socket": str(self.vad_socket) if self.vad_socket is not None else None,
            "lid_socket": str(self.lid_socket) if self.lid_socket is not None else None,
            "accuracy_socket": (
                str(self.accuracy_socket) if self.accuracy_socket is not None else None
            ),
            "accuracy_routes": {
                language: route.as_dict() for language, route in self.accuracy_routes.items()
            },
            "errors": list(self.errors),
            "generated_at": self.generated_at,
        }


def load_resident_worker_manifest(path: Path) -> ResidentWorkerManifest:
    """Read only a same-user, non-symlink, size-bounded supervisor handoff."""

    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_size > MAX_RESIDENT_MANIFEST_BYTES
    ):
        raise ValueError("resident worker manifest path is unsafe")
    return ResidentWorkerManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _optional_absolute_path(value: object, name: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"{name} must be an absolute path or null")
    return Path(value)
