"""Sample-exact derived recordings; sources remain immutable."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from classscribe.audio.limits import validate_transcription_duration
from classscribe.audio.media import FFmpegMediaPipeline, file_sha256
from classscribe.db.models import Recording
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.paths import AppPaths
from classscribe.security import parse_uuid


def extract_clip(
    source: Path,
    destination: Path,
    start_sample: int,
    end_sample: int,
    *,
    cancelled: threading.Event | None = None,
) -> int:
    """Decode before trimming so compressed inputs share the canonical sample timeline."""
    if (
        type(start_sample) is not int
        or type(end_sample) is not int
        or not 0 <= start_sample < end_sample
    ):
        raise ValueError("invalid clip range")
    source = FFmpegMediaPipeline.validate_source(source)
    if destination.resolve() == source:
        raise ValueError("clip may not overwrite its source")
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("clip cancelled")
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "2",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
        "-af",
        f"aresample=16000,atrim=start_sample={start_sample}:end_sample={end_sample},asetpts=PTS-STARTPTS",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-n",
        str(destination),
    ]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 3600
        while process.poll() is None:
            if cancelled is not None and cancelled.wait(0.05):
                raise InterruptedError("clip cancelled")
            if cancelled is None:
                time.sleep(0.05)
            if time.monotonic() > deadline:
                raise TimeoutError("clip decoding timed out")
        if process.returncode:
            raise ClassScribeError(ErrorCode.MEDIA_DECODE_FAILED, "clip decoding failed")
        master = FFmpegMediaPipeline._inspect_master(destination)
        if master.duration_samples != end_sample - start_sample:
            raise ClassScribeError(ErrorCode.AUDIO_TIMELINE_INVALID, "clip exceeds decoded audio")
        destination.chmod(0o400)
        return master.duration_samples
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        destination.unlink(missing_ok=True)
        raise


def create_recording_clip(
    sessions: sessionmaker[Session],
    paths: AppPaths,
    recording_id: str,
    submission_key: str,
    start_sample: int,
    end_sample: int,
    *,
    cancelled: threading.Event | None = None,
    provider: str = "local",
) -> str:
    parse_uuid(recording_id, field="recording_id")
    parse_uuid(submission_key, field="submission_key")
    validate_transcription_duration(end_sample - start_sample, provider)

    def existing() -> str | None:
        with sessions() as session:
            item = session.scalar(
                select(Recording).where(Recording.clip_submission_key == submission_key)
            )
            if item is None:
                return None
            if (item.parent_recording_id, item.source_start_sample, item.source_end_sample) != (
                recording_id,
                start_sample,
                end_sample,
            ):
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "clip submission key already used with different parameters",
                )
            return item.id

    found = existing()
    if found is not None:
        return found
    with sessions() as session:
        parent = session.get(Recording, recording_id)
        if parent is None:
            raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "recording does not exist")
        if parent.parent_recording_id is not None:
            raise ClassScribeError(
                ErrorCode.AUDIO_TIMELINE_INVALID, "select a range from the original recording"
            )
        if (
            type(start_sample) is not int
            or type(end_sample) is not int
            or not 0 <= start_sample < end_sample <= parent.duration_samples
        ):
            raise ClassScribeError(
                ErrorCode.AUDIO_TIMELINE_INVALID, "clip range is outside recording"
            )
        source, digest, name = Path(parent.source_path), parent.source_sha256, parent.source_name
    root = paths.data_path("recordings", recording_id).resolve()
    if source.is_symlink() or not source.resolve().is_relative_to(root):
        raise ClassScribeError(ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "recording file is invalid")
    if file_sha256(source) != digest:
        raise ClassScribeError(ErrorCode.AUDIO_MASTER_INVALID, "source audio has changed")
    identifier = str(uuid4())
    directory = paths.data_path("recordings", identifier)
    target = directory / "source" / "clip.wav"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    committed = False
    try:
        duration = extract_clip(source, target, start_sample, end_sample, cancelled=cancelled)
        if file_sha256(source) != digest:
            raise ClassScribeError(
                ErrorCode.AUDIO_MASTER_INVALID, "source audio changed during clipping"
            )
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("clip cancelled")
        try:
            with sessions.begin() as session:
                session.add(
                    Recording(
                        id=identifier,
                        source_name=f"{Path(name).stem}-clip.wav",
                        source_sha256=file_sha256(target),
                        source_path=str(target),
                        duration_samples=duration,
                        sample_rate=16000,
                        channels=1,
                        parent_recording_id=recording_id,
                        source_start_sample=start_sample,
                        source_end_sample=end_sample,
                        clip_submission_key=submission_key,
                        audio_qc_json={
                            "upload_validated": True,
                            "source_bytes": target.stat().st_size,
                        },
                    )
                )
            committed = True
            return identifier
        except IntegrityError:
            found = existing()
            if found is not None:
                return found
            raise
    finally:
        if not committed:
            shutil.rmtree(directory, ignore_errors=True)
