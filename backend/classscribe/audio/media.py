"""Immutable media ingestion and FFmpeg canonical master generation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import validate_restricted_directory
from classscribe.timeline import SAMPLE_RATE

SUPPORTED_MEDIA_SUFFIXES = frozenset(
    {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm"}
)
MAX_SOURCE_BYTES = 8 * 1024**3
MAX_DURATION_SECONDS = 90 * 60


class ChannelMixPolicy(StrEnum):
    EQUAL = "equal"
    BEST = "best"


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    format_name: str
    codec_name: str
    duration_seconds: Decimal
    sample_rate: int
    channels: int
    bit_rate: int | None


@dataclass(frozen=True, slots=True)
class ImportedMedia:
    source_name: str
    source_path: Path
    source_sha256: str
    size_bytes: int
    metadata: MediaMetadata


@dataclass(frozen=True, slots=True)
class AudioMaster:
    path: Path
    sha256: str
    duration_samples: int
    sample_rate: int = SAMPLE_RATE
    channels: int = 1
    codec: str = "pcm_s16le"


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True, timeout=3600)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FFmpegMediaPipeline:
    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        runner: Runner = _run,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self._runner = runner

    def _execute(self, command: Sequence[str], code: ErrorCode) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(command)
        except (OSError, subprocess.SubprocessError) as error:
            raise ClassScribeError(code, f"media subprocess failed: {error}") from error

    @staticmethod
    def validate_source(path: Path) -> Path:
        absolute = path.absolute()
        try:
            canonical = path.resolve(strict=True)
        except OSError as error:
            raise ClassScribeError(
                ErrorCode.UNSUPPORTED_MEDIA, "media source does not exist"
            ) from error
        if absolute != canonical or not canonical.is_file() or canonical.is_symlink():
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT,
                "media source must be a canonical regular file without symlinks",
            )
        if canonical.suffix.lower() not in SUPPORTED_MEDIA_SUFFIXES:
            raise ClassScribeError(ErrorCode.UNSUPPORTED_MEDIA, "unsupported media suffix")
        if canonical.stat().st_size > MAX_SOURCE_BYTES:
            raise ClassScribeError(ErrorCode.UPLOAD_TOO_LARGE, "media exceeds 8 GiB")
        return canonical

    def probe(self, path: Path) -> MediaMetadata:
        source = self.validate_source(path)
        command = (
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration,bit_rate:stream=index,codec_type,codec_name,sample_rate,channels,duration",
            "-of",
            "json",
            str(source),
        )
        completed = self._execute(command, ErrorCode.MEDIA_PROBE_FAILED)
        if completed.returncode:
            raise ClassScribeError(
                ErrorCode.MEDIA_PROBE_FAILED,
                f"ffprobe exited {completed.returncode}: {completed.stderr.strip()[:500]}",
            )
        try:
            payload: dict[str, Any] = json.loads(completed.stdout)
            streams = payload["streams"]
            audio = next(item for item in streams if item.get("codec_type") == "audio")
            format_data = payload["format"]
            duration_raw = audio.get("duration") or format_data["duration"]
            duration = Decimal(str(duration_raw))
            sample_rate = int(audio["sample_rate"])
            channels = int(audio["channels"])
            bit_rate_raw = format_data.get("bit_rate")
            bit_rate = int(bit_rate_raw) if bit_rate_raw is not None else None
            format_name = str(format_data["format_name"])
            codec_name = str(audio["codec_name"])
        except (KeyError, StopIteration, TypeError, ValueError, InvalidOperation) as error:
            raise ClassScribeError(
                ErrorCode.MEDIA_PROBE_FAILED, f"invalid ffprobe audio metadata: {error}"
            ) from error
        if duration < 0 or duration > MAX_DURATION_SECONDS:
            raise ClassScribeError(ErrorCode.AUDIO_TOO_LONG, "media exceeds 90-minute limit")
        if sample_rate <= 0 or channels <= 0 or channels > 8:
            raise ClassScribeError(
                ErrorCode.DECODE_LIMIT_EXCEEDED, "invalid audio stream dimensions"
            )
        return MediaMetadata(format_name, codec_name, duration, sample_rate, channels, bit_rate)

    def import_source(self, source: Path, destination_directory: Path) -> ImportedMedia:
        canonical = self.validate_source(source)
        metadata = self.probe(canonical)
        if destination_directory.is_symlink():
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "source directory may not be a symlink"
            )
        destination_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination_directory.chmod(0o700)
        validate_restricted_directory(destination_directory)
        target = destination_directory / f"{uuid4()}{canonical.suffix.lower()}"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".source-", suffix=canonical.suffix.lower(), dir=destination_directory
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        try:
            with canonical.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
                for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                    digest.update(chunk)
                    output_file.write(chunk)
                output_file.flush()
                os.fsync(output_file.fileno())
            os.chmod(temporary, 0o400)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return ImportedMedia(
            source_name=canonical.name,
            source_path=target,
            source_sha256=digest.hexdigest(),
            size_bytes=target.stat().st_size,
            metadata=metadata,
        )

    def normalize(
        self,
        source: Path,
        destination: Path,
        *,
        mix_policy: ChannelMixPolicy = ChannelMixPolicy.EQUAL,
        best_channel: int | None = None,
    ) -> AudioMaster:
        canonical = self.validate_source(source)
        metadata = self.probe(canonical)
        if destination.name != "audio_master.wav":
            raise ValueError("canonical master must be named audio_master.wav")
        if destination.absolute() == canonical:
            raise ValueError("audio master may not overwrite the immutable source")
        if mix_policy is ChannelMixPolicy.BEST and (
            best_channel is None or not 0 <= best_channel < metadata.channels
        ):
            raise ValueError(
                "best channel index must be present and within the source channel count"
            )
        if destination.parent.is_symlink():
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "master directory may not be a symlink"
            )
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.parent.chmod(0o700)
        validate_restricted_directory(destination.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".audio-master-", suffix=".wav", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "2",
            "-i",
            str(canonical),
            "-map",
            "0:a:0",
            "-vn",
        ]
        if mix_policy is ChannelMixPolicy.BEST:
            command.extend(("-af", f"pan=mono|c0=c{best_channel}"))
        elif metadata.channels > 1:
            gain = f"{1 / metadata.channels:.10f}"
            terms = "+".join(f"{gain}*c{index}" for index in range(metadata.channels))
            command.extend(("-af", f"pan=mono|c0={terms}"))
        command.extend(
            ("-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-y", str(temporary))
        )
        try:
            completed = self._execute(tuple(command), ErrorCode.MEDIA_DECODE_FAILED)
            if completed.returncode:
                raise ClassScribeError(
                    ErrorCode.MEDIA_DECODE_FAILED,
                    f"ffmpeg exited {completed.returncode}: {completed.stderr.strip()[:500]}",
                )
            master = self._inspect_master(temporary)
            os.chmod(temporary, 0o400)
            os.replace(temporary, destination)
            return AudioMaster(
                path=destination,
                sha256=file_sha256(destination),
                duration_samples=master.duration_samples,
            )
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _inspect_master(path: Path) -> AudioMaster:
        try:
            with wave.open(str(path), "rb") as recording:
                channels = recording.getnchannels()
                rate = recording.getframerate()
                width = recording.getsampwidth()
                compression = recording.getcomptype()
                frames = recording.getnframes()
        except (OSError, wave.Error) as error:
            raise ClassScribeError(
                ErrorCode.AUDIO_MASTER_INVALID, f"cannot read canonical WAV: {error}"
            ) from error
        if (
            channels != 1
            or rate != SAMPLE_RATE
            or width != 2
            or compression != "NONE"
            or frames < 0
        ):
            raise ClassScribeError(
                ErrorCode.AUDIO_MASTER_INVALID,
                "audio master must be mono 16 kHz uncompressed 16-bit PCM",
            )
        return AudioMaster(path, "", frames)

    def make_multichannel_qc_wav(self, source: Path, destination: Path) -> Path:
        """Decode a temporary 16 kHz PCM copy while preserving source channels for QC."""

        canonical = self.validate_source(source)
        if destination.parent.is_symlink():
            raise ClassScribeError(
                ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "QC directory may not be a symlink"
            )
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.parent.chmod(0o700)
        validate_restricted_directory(destination.parent)
        command = (
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "2",
            "-i",
            str(canonical),
            "-map",
            "0:a:0",
            "-vn",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(destination),
        )
        completed = self._execute(command, ErrorCode.MEDIA_DECODE_FAILED)
        if completed.returncode:
            raise ClassScribeError(ErrorCode.MEDIA_DECODE_FAILED, completed.stderr.strip()[:500])
        return destination
