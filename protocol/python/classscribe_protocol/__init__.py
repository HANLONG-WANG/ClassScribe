"""Versioned worker protocol primitives shared without model dependencies."""

from classscribe_protocol.cuda import cuda_status
from classscribe_protocol.dictation import (
    ActivationMode,
    ConfirmationMode,
    DictationConfig,
    DictationLanguage,
    DictationPunctuation,
    DictationState,
    DictationStatus,
)
from classscribe_protocol.dictation_ipc import (
    MAX_CONTROL_BYTES,
    DictationIPCClient,
    DictationIPCError,
    default_dictation_socket,
)
from classscribe_protocol.envelope import Envelope, ProtocolError, WorkerHello
from classscribe_protocol.framing import MAX_FRAME_BYTES, decode_payload, encode_frame
from classscribe_protocol.messages import (
    ALIGNMENT_CONTRACT,
    BODY_ASR_CONTRACT,
    PUNCTUATION_CONTRACT,
    STANDARD_METHODS,
    Priority,
    RPCErrorCode,
    RPCRequest,
    RPCResponse,
    validate_readonly_audio_path,
)
from classscribe_protocol.resident_workers import (
    MAX_RESIDENT_MANIFEST_BYTES,
    ResidentAccuracyRoute,
    ResidentWorkerManifest,
    ResidentWorkerRoute,
    load_resident_worker_manifest,
)
from classscribe_protocol.rpc import RPCClient, RPCServer
from classscribe_protocol.version import MIN_PROTOCOL_VERSION, PROTOCOL_NAME, PROTOCOL_VERSION
from classscribe_protocol.worker_main import run_worker

__all__ = [
    "ALIGNMENT_CONTRACT",
    "BODY_ASR_CONTRACT",
    "MAX_CONTROL_BYTES",
    "MAX_FRAME_BYTES",
    "MAX_RESIDENT_MANIFEST_BYTES",
    "MIN_PROTOCOL_VERSION",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "PUNCTUATION_CONTRACT",
    "STANDARD_METHODS",
    "ActivationMode",
    "ConfirmationMode",
    "DictationConfig",
    "DictationIPCClient",
    "DictationIPCError",
    "DictationLanguage",
    "DictationPunctuation",
    "DictationState",
    "DictationStatus",
    "Envelope",
    "Priority",
    "ProtocolError",
    "RPCClient",
    "RPCErrorCode",
    "RPCRequest",
    "RPCResponse",
    "RPCServer",
    "ResidentAccuracyRoute",
    "ResidentWorkerManifest",
    "ResidentWorkerRoute",
    "WorkerHello",
    "cuda_status",
    "decode_payload",
    "default_dictation_socket",
    "encode_frame",
    "load_resident_worker_manifest",
    "run_worker",
    "validate_readonly_audio_path",
]
