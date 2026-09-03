"""Compatibility envelope and worker greeting primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from classscribe_protocol.version import MIN_PROTOCOL_VERSION, PROTOCOL_VERSION


class ProtocolError(ValueError):
    """Raised for malformed or incompatible protocol data."""


@dataclass(frozen=True, slots=True)
class Envelope:
    protocol_version: int
    request_id: str
    message_type: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if isinstance(self.protocol_version, bool) or not isinstance(self.protocol_version, int):
            raise ProtocolError("protocol_version must be an integer")
        if not MIN_PROTOCOL_VERSION <= self.protocol_version <= PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version: {self.protocol_version}")
        if not self.request_id:
            raise ProtocolError("request_id must not be empty")
        if not self.message_type:
            raise ProtocolError("message_type must not be empty")


@dataclass(frozen=True, slots=True)
class WorkerHello:
    protocol_version: int
    worker_id: str
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError("worker protocol version does not match core")
        if not self.worker_id:
            raise ProtocolError("worker_id must not be empty")
        if not self.capabilities or any(not item for item in self.capabilities):
            raise ProtocolError("worker must declare non-empty capabilities")
