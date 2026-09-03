#!/usr/bin/env python3
"""Materialize deterministic PCM WAV guard fixtures from the checked-in recipe."""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import tempfile
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="materialize-regression-audio")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_directory", type=Path)
    return parser


def materialize(manifest: Path, output_directory: Path) -> tuple[Path, ...]:
    value = _object(manifest)
    audio_format = value.get("format")
    fixtures = value.get("fixtures")
    if not isinstance(audio_format, dict) or not isinstance(fixtures, list):
        raise ValueError("regression audio manifest lacks format or fixtures")
    if audio_format != {
        "container": "wav",
        "codec": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
    }:
        raise ValueError("regression audio format must be canonical PCM")
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_directory.chmod(0o700)
    results: list[Path] = []
    identifiers: set[str] = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("fixture must be an object")
        identifier = str(fixture.get("id", ""))
        if not identifier or identifier in identifiers or not identifier.replace("-", "").isalnum():
            raise ValueError("fixture ID must be safe and unique")
        identifiers.add(identifier)
        duration = int(fixture.get("duration_samples", 0))
        if duration <= 0 or duration > 16000 * 60:
            raise ValueError("fixture duration must be within one minute")
        samples = [0] * duration
        segments = fixture.get("segments")
        if not isinstance(segments, list):
            raise ValueError("fixture segments must be an array")
        for segment in segments:
            _apply_tone(samples, segment)
        destination = output_directory / f"{identifier}.wav"
        _write_wav(destination, samples)
        results.append(destination)
    return tuple(results)


def _apply_tone(samples: list[int], value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("tone segment must be an object")
    start = int(value.get("start_sample", -1))
    end = int(value.get("end_sample", -1))
    frequency = float(value.get("frequency_hz", 0))
    amplitude = float(value.get("amplitude", 0))
    if not 0 <= start < end <= len(samples):
        raise ValueError("tone segment exceeds fixture duration")
    if not 20 <= frequency <= 8000 or not 0 < amplitude <= 0.9:
        raise ValueError("tone frequency or amplitude is unsafe")
    scale = round(amplitude * 32767)
    for index in range(start, end):
        samples[index] = round(scale * math.sin(2 * math.pi * frequency * index / 16000))


def _write_wav(path: Path, samples: list[int]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("manifest must be a regular non-symlink file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = materialize(args.manifest, args.output_directory)
    print(json.dumps({"created": [str(path) for path in paths]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
