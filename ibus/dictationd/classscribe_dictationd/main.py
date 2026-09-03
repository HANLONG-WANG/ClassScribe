"""Runnable PipeWire/streaming-ASR dictation daemon."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from classscribe_protocol import PROTOCOL_VERSION, default_dictation_socket


def health_status() -> dict[str, str | int | bool]:
    return {
        "service": "classscribe-dictationd",
        "status": "ok",
        "protocol_version": PROTOCOL_VERSION,
        "audio_backend": "gstreamer-pipewire",
        "microphone_audio_persisted": False,
        "frame_ms": 20,
    }


def build_parser() -> argparse.ArgumentParser:
    runtime = default_dictation_socket().parent
    parser = argparse.ArgumentParser(prog="classscribe-dictationd")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--socket", type=Path, default=default_dictation_socket())
    parser.add_argument(
        "--worker-socket", type=Path, default=runtime / "workers" / "dictation.sock"
    )
    parser.add_argument(
        "--worker-manifest",
        type=Path,
        default=runtime / "resident-workers.json",
        help="core-owned resident worker handoff; re-read at every dictation session",
    )
    parser.add_argument(
        "--worker-model-id",
        default="nemotron_3_5_asr_streaming_0_6b",
        help="identity of the model preloaded at --worker-socket",
    )
    parser.add_argument(
        "--model-worker",
        action="append",
        type=_model_worker,
        metavar="MODEL_ID=SOCKET",
        help=(
            "repeatable concrete preloaded model route; when supplied, replaces "
            "--worker-model-id/--worker-socket"
        ),
    )
    parser.add_argument("--scheduler-socket", type=Path, default=runtime / "gpu-lease.sock")
    parser.add_argument(
        "--vad-worker-socket", type=Path, default=runtime / "workers" / "firered-vad.sock"
    )
    parser.add_argument(
        "--lid-worker-socket", type=Path, default=runtime / "workers" / "firered-lid.sock"
    )
    parser.add_argument(
        "--accuracy-worker-socket",
        type=Path,
        default=runtime / "workers" / "dictation-accuracy.sock",
    )
    parser.add_argument(
        "--energy-vad",
        action="store_true",
        help="explicit fallback when the FireRed streaming-VAD worker is unavailable",
    )
    parser.add_argument(
        "--without-gpu-lease",
        action="store_true",
        help="explicit CPU/test mode; production must coordinate with classscribe-core",
    )
    return parser


async def run_daemon(args: argparse.Namespace) -> None:
    from classscribe_dictationd.daemon import (
        DictationControlServer,
        DictationService,
        NullDictationLease,
        SchedulerLeaseClient,
        StreamingVAD,
    )
    from classscribe_dictationd.runtime import (
        EnergyVAD,
        GStreamerPipeWireSource,
        ManifestRoutedStreamingRecognizer,
        ManifestRPCFrameVAD,
        RoutedStreamingRecognizer,
        RPCFrameVAD,
        RPCStreamingRecognizer,
    )
    from classscribe_dictationd.session import DictationController, StreamingRecognizer

    lease = (
        NullDictationLease()
        if args.without_gpu_lease
        else SchedulerLeaseClient(args.scheduler_socket)
    )
    recognizer: StreamingRecognizer
    vad: StreamingVAD
    available_models_provider: Callable[[], tuple[str, ...]] | None
    if args.model_worker:
        worker_routes = args.model_worker
        if len({model_id for model_id, _socket in worker_routes}) != len(worker_routes):
            raise ValueError("each --model-worker model ID must be unique")
        recognizers = {
            model_id: RPCStreamingRecognizer(
                socket_path,
                model_id=model_id,
                lid_socket_path=args.lid_worker_socket,
                accuracy_socket_path=args.accuracy_worker_socket,
            )
            for model_id, socket_path in worker_routes
        }
        recognizer = RoutedStreamingRecognizer(recognizers, default_model_id=worker_routes[0][0])
        available_models = tuple(recognizers)
        available_models_provider = None
        vad = EnergyVAD() if args.energy_vad else RPCFrameVAD(args.vad_worker_socket)
    else:
        recognizer = ManifestRoutedStreamingRecognizer(args.worker_manifest)
        available_models = ()
        available_models_provider = recognizer.available_models
        vad = EnergyVAD() if args.energy_vad else ManifestRPCFrameVAD(args.worker_manifest)
    controller = DictationController(recognizer)
    service = DictationService(
        controller,
        GStreamerPipeWireSource(),
        lease,
        vad=vad,
        available_models=available_models,
        available_models_provider=available_models_provider,
    )
    server = DictationControlServer(args.socket, service)
    try:
        await server.serve_forever()
    finally:
        await server.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.health_check:
        print(json.dumps(health_status(), sort_keys=True))
        return 0
    try:
        asyncio.run(run_daemon(args))
    except KeyboardInterrupt:
        return 0
    return 0


def _model_worker(value: str) -> tuple[str, Path]:
    model_id, separator, socket_value = value.partition("=")
    if not separator or not model_id or not socket_value:
        raise argparse.ArgumentTypeError("expected MODEL_ID=SOCKET")
    if model_id == "auto_best":
        raise argparse.ArgumentTypeError("auto_best cannot identify a concrete worker")
    return model_id, Path(socket_value)


if __name__ == "__main__":
    raise SystemExit(main())
