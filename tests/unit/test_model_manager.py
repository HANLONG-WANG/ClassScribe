from __future__ import annotations

import hashlib
import json
import socket
import wave
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from classscribe.db.models import ModelHealth, ModelInstallation
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import (
    DownloadReceipt,
    HealthCheckOutcome,
    ManifestFile,
    ModelManager,
    ModelManifest,
    SQLAlchemyInstallationRecorder,
    runtime_environment,
)
from classscribe.models.manager import AUDIT_FILENAME
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

REVISION_A = "a" * 40
REVISION_B = "b" * 40


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def manifest(
    revision: str = REVISION_A,
    *,
    model: bytes = b"model-a",
    code: bytes = b"def load(): return 'a'\n",
) -> tuple[ModelManifest, dict[str, bytes]]:
    content = {"weights/model.bin": model, "code/modeling.py": code}
    files = tuple(
        ManifestFile(
            path=path,
            sha256=sha(value),
            size_bytes=len(value),
            kind="remote_code" if path.startswith("code/") else "model",
        )
        for path, value in content.items()
    )
    return (
        ModelManifest(
            manifest_version=1,
            model_id="moss-test",
            repository="example/moss-test",
            revision=revision,
            worker="moss_td",
            license_id="Apache-2.0",
            license_url="https://example.invalid/licenses/apache-2.0",
            requires_terms_acceptance=False,
            trust_remote_code=True,
            estimated_download_bytes=sum(map(len, content.values())),
            installed_size_bytes=sum(map(len, content.values())),
            environment={"torch": "2.8.0", "device": "cuda:0"},
            files=files,
        ),
        content,
    )


class FakeDownloader:
    def __init__(
        self,
        content: Mapping[str, bytes],
        *,
        resolved_revision: str | None = None,
    ) -> None:
        self.content = content
        self.resolved_revision = resolved_revision
        self.calls = 0

    def download(
        self,
        *,
        repository: str,
        revision: str,
        destination: Path,
        files: Sequence[ManifestFile],
    ) -> DownloadReceipt:
        del repository, files
        self.calls += 1
        for relative, value in self.content.items():
            target = destination / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(value)
        return DownloadReceipt(
            resolved_revision=self.resolved_revision or revision,
            source="fixture://offline-model-store",
            downloaded_bytes=sum(map(len, self.content.values())),
        )


def write_health_wav(path: Path, *, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x01\x00" * 3_200)
    return path


def test_health_audio_must_match_worker_mono_contract(tmp_path: Path) -> None:
    manager = ModelManager(tmp_path / "models")
    model_manifest, content = manifest()
    plan = manager.request_user_install(model_manifest)
    with pytest.raises(ValueError, match="mono"):
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(content),
            health_audio=write_health_wav(tmp_path / "stereo.wav", channels=2),
            health_check=healthy,
        )


def healthy(
    model_path: Path, audio_path: Path, environment: Mapping[str, str]
) -> HealthCheckOutcome:
    assert (model_path / "weights/model.bin").read_bytes()
    with wave.open(str(audio_path), "rb") as recording:
        assert recording.getnframes() == 3_200
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    return HealthCheckOutcome(True, 6120, "decoded non-empty transcript", {"gpu": "fixture"})


def install(
    manager: ModelManager,
    value: ModelManifest,
    content: Mapping[str, bytes],
    audio: Path,
    *,
    check: Any = healthy,
) -> Path:
    plan = manager.request_user_install(value)
    result = manager.install_confirmed(
        plan.confirmation_token,
        downloader=FakeDownloader(content),
        health_audio=audio,
        health_check=check,
    )
    return result.local_path


def test_install_is_atomic_audited_offline_and_persisted(
    tmp_path: Path,
    database: tuple[Any, sessionmaker[Session], Path],
) -> None:
    _, sessions, _ = database
    model_manifest, content = manifest()
    downloader = FakeDownloader(content)
    manager = ModelManager(tmp_path / "models", recorder=SQLAlchemyInstallationRecorder(sessions))
    plan = manager.request_user_install(model_manifest)

    assert plan.estimated_download_bytes == sum(map(len, content.values()))
    assert plan.installed_size_bytes == sum(map(len, content.values()))
    assert plan.available_bytes >= plan.required_free_bytes
    assert plan.environment["torch"] == "2.8.0"

    result = manager.install_confirmed(
        plan.confirmation_token,
        downloader=downloader,
        health_audio=write_health_wav(tmp_path / "health.wav"),
        health_check=healthy,
    )
    assert downloader.calls == 1
    assert manager.resolve_for_runtime("moss-test") == result.local_path
    audit = json.loads((result.local_path / AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert audit["manifest"]["revision"] == REVISION_A
    assert audit["source"] == "fixture://offline-model-store"
    assert audit["aggregate_sha256"] == result.aggregate_sha256
    assert audit["manifest"]["files"][1]["path"] == "code/modeling.py"
    assert audit["health_audio"]["frames"] == 3_200
    assert audit["health_audio"]["sample_rate"] == 16_000
    assert audit["health_check"]["healthy"] is True
    assert audit["health_check"]["measured_vram_mb"] == 6120

    with sessions() as session:
        record = session.scalar(select(ModelInstallation))
        assert record is not None
        assert record.revision == REVISION_A
        assert record.health_status is ModelHealth.HEALTHY
        assert record.measured_vram_mb == 6120
        assert record.environment_json["observed"] == {"gpu": "fixture"}


def test_install_requires_fresh_single_use_user_confirmation(tmp_path: Path) -> None:
    model_manifest, content = manifest()
    manager = ModelManager(tmp_path / "models")
    audio = write_health_wav(tmp_path / "health.wav")
    with pytest.raises(ClassScribeError) as unconfirmed:
        manager.install_confirmed(
            "invented", downloader=FakeDownloader(content), health_audio=audio, health_check=healthy
        )
    assert unconfirmed.value.code is ErrorCode.MODEL_INSTALL_NOT_USER_INITIATED

    plan = manager.request_user_install(model_manifest)
    manager.install_confirmed(
        plan.confirmation_token,
        downloader=FakeDownloader(content),
        health_audio=audio,
        health_check=healthy,
    )
    with pytest.raises(ClassScribeError) as reused:
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(content),
            health_audio=audio,
            health_check=healthy,
        )
    assert reused.value.code is ErrorCode.MODEL_INSTALL_NOT_USER_INITIATED


def test_install_confirmation_is_bound_to_model_and_upstream_terms(tmp_path: Path) -> None:
    model_manifest, content = manifest()
    manager = ModelManager(tmp_path / "models")
    audio = write_health_wav(tmp_path / "health.wav")
    plan = manager.request_user_install(model_manifest)
    with pytest.raises(ClassScribeError, match="different model"):
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(content),
            health_audio=audio,
            health_check=healthy,
            expected_model_id="another-model",
        )

    gated = replace(model_manifest, requires_terms_acceptance=True)
    plan = manager.request_user_install(gated)
    with pytest.raises(ClassScribeError, match="access terms"):
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(content),
            health_audio=audio,
            health_check=healthy,
        )
    plan = manager.request_user_install(gated)
    result = manager.install_confirmed(
        plan.confirmation_token,
        downloader=FakeDownloader(content),
        health_audio=audio,
        health_check=healthy,
        terms_accepted=True,
        expected_model_id="moss-test",
    )
    assert result.model_id == "moss-test"


@pytest.mark.parametrize("bad_revision", ["main", "v1", "A" * 40, "a" * 39])
def test_manifest_rejects_unpinned_revision(bad_revision: str) -> None:
    with pytest.raises(ClassScribeError) as raised:
        manifest(bad_revision)
    assert raised.value.code is ErrorCode.MODEL_REVISION_NOT_PINNED


def test_resolved_revision_and_hash_mismatch_never_publish(tmp_path: Path) -> None:
    model_manifest, content = manifest()
    audio = write_health_wav(tmp_path / "health.wav")
    manager = ModelManager(tmp_path / "models")

    plan = manager.request_user_install(model_manifest)
    with pytest.raises(ClassScribeError) as wrong_revision:
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(content, resolved_revision=REVISION_B),
            health_audio=audio,
            health_check=healthy,
        )
    assert wrong_revision.value.code is ErrorCode.MODEL_REVISION_NOT_PINNED

    plan = manager.request_user_install(model_manifest)
    corrupted = {**content, "weights/model.bin": b"tampered"}
    with pytest.raises(ClassScribeError) as bad_hash:
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(corrupted),
            health_audio=audio,
            health_check=healthy,
        )
    assert bad_hash.value.code is ErrorCode.MODEL_INTEGRITY_FAILED
    assert not (tmp_path / "models" / "moss-test" / "active.json").exists()
    assert not list((tmp_path / "models" / ".staging").iterdir())


def test_health_failure_removes_candidate_and_restores_previous_active(tmp_path: Path) -> None:
    first, first_content = manifest()
    second, second_content = manifest(
        REVISION_B, model=b"model-b", code=b"def load(): return 'b'\n"
    )
    manager = ModelManager(tmp_path / "models")
    audio = write_health_wav(tmp_path / "health.wav")
    first_path = install(manager, first, first_content, audio)

    plan = manager.request_user_install(second)
    with pytest.raises(ClassScribeError) as failed:
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(second_content),
            health_audio=audio,
            health_check=lambda *_: HealthCheckOutcome(False, None, "no transcript", {}),
        )
    assert failed.value.code is ErrorCode.MODEL_HEALTH_CHECK_FAILED
    assert manager.resolve_for_runtime("moss-test") == first_path
    assert not (tmp_path / "models" / "moss-test" / "revisions" / REVISION_B).exists()


def test_upgrade_diff_rollback_and_delete(tmp_path: Path) -> None:
    first, first_content = manifest()
    second, second_content = manifest(
        REVISION_B, model=b"model-b", code=b"def load(): return 'b'\n"
    )
    manager = ModelManager(tmp_path / "models")
    audio = write_health_wav(tmp_path / "health.wav")
    first_path = install(manager, first, first_content, audio)

    diff = manager.diff_upgrade("moss-test", second)
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.changed == ("code/modeling.py", "weights/model.bin")
    assert diff.remote_code_changed == ("code/modeling.py",)

    second_path = install(manager, second, second_content, audio)
    assert manager.resolve_for_runtime("moss-test") == second_path
    with pytest.raises(ClassScribeError) as active_delete:
        manager.delete_revision("moss-test", REVISION_B)
    assert active_delete.value.code is ErrorCode.MODEL_REVISION_ACTIVE

    assert manager.rollback("moss-test", REVISION_A) == first_path
    manager.delete_revision("moss-test", REVISION_B)
    assert not second_path.exists()


def test_runtime_missing_or_tampered_files_fail_without_downloader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(tmp_path / "models")

    def network_forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("runtime attempted to create a network socket")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    with pytest.raises(ClassScribeError) as missing:
        manager.resolve_for_runtime("moss-test")
    assert missing.value.code is ErrorCode.MODEL_NOT_FULLY_INSTALLED

    model_manifest, content = manifest()
    audio = write_health_wav(tmp_path / "health.wav")
    installed = install(manager, model_manifest, content, audio)
    (installed / "weights/model.bin").unlink()
    with pytest.raises(ClassScribeError) as incomplete:
        manager.resolve_for_runtime("moss-test")
    assert incomplete.value.code is ErrorCode.MODEL_NOT_FULLY_INSTALLED


def test_remote_code_snapshot_and_health_audio_contracts_are_strict(tmp_path: Path) -> None:
    base, _ = manifest()
    with pytest.raises(ValueError, match="hashed code"):
        replace(
            base,
            files=tuple(item for item in base.files if item.kind != "remote_code"),
            installed_size_bytes=sum(
                item.size_bytes for item in base.files if item.kind != "remote_code"
            ),
        )

    manager = ModelManager(tmp_path / "models")
    plan = manager.request_user_install(base)
    invalid_audio = tmp_path / "not-a-wave.wav"
    invalid_audio.write_text("not audio", encoding="utf-8")
    with pytest.raises(ValueError, match="PCM WAV"):
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader({}),
            health_audio=invalid_audio,
            health_check=healthy,
        )


def test_runtime_environment_overrides_online_values() -> None:
    assert runtime_environment(
        {"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "false", "KEEP": "yes"}
    ) == {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "KEEP": "yes",
    }


def test_model_control_directories_reject_symlinks(tmp_path: Path) -> None:
    model_manifest, content = manifest()
    manager = ModelManager(tmp_path / "models")
    outside = tmp_path / "outside"
    outside.mkdir()
    (manager.root / "moss-test").symlink_to(outside, target_is_directory=True)
    plan = manager.request_user_install(model_manifest)
    with pytest.raises(ValueError, match="symlink"):
        manager.install_confirmed(
            plan.confirmation_token,
            downloader=FakeDownloader(content),
            health_audio=write_health_wav(tmp_path / "health.wav"),
            health_check=healthy,
        )
    assert not list(outside.iterdir())
