"""Bounded MessagePack framing for Unix-domain worker sockets."""

from __future__ import annotations

import asyncio
import struct

import msgspec

from classscribe_protocol.envelope import ProtocolError
from classscribe_protocol.messages import RPCRequest, RPCResponse

HEADER = struct.Struct(">I")
MAX_FRAME_BYTES = 16 * 1024 * 1024
Message = RPCRequest | RPCResponse


def encode_frame(message: Message) -> bytes:
    payload = msgspec.msgpack.encode(message)
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("message exceeds the transport frame limit")
    return HEADER.pack(len(payload)) + payload


def decode_payload[MessageT: (RPCRequest, RPCResponse)](
    payload: bytes, message_type: type[MessageT]
) -> MessageT:
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("invalid MessagePack payload length")
    try:
        decoded = msgspec.msgpack.decode(payload, type=message_type, strict=True)
        # msgspec constructs dataclasses without invoking __post_init__.
        decoded.__post_init__()
        return decoded
    except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, ProtocolError):
            raise
        raise ProtocolError(f"invalid MessagePack payload: {exc}") from exc


async def read_frame[MessageT: (RPCRequest, RPCResponse)](
    reader: asyncio.StreamReader, message_type: type[MessageT]
) -> MessageT:
    try:
        header = await reader.readexactly(HEADER.size)
        (size,) = HEADER.unpack(header)
        if size <= 0 or size > MAX_FRAME_BYTES:
            raise ProtocolError("invalid transport frame length")
        return decode_payload(await reader.readexactly(size), message_type)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("truncated transport frame") from exc


async def write_frame(writer: asyncio.StreamWriter, message: Message) -> None:
    writer.write(encode_frame(message))
    await writer.drain()
