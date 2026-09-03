"""Offline production adapter for pyannote Community-1."""

from __future__ import annotations

import asyncio
import gc
import math
import time
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

from classscribe_protocol.adapter import AdapterError, StatefulAdapter
from classscribe_protocol.messages import RPCErrorCode

SAMPLE_RATE = 16_000


class PyannoteAdapter(StatefulAdapter):
    def __init__(self) -> None:
        super().__init__(
            "pyannote",
            capabilities=(
                "diarization",
                "overlap",
                "exclusive_diarization",
                "speaker_embeddings",
            ),
            supported_methods=("diarize",),
        )
        self._pipeline: Any | None = None
        self._torch: Any | None = None
        self._soundfile: Any | None = None
        self._device: Any | None = None

    async def dispatch(
        self, method: str, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        if method == "load":
            return await self._load(params, cancelled)
        if method == "unload":
            self._release_model()
            return await super().dispatch(method, params, cancelled)
        if method == "diarize" and self._pipeline is not None:
            return await self._diarize(params, cancelled)
        return await super().dispatch(method, params, cancelled)

    async def _load(self, params: Mapping[str, Any], cancelled: asyncio.Event) -> Mapping[str, Any]:
        model_path = Path(str(params["model_path"]))
        if model_path.is_symlink() or not (model_path / "config.yaml").is_file():
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                "pyannote model_path must be a complete non-symlink local snapshot",
            )

        def load_pipeline() -> tuple[Any, Any, Any, Any]:
            try:
                import soundfile  # type: ignore[import-not-found]
                import torch  # type: ignore[import-not-found]
                from pyannote.audio import Pipeline  # type: ignore[import-not-found]
            except ImportError as exc:
                raise AdapterError(
                    RPCErrorCode.MODEL_LOAD_FAILED,
                    "isolated pyannote worker environment is incomplete",
                ) from exc
            pipeline = Pipeline.from_pretrained(str(model_path))
            device_name = str(params.get("device", "auto"))
            if device_name == "auto":
                device_name = "cuda:0" if torch.cuda.is_available() else "cpu"
            device = torch.device(device_name)
            pipeline.to(device)
            if hasattr(pipeline, "embedding_exclude_overlap"):
                pipeline.embedding_exclude_overlap = True
            return pipeline, torch, soundfile, device

        try:
            pipeline, torch, soundfile, device = await asyncio.to_thread(load_pipeline)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.MODEL_LOAD_FAILED,
                f"pyannote initialization failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        result = await super().dispatch("load", params, cancelled)
        self._pipeline = pipeline
        self._torch = torch
        self._soundfile = soundfile
        self._device = device
        return {
            **result,
            "backend": "pyannote_audio_community_1",
            "device": str(device),
            "offline": True,
            "embedding_exclude_overlap": True,
        }

    async def _diarize(
        self, params: Mapping[str, Any], cancelled: asyncio.Event
    ) -> Mapping[str, Any]:
        start_sample = int(params["start_sample"])
        end_sample = int(params["end_sample"])
        started = time.perf_counter()

        def infer() -> tuple[Any, int]:
            assert (
                self._soundfile is not None
                and self._torch is not None
                and self._pipeline is not None
            )
            audio, sample_rate = self._soundfile.read(
                str(params["audio_path"]),
                start=start_sample,
                stop=end_sample,
                dtype="float32",
                always_2d=True,
            )
            if sample_rate != SAMPLE_RATE or audio.shape[1] != 1:
                raise ValueError("pyannote worker requires canonical 16 kHz mono audio")
            waveform = self._torch.from_numpy(audio.T.copy())
            kwargs: dict[str, int | bool] = {"return_embeddings": True}
            if "num_speakers" in params:
                kwargs["num_speakers"] = int(params["num_speakers"])
            else:
                kwargs["min_speakers"] = int(params.get("min_speakers", 1))
                kwargs["max_speakers"] = int(params.get("max_speakers", 12))
            if cancelled.is_set():
                raise asyncio.CancelledError
            output = self._pipeline({"waveform": waveform, "sample_rate": sample_rate}, **kwargs)
            return output, int(waveform.shape[-1])

        try:
            output, processed_samples = await asyncio.to_thread(infer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AdapterError(
                RPCErrorCode.INTERNAL,
                f"pyannote inference failed: {type(exc).__name__}: {exc}",
            ) from exc
        if cancelled.is_set():
            raise asyncio.CancelledError
        regular = _annotation_segments(
            output.speaker_diarization, start_sample=start_sample, end_sample=end_sample
        )
        exclusive = _annotation_segments(
            output.exclusive_speaker_diarization,
            start_sample=start_sample,
            end_sample=end_sample,
        )
        embeddings = _embedding_records(
            output.speaker_diarization.labels(),
            output.speaker_embeddings,
            regular,
            exclusive,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        peak_vram_mb = 0.0
        torch = self._torch
        device = self._device
        if torch is not None and device is not None and device.type == "cuda":
            peak_vram_mb = torch.cuda.max_memory_allocated(device) / (1024 * 1024)
        return {
            "segments": regular,
            "exclusive_segments": exclusive,
            "embeddings": embeddings,
            "metrics": {
                "inference_ms": round(elapsed_ms, 3),
                "peak_vram_mb": round(peak_vram_mb, 3),
                "rtf": round(elapsed_ms / 1000 / (processed_samples / SAMPLE_RATE), 6),
                "speaker_count": len(output.speaker_diarization.labels()),
                "regular_span_count": len(regular),
                "exclusive_span_count": len(exclusive),
                "embedding_support_count": sum(int(item["support_count"]) for item in embeddings),
                "backend": "pyannote_audio_community_1",
            },
            "warnings": [],
        }

    def _release_model(self) -> None:
        self._pipeline = None
        self._soundfile = None
        torch = self._torch
        device = self._device
        self._torch = None
        self._device = None
        gc.collect()
        if torch is not None and device is not None and device.type == "cuda":
            torch.cuda.empty_cache()


def _annotation_segments(
    annotation: Any, *, start_sample: int, end_sample: int
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for turn, _track, speaker in annotation.itertracks(yield_label=True):
        start = max(start_sample, start_sample + round(float(turn.start) * SAMPLE_RATE))
        end = min(end_sample, start_sample + round(float(turn.end) * SAMPLE_RATE))
        if end > start:
            segments.append(
                {
                    "start_sample": start,
                    "end_sample": end,
                    "speaker_local": str(speaker),
                    "confidence_raw": None,
                }
            )
    return sorted(segments, key=lambda item: (item["start_sample"], item["end_sample"]))


def _embedding_records(
    labels: list[str],
    speaker_embeddings: Any,
    regular: list[dict[str, Any]],
    exclusive: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if speaker_embeddings is None:
        return []
    overlap_ranges = _true_overlap_ranges(regular)
    result: list[dict[str, Any]] = []
    for index, label in enumerate(labels):
        vector = [float(value) for value in speaker_embeddings[index].tolist()]
        if not vector or not all(math.isfinite(value) for value in vector):
            continue
        candidates: list[dict[str, Any]] = []
        for item in exclusive:
            if item["speaker_local"] != label:
                continue
            for start, end in _subtract_ranges(
                int(item["start_sample"]),
                int(item["end_sample"]),
                overlap_ranges,
            ):
                candidates.append({"start_sample": start, "end_sample": end})
        supports = _choose_support_spans(candidates)
        for start, end in supports:
            duration = end - start
            result.append(
                {
                    "start_sample": start,
                    "end_sample": end,
                    "speaker_local": label,
                    "vector": vector,
                    "signal_quality": round(min(1.0, duration / (3 * SAMPLE_RATE)), 6),
                    "overlap": False,
                    "support_count": 1,
                    "embedding_kind": "pyannote_cluster_centroid_with_nonoverlap_support",
                }
            )
    return result


def _subtract_ranges(
    start: int, end: int, exclusions: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Return portions of ``[start, end)`` that contain no detected overlap."""

    remaining = [(start, end)]
    for excluded_start, excluded_end in exclusions:
        next_remaining: list[tuple[int, int]] = []
        for current_start, current_end in remaining:
            if excluded_end <= current_start or excluded_start >= current_end:
                next_remaining.append((current_start, current_end))
                continue
            if current_start < excluded_start:
                next_remaining.append((current_start, excluded_start))
            if excluded_end < current_end:
                next_remaining.append((excluded_end, current_end))
        remaining = next_remaining
    return [(part_start, part_end) for part_start, part_end in remaining if part_end > part_start]


def _choose_support_spans(segments: list[dict[str, Any]]) -> list[tuple[int, int]]:
    supports: list[tuple[int, int]] = []
    for item in sorted(
        segments,
        key=lambda value: int(value["end_sample"]) - int(value["start_sample"]),
        reverse=True,
    ):
        start = int(item["start_sample"])
        end = int(item["end_sample"])
        cursor = start
        while end - cursor >= SAMPLE_RATE and len(supports) < 3:
            support_end = min(end, cursor + 4 * SAMPLE_RATE)
            supports.append((cursor, support_end))
            cursor = support_end
        if len(supports) >= 3:
            break
    return supports


def _true_overlap_ranges(regular: list[dict[str, Any]]) -> list[tuple[int, int]]:
    boundaries = sorted(
        {int(item[key]) for item in regular for key in ("start_sample", "end_sample")}
    )
    overlaps: list[tuple[int, int]] = []
    for start, end in pairwise(boundaries):
        speakers = {
            str(item["speaker_local"])
            for item in regular
            if int(item["start_sample"]) < end and start < int(item["end_sample"])
        }
        if len(speakers) >= 2:
            overlaps.append((start, end))
    return overlaps


def create_adapter() -> PyannoteAdapter:
    return PyannoteAdapter()
