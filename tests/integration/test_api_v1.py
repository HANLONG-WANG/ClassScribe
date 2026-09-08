from __future__ import annotations

import asyncio
import hashlib
import json
import wave
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock
from urllib.parse import urlencode, urlsplit

import pytest
from classscribe.api.app import create_app
from classscribe.api.schemas import ProfileUpdate
from classscribe.api.service import ClassScribeService
from classscribe.contracts import LanguageMode
from classscribe.db import create_schema, create_sqlite_engine, make_session_factory
from classscribe.db.models import (
    ASRCandidate,
    BenchmarkRun,
    BenchmarkStatus,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.diagnostics import DiagnosticSnapshot
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.models import (
    DownloadReceipt,
    HealthCheckOutcome,
    LoadedManifestBundle,
    ManifestBundleIndex,
    ManifestFile,
    ModelLicense,
    ModelManager,
    ModelManifest,
    SQLAlchemyInstallationRecorder,
    load_registry,
)
from classscribe.paths import AppPaths
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: dict[str, str]
    content: bytes

    def json(self) -> Any:
        return json.loads(self.content or b"{}")


async def request(
    app: ASGIApp,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    query: dict[str, str] | None = None,
) -> Response:
    parsed = urlsplit(path)
    query_string = urlencode(query or {}).encode("ascii")
    request_headers = {"Host": "127.0.0.1:8765", **(headers or {})}
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode("ascii"),
            "query_string": query_string,
            "root_path": "",
            "headers": [
                (name.lower().encode("ascii"), value.encode("utf-8"))
                for name, value in request_headers.items()
            ],
            "client": ("127.0.0.1", 50_000),
            "server": ("127.0.0.1", 8765),
        },
    )
    received = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, cast(Receive, receive), cast(Send, send))
    start = next(item for item in messages if item["type"] == "http.response.start")
    content = b"".join(
        item.get("body", b"") for item in messages if item["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1") for key, value in start["headers"]
    }
    return Response(start["status"], response_headers, content)


def service_fixture(
    tmp_path: Path,
    *,
    model_downloader: Any = None,
    model_health_check: Any = None,
    manifest_bundle: LoadedManifestBundle | None = None,
    model_licenses: dict[str, ModelLicense] | None = None,
) -> tuple[ClassScribeService, Any]:
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    engine = create_sqlite_engine(tmp_path / "classscribe.sqlite3")
    create_schema(engine)
    sessions = make_session_factory(engine)
    registry = load_registry(Path("config/model-registry.v1.yaml"))
    manager = ModelManager(
        paths.cache / "models", recorder=SQLAlchemyInstallationRecorder(sessions)
    )
    return (
        ClassScribeService(
            sessions,
            paths,
            registry,
            model_manager=manager,
            model_downloader=model_downloader,
            model_health_check=model_health_check,
            manifest_bundle=manifest_bundle,
            model_licenses=model_licenses,
        ),
        sessions,
    )


class FixtureModelDownloader:
    def __init__(
        self,
        content: dict[str, bytes],
        repository: str = "OpenMOSS-Team/MOSS-Transcribe-Diarize",
    ) -> None:
        self.content = content
        self.repository = repository
        self.calls = 0

    def download(
        self,
        *,
        repository: str,
        revision: str,
        destination: Path,
        files: tuple[ManifestFile, ...],
    ) -> DownloadReceipt:
        assert repository == self.repository
        assert {item.path for item in files} == self.content.keys()
        self.calls += 1
        for relative, content in self.content.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return DownloadReceipt(
            revision, "https://huggingface.co/fixture", sum(map(len, self.content.values()))
        )


def injected_bundle(manifest: ModelManifest) -> LoadedManifestBundle:
    manifest_bytes = (
        json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    return LoadedManifestBundle(
        index=ManifestBundleIndex.model_validate(
            {
                "schema_version": 1,
                "registry_revision": 1,
                "facts_as_of": "2026-09-03",
                "generated_at": "2026-09-05T00:00:00Z",
                "generator": {
                    "name": "classscribe-model-manifest",
                    "version": "1",
                    "lock_sha256": "0" * 64,
                },
                "manifests": [
                    {
                        "model_id": manifest.model_id,
                        "path": f"{manifest.model_id}.json",
                        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    }
                ],
            }
        ),
        manifests=(manifest,),
    )


def registry_fixture_manifest(
    model_id: str,
    content: dict[str, bytes],
    *,
    requires_terms_acceptance: bool = False,
) -> ModelManifest:
    entry = load_registry(Path("config/model-registry.v1.yaml")).model(model_id)
    return ModelManifest(
        manifest_version=1,
        model_id=entry.id,
        repository=entry.repository,
        revision=entry.revision,
        worker=entry.worker,
        license_id="CC-BY-4.0" if requires_terms_acceptance else "Apache-2.0",
        license_url=f"https://huggingface.co/{entry.repository}",
        requires_terms_acceptance=requires_terms_acceptance,
        trust_remote_code=entry.trust_remote_code,
        estimated_download_bytes=sum(map(len, content.values())),
        installed_size_bytes=sum(map(len, content.values())),
        environment={"worker_lock": "pinned"},
        files=tuple(
            ManifestFile(
                path=path,
                sha256=hashlib.sha256(value).hexdigest(),
                size_bytes=len(value),
                kind="remote_code" if entry.trust_remote_code else "config",
            )
            for path, value in content.items()
        ),
    )


def test_model_install_preflight_uses_only_injected_bundle_manifest(tmp_path: Path) -> None:
    manifest_bytes = Path("config/model-manifests/v1/whisper_tiny_reference.json").read_bytes()
    manifest = ModelManifest.from_dict(json.loads(manifest_bytes))
    index = ManifestBundleIndex.model_validate(
        {
            "schema_version": 1,
            "registry_revision": 1,
            "facts_as_of": "2026-09-03",
            "generated_at": "2026-09-05T00:00:00Z",
            "generator": {
                "name": "classscribe-model-manifest",
                "version": "1",
                "lock_sha256": "0" * 64,
            },
            "manifests": [
                {
                    "model_id": manifest.model_id,
                    "path": f"{manifest.model_id}.json",
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                }
            ],
        }
    )
    service, _ = service_fixture(
        tmp_path,
        manifest_bundle=LoadedManifestBundle(index=index, manifests=(manifest,)),
    )
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)

    async def request_preflight(body: bytes = b"{}") -> Response:
        return await request(
            app,
            "POST",
            "/api/v1/models/whisper_tiny_reference/install",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=body,
        )

    replacement = manifest.as_dict()
    replacement["repository"] = "attacker/replacement"
    replacement["revision"] = "0" * 40
    rejected = asyncio.run(request_preflight(json.dumps({"manifest": replacement}).encode()))
    assert rejected.status == 422
    assert "extra_forbidden" in rejected.content.decode()
    manifest_response = asyncio.run(
        request(
            app,
            "GET",
            "/api/v1/models/whisper_tiny_reference/manifest",
            headers=auth(token, csrf),
        )
    )
    assert manifest_response.status == 200
    assert manifest_response.json() == {
        "model_id": manifest.model_id,
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "manifest": manifest.as_dict(),
    }
    assert "confirmation_token" not in manifest_response.content.decode()
    assert "HF_TOKEN" not in manifest_response.content.decode()
    assert str(tmp_path).encode() not in manifest_response.content
    models_response = asyncio.run(request(app, "GET", "/api/v1/models", headers=auth(token, csrf)))
    models_by_id = {item["id"]: item for item in models_response.json()}
    assert models_by_id["whisper_tiny_reference"]["manifest_available"] is True
    assert (
        models_by_id["whisper_tiny_reference"]["manifest_sha256"]
        == hashlib.sha256(manifest_bytes).hexdigest()
    )
    assert (
        models_by_id["whisper_tiny_reference"]["estimated_download_bytes"]
        == manifest.estimated_download_bytes
    )
    assert (
        models_by_id["whisper_tiny_reference"]["installed_size_bytes"]
        == manifest.installed_size_bytes
    )
    assert models_by_id["whisper_tiny_reference"]["remote_code_file_count"] == 0
    assert models_by_id["moss_td_0_9b"]["manifest_available"] is False
    assert models_by_id["moss_td_0_9b"]["manifest_sha256"] is None
    assert models_by_id["moss_td_0_9b"]["estimated_download_bytes"] is None
    assert models_by_id["moss_td_0_9b"]["installed_size_bytes"] is None
    assert models_by_id["moss_td_0_9b"]["remote_code_file_count"] is None
    assert models_by_id["moss_td_0_9b"]["enabled"] is True
    assert models_by_id["moss_td_0_9b"]["worker_implemented"] is True
    assert models_by_id["whisper_tiny_reference"]["worker_implemented"] is True
    assert models_by_id["whisper_tiny_reference"]["installable"] is False
    assert (
        models_by_id["whisper_tiny_reference"]["install_block_reason"] == "registry_policy_disabled"
    )
    assert models_by_id["moss_td_0_9b"]["installable"] is False
    assert models_by_id["moss_td_0_9b"]["install_block_reason"] == "manifest_unavailable"
    assert models_by_id["firered_asr2_llm"]["worker_implemented"] is False
    assert models_by_id["granite_speech_5_0_turboctc_470m"]["worker_implemented"] is False
    assert models_by_id["granite_speech_5_0_turboctc_470m"]["enabled"] is True
    assert (
        models_by_id["granite_speech_5_0_turboctc_470m"]["install_block_reason"]
        == "worker_not_implemented"
    )
    assert models_by_id["voxtral_mini_4b_realtime_2602"]["worker_implemented"] is False
    assert models_by_id["vibevoice_asr_streaming_1_5b"]["worker_implemented"] is False
    response = asyncio.run(request_preflight())
    assert response.status == 409
    assert response.json()["error"] == {
        "code": ErrorCode.MODEL_INSTALL_BLOCKED.value,
        "detail": "model is not ordinarily installable: registry_policy_disabled",
    }
    with pytest.raises(ClassScribeError, match="manifest_unavailable") as failure:
        service.request_bundled_model_install("moss_td_0_9b")
    assert failure.value.code is ErrorCode.MODEL_INSTALL_BLOCKED


def test_injected_bundle_drives_complete_installation_transaction(tmp_path: Path) -> None:
    content = {"code/modeling.py": b"MODEL = 'bundle-fixture'\n", "config.json": b"{}"}
    manifest = ModelManifest.from_dict(
        {
            "manifest_version": 1,
            "model_id": "moss_td_0_9b",
            "repository": "OpenMOSS-Team/MOSS-Transcribe-Diarize",
            "revision": "704aa4a9c304e8520be88901e0d1960158ef5b15",
            "worker": "moss_td",
            "license_id": "Apache-2.0",
            "license_url": "https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize",
            "requires_terms_acceptance": False,
            "trust_remote_code": True,
            "estimated_download_bytes": sum(map(len, content.values())),
            "installed_size_bytes": sum(map(len, content.values())),
            "environment": {"worker_lock": "pinned"},
            "files": [
                {
                    "path": path,
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "size_bytes": len(value),
                    "kind": "remote_code" if path.startswith("code/") else "config",
                }
                for path, value in content.items()
            ],
        }
    )
    manifest_bytes = (
        json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    bundle = LoadedManifestBundle(
        index=ManifestBundleIndex.model_validate(
            {
                "schema_version": 1,
                "registry_revision": 1,
                "facts_as_of": "2026-09-03",
                "generated_at": "2026-09-05T00:00:00Z",
                "generator": {
                    "name": "classscribe-model-manifest",
                    "version": "1",
                    "lock_sha256": "0" * 64,
                },
                "manifests": [
                    {
                        "model_id": manifest.model_id,
                        "path": f"{manifest.model_id}.json",
                        "sha256": manifest_sha256,
                    }
                ],
            }
        ),
        manifests=(manifest,),
    )
    lifecycle: list[str] = []

    class BundleDownloader(FixtureModelDownloader):
        def download(
            self,
            *,
            repository: str,
            revision: str,
            destination: Path,
            files: tuple[ManifestFile, ...],
        ) -> DownloadReceipt:
            lifecycle.append("download")
            return super().download(
                repository=repository,
                revision=revision,
                destination=destination,
                files=files,
            )

    def health_check(
        _entry: Any,
        model: Path,
        audio: Path,
        environment: dict[str, str],
        language: str,
        transcript: str | None,
    ) -> HealthCheckOutcome:
        lifecycle.append("health")
        assert {path: (model / path).read_bytes() for path in content} == content
        assert audio.read_bytes().startswith(b"RIFF")
        assert environment["HF_HUB_OFFLINE"] == "1"
        assert environment["TRANSFORMERS_OFFLINE"] == "1"
        assert environment["HF_DATASETS_OFFLINE"] == "1"
        assert language == "ja"
        assert transcript == "bundle health transcript"
        return HealthCheckOutcome(True, 4096, "bundle health inference completed", {"gpu": "test"})

    service, _ = service_fixture(
        tmp_path,
        model_downloader=BundleDownloader(content),
        model_health_check=health_check,
        manifest_bundle=bundle,
    )
    xdg_roots = (
        service.paths.config,
        service.paths.data,
        service.paths.cache,
        service.paths.runtime,
    )

    def user_manifest_files() -> list[Path]:
        return [
            path
            for root in xdg_roots
            for path in root.rglob("*")
            if path.is_file() and "manifest" in path.name
        ]

    assert all(root.is_relative_to(tmp_path / "home") for root in xdg_roots)
    assert user_manifest_files() == []
    recording = service.create_recording(
        source_name="health.wav",
        content=health_wav_bytes(),
        duration_samples=3_200,
        channels=1,
        sample_rate=16_000,
    )

    planned = service.request_bundled_model_install(manifest.model_id)
    assert planned["manifest_sha256"] == manifest_sha256
    confirmed = service.confirm_model_install(
        manifest.model_id,
        confirmation_token=planned["confirmation_token"],
        health_recording_id=recording["id"],
        health_language="ja",
        health_transcript="bundle health transcript",
        terms_accepted=False,
    )

    assert lifecycle == ["download", "health"]
    assert confirmed["installed"] is True
    assert confirmed["revision"] == manifest.revision
    assert confirmed["health"]["healthy"] is True
    manager = service.model_manager
    assert manager is not None
    active = manager.resolve_for_runtime(manifest.model_id)
    active_pointer = json.loads((active.parent.parent / "active.json").read_text())
    assert active_pointer["revision"] == manifest.revision
    audit = json.loads((active / "supply-chain.json").read_text())
    assert audit["manifest"] == manifest.as_dict()
    assert audit["health_check"]["healthy"] is True
    assert service.verify_model(manifest.model_id)["verified"] is True
    assert user_manifest_files() == []

    # A healthy weight audit must not hide stale worker sources or trigger a redownload.
    from classscribe.models.environment import WorkerEnvironmentProvisioner

    provisioner = Mock(spec=WorkerEnvironmentProvisioner)
    provisioner.inspect.return_value = {"worker_id": manifest.worker, "status": "source_changed"}
    provisioner.resolve.side_effect = ClassScribeError(
        ErrorCode.MODEL_HEALTH_CHECK_FAILED, "source_changed"
    )
    service.worker_environments = provisioner
    listed = next(item for item in service.models() if item["id"] == manifest.model_id)
    assert listed["worker_environment"]["status"] == "source_changed"
    with pytest.raises(ClassScribeError, match="source_changed"):
        service.verify_model(manifest.model_id)
    provisioner.resolve.side_effect = None
    provisioner.inspect.return_value = {"worker_id": manifest.worker, "status": "ready"}
    repaired = service.repair_model_environment(manifest.model_id)
    assert repaired["repaired"] is True
    provisioner.ensure.assert_called_once_with(manifest.worker, repair=True)
    assert lifecycle == ["download", "health"]
    assert manager.resolve_for_runtime(manifest.model_id) == active


def test_aligner_confirmation_requires_exact_text_before_download(tmp_path: Path) -> None:
    content = {"config.json": b"{}"}
    manifest = registry_fixture_manifest("qwen3_forced_aligner_0_6b", content)
    downloader = FixtureModelDownloader(content, manifest.repository)
    observed: dict[str, str | None] = {}

    def health_check(
        _entry: Any,
        _model: Path,
        _audio: Path,
        _environment: dict[str, str],
        language: str,
        transcript: str | None,
    ) -> HealthCheckOutcome:
        observed.update(language=language, transcript=transcript)
        return HealthCheckOutcome(True, 1024, "aligner health completed", {})

    service, _ = service_fixture(
        tmp_path,
        model_downloader=downloader,
        model_health_check=health_check,
        manifest_bundle=injected_bundle(manifest),
    )
    recording = service.create_recording(
        source_name="health.wav",
        content=health_wav_bytes(),
        duration_samples=3_200,
        channels=1,
        sample_rate=16_000,
    )
    plan = service.request_bundled_model_install(manifest.model_id)

    with pytest.raises(ClassScribeError, match="exact transcript") as missing:
        service.confirm_model_install(
            manifest.model_id,
            confirmation_token=plan["confirmation_token"],
            health_recording_id=recording["id"],
            health_language="ja",
            health_transcript=None,
            terms_accepted=False,
        )
    assert missing.value.code is ErrorCode.MODEL_HEALTH_CHECK_FAILED
    assert downloader.calls == 0

    confirmed = service.confirm_model_install(
        manifest.model_id,
        confirmation_token=plan["confirmation_token"],
        health_recording_id=recording["id"],
        health_language="ja",
        health_transcript="正確なアラインメント文字列",
        terms_accepted=False,
    )
    assert confirmed["installed"] is True
    assert observed == {"language": "ja", "transcript": "正確なアラインメント文字列"}
    assert downloader.calls == 1


def test_gated_confirmation_requires_terms_before_download(tmp_path: Path) -> None:
    content = {"config.json": b"{}"}
    manifest = registry_fixture_manifest(
        "pyannote_community_1", content, requires_terms_acceptance=True
    )
    downloader = FixtureModelDownloader(content, manifest.repository)

    def health_check(*_args: Any) -> HealthCheckOutcome:
        return HealthCheckOutcome(True, 2048, "gated health completed", {})

    service, _ = service_fixture(
        tmp_path,
        model_downloader=downloader,
        model_health_check=health_check,
        manifest_bundle=injected_bundle(manifest),
    )
    recording = service.create_recording(
        source_name="health.wav",
        content=health_wav_bytes(),
        duration_samples=3_200,
        channels=1,
        sample_rate=16_000,
    )
    plan = service.request_bundled_model_install(manifest.model_id)

    with pytest.raises(ClassScribeError, match="access terms") as rejected:
        service.confirm_model_install(
            manifest.model_id,
            confirmation_token=plan["confirmation_token"],
            health_recording_id=recording["id"],
            health_language="en",
            health_transcript=None,
            terms_accepted=False,
        )
    assert rejected.value.code is ErrorCode.MODEL_INSTALL_NOT_USER_INITIATED
    assert downloader.calls == 0

    accepted = service.request_bundled_model_install(manifest.model_id)
    confirmed = service.confirm_model_install(
        manifest.model_id,
        confirmation_token=accepted["confirmation_token"],
        health_recording_id=recording["id"],
        health_language="en",
        health_transcript=None,
        terms_accepted=True,
    )
    assert confirmed["installed"] is True
    assert downloader.calls == 1


def test_model_install_error_response_redacts_credentials_and_local_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _pipeline = service_fixture(tmp_path)

    def fail_install(_model_id: str) -> dict[str, Any]:
        raise ClassScribeError(
            ErrorCode.MODEL_HEALTH_CHECK_FAILED,
            "Authorization: Bearer server-secret hf_abcdefghijklmnop failed at "
            "/home/private-user/models/revision/config.json",
        )

    monkeypatch.setattr(service, "request_bundled_model_install", fail_install)
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/models/moss_td_0_9b/install",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=b"{}",
        )
    )

    assert response.status == 409
    assert response.json()["error"] == {
        "code": ErrorCode.MODEL_HEALTH_CHECK_FAILED.value,
        "detail": "[REDACTED] [REDACTED] failed at [LOCAL_PATH]",
    }
    serialized = response.content.decode()
    for unsafe in (
        "Authorization",
        "server-secret",
        "hf_abcdefghijklmnop",
        "/home/private-user/models/revision/config.json",
    ):
        assert unsafe not in serialized


def test_recordings_collection_supports_health_audio_selection_without_paths(
    tmp_path: Path,
) -> None:
    service, _pipeline = service_fixture(tmp_path)
    created = service.create_recording(
        source_name="health.wav",
        content=health_wav_bytes(),
        duration_samples=3_200,
        channels=1,
        sample_rate=16_000,
    )
    invalid = service.create_recording(
        source_name="spoofed.wav",
        content=b"RIFFtest",
        duration_samples=3_200,
        channels=1,
        sample_rate=16_000,
    )
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)

    response = asyncio.run(request(app, "GET", "/api/v1/recordings", headers=auth(token, csrf)))

    assert response.status == 200
    rows = {item["id"]: item for item in response.json()}
    assert rows.keys() == {created["id"], invalid["id"]}
    assert rows[created["id"]]["audio_qc"]["health_wav_eligible"] is True
    assert rows[invalid["id"]]["audio_qc"]["health_wav_eligible"] is False
    serialized = response.content.decode()
    assert "source_path" not in serialized
    assert str(tmp_path) not in serialized


def test_health_recording_must_be_server_verified_before_confirmation(
    tmp_path: Path,
) -> None:
    content = {"config.json": b"{}"}
    manifest = registry_fixture_manifest("moss_td_0_9b", content)
    downloader = FixtureModelDownloader(content)

    def health_check(*_args: Any) -> HealthCheckOutcome:
        return HealthCheckOutcome(True, None, "valid health audio", {})

    service, _pipeline = service_fixture(
        tmp_path,
        model_downloader=downloader,
        model_health_check=health_check,
        manifest_bundle=injected_bundle(manifest),
    )
    invalid = service.create_recording(
        source_name="spoofed.wav",
        content=b"RIFFtest",
        duration_samples=3_200,
        channels=1,
        sample_rate=16_000,
    )
    valid = service.create_recording(
        source_name="health.wav",
        content=health_wav_bytes(),
        duration_samples=3_200,
        channels=1,
        sample_rate=16_000,
    )
    plan = service.request_bundled_model_install(manifest.model_id)

    with pytest.raises(ClassScribeError, match="16 kHz mono 16-bit PCM"):
        service.confirm_model_install(
            manifest.model_id,
            confirmation_token=plan["confirmation_token"],
            health_recording_id=invalid["id"],
            health_language="ja",
            health_transcript=None,
            terms_accepted=False,
        )
    assert downloader.calls == 0

    confirmed = service.confirm_model_install(
        manifest.model_id,
        confirmation_token=plan["confirmation_token"],
        health_recording_id=valid["id"],
        health_language="ja",
        health_transcript=None,
        terms_accepted=False,
    )
    assert confirmed["installed"] is True
    assert downloader.calls == 1


def health_wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x01\x00" * 3_200)
    return output.getvalue()


def auth(token: str, csrf: str, *, write: bool = False) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token}"}
    if write:
        result["X-ClassScribe-CSRF-Token"] = csrf
    return result


def test_profile_application_requires_exact_passed_ranking_and_can_rollback(
    tmp_path: Path,
) -> None:
    service, sessions = service_fixture(tmp_path)
    ranking = ["qwen3_asr_1_7b"]
    entry = service.registry.model(ranking[0])
    calibration = {
        "schema_version": 1,
        "model_id": entry.id,
        "model_revision": entry.revision,
        "language": "ja",
        "scenario": "ibus",
        "manifest_sha256": "a" * 64,
        "method": "isotonic",
        "error_threshold": 0.2,
        "parameters": {"thresholds": [1.0], "values": [0.9]},
        "validation_brier": 0.01,
        "test_brier": 0.02,
        "training_sha256": "b" * 64,
        "created_at": "2026-09-04T00:00:00+00:00",
    }
    with sessions.begin() as session:
        run = BenchmarkRun(
            manifest_version="private-gold-v1",
            status=BenchmarkStatus.COMPLETED,
            hardware_json={"gpu": "local"},
            parameters_json={
                "offline": True,
                "production_gold": True,
                "real_model_execution": True,
                "synthetic_gold": False,
                "manifest_sha256": "a" * 64,
                "calibrations": [calibration],
            },
            metrics_json={},
            ranking_json=[
                {
                    "language": "ja",
                    "scenario": "ibus",
                    "models": ranking,
                    "passed": True,
                    "candidates": [
                        {
                            "model_id": entry.id,
                            "model_revision": entry.revision,
                            "eligible": True,
                            "metrics": {"normalized_cer": 0.1},
                        }
                    ],
                }
            ],
        )
        session.add(run)
        session.flush()
        run_id = run.id
    applied = service.update_profile(
        "ja",
        "ibus.balanced",
        ProfileUpdate(
            primary_model_id=ranking[0],
            fallback_model_ids=(),
            benchmark_run_id=run_id,
        ),
    )
    assert applied["models"] == ranking and applied["source"] == "local_benchmark"
    restored = service.rollback_profile("ja", "ibus.balanced")
    assert restored["source"] == "bootstrap"
    assert restored["models"] == ["qwen3_asr_1_7b"]


def test_openapi_contains_the_complete_version_one_contract(tmp_path: Path) -> None:
    service, _ = service_fixture(tmp_path)
    app = create_app(api_token="t" * 43, csrf_token="c" * 43, service=service)

    async def scenario() -> None:
        response = await request(app, "GET", "/openapi.json")
        assert response.status == 200
        paths = response.json()["paths"]
        required = {
            "/api/v1/recordings",
            "/api/v1/jobs",
            "/api/v1/jobs/{job_id}/events",
            "/api/v1/jobs/{job_id}/retry-segment/{segment_id}",
            "/api/v1/jobs/{job_id}/transcript",
            "/api/v1/segments/{segment_id}",
            "/api/v1/segments/{segment_id}/candidates",
            "/api/v1/segments/{segment_id}/split",
            "/api/v1/segments/{segment_id}/merge",
            "/api/v1/models",
            "/api/v1/profiles",
            "/api/v1/settings",
            "/api/v1/glossaries",
            "/api/v1/jobs/{job_id}/exports",
            "/api/v1/exports/{export_id}",
            "/api/v1/benchmarks",
            "/api/v1/benchmarks/{benchmark_id}/apply-ranking",
            "/api/v1/diagnostics",
            "/api/v1/diagnostics/bundle",
            "/api/v1/ibus/status",
        }
        assert required <= paths.keys()

    asyncio.run(scenario())


def test_authenticated_diagnostic_bundle_contains_only_redacted_json(tmp_path: Path) -> None:
    service, _ = service_fixture(tmp_path)
    app = create_app(
        api_token="t" * 43,
        csrf_token="c" * 43,
        service=service,
        diagnostic_provider=lambda: DiagnosticSnapshot(
            generated_at="2026-09-04T00:00:00+00:00",
            system={"home": str(Path.home())},
            gpu={},
            components=(),
            recent_errors=({"raw_text": "private classroom text"},),
        ),
    )

    async def scenario() -> None:
        denied = await request(app, "GET", "/api/v1/diagnostics/bundle")
        assert denied.status == 401
        response = await request(
            app,
            "GET",
            "/api/v1/diagnostics/bundle",
            headers=auth("t" * 43, "c" * 43),
        )
        assert response.status == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            content = archive.read("diagnostics.json").decode("utf-8")
        assert "private classroom text" not in content
        assert str(Path.home()) not in content
        assert "[REDACTED]" in content

    asyncio.run(scenario())


def test_built_webui_is_served_without_shadowing_versioned_api(tmp_path: Path) -> None:
    service, _ = service_fixture(tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    token = "t" * 43
    (web / "index.html").write_text(
        '<meta name="classscribe-api-token" content="__CLASSSCRIBE_API_TOKEN__">'
        "<h1>ClassScribe workbench</h1>",
        encoding="utf-8",
    )
    app = create_app(
        api_token=token,
        csrf_token="c" * 43,
        service=service,
        static_directory=web,
    )

    async def scenario() -> None:
        root = await request(app, "GET", "/")
        assert root.status == 200
        assert "ClassScribe workbench" in root.content.decode("utf-8")
        assert f'content="{token}"' in root.content.decode("utf-8")
        assert "__CLASSSCRIBE_API_TOKEN__" not in root.content.decode("utf-8")
        assert root.headers["cache-control"] == "no-store"
        explicit_index = await request(app, "GET", "/index.html")
        assert explicit_index.status == 200
        assert explicit_index.content == root.content
        models = await request(
            app,
            "GET",
            "/api/v1/models",
            headers=auth(token, "c" * 43),
        )
        assert models.status == 200
        glossaries = await request(
            app,
            "GET",
            "/api/v1/glossaries",
            headers=auth(token, "c" * 43),
        )
        assert glossaries.status == 200

        rejected_host = await request(app, "GET", "/", headers={"Host": "attacker.example"})
        assert rejected_host.status == 403
        assert rejected_host.json()["error"]["code"] == ErrorCode.NON_LOOPBACK_CLIENT.value

    asyncio.run(scenario())


def test_model_install_requires_second_confirmation_and_runs_health_inference(
    tmp_path: Path,
) -> None:
    content = {"code/modeling.py": b"MODEL = 'fixture'\n", "config.json": b"{}"}

    def healthy(
        _entry: Any,
        model: Path,
        audio: Path,
        environment: dict[str, str],
        language: str,
        _transcript: str | None,
    ) -> HealthCheckOutcome:
        assert (model / "config.json").is_file()
        assert audio.read_bytes().startswith(b"RIFF")
        assert environment["HF_HUB_OFFLINE"] == "1"
        assert language == "ja"
        return HealthCheckOutcome(True, 4096, "fixture inference completed", {"gpu": "test"})

    revision = "704aa4a9c304e8520be88901e0d1960158ef5b15"
    manifest = {
        "manifest_version": 1,
        "model_id": "moss_td_0_9b",
        "repository": "OpenMOSS-Team/MOSS-Transcribe-Diarize",
        "revision": revision,
        "worker": "moss_td",
        "license_id": "Apache-2.0",
        "license_url": "https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize",
        "requires_terms_acceptance": False,
        "trust_remote_code": True,
        "estimated_download_bytes": sum(map(len, content.values())),
        "installed_size_bytes": sum(map(len, content.values())),
        "environment": {"worker_lock": "pinned"},
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
                "kind": "remote_code" if path.startswith("code/") else "config",
            }
            for path, value in content.items()
        ],
        "component_sources": [
            {
                "repository": "upstream/component",
                "revision": "c" * 40,
                "relationship": "derived",
                "license_id": "CC-BY-4.0",
                "license_url": "https://huggingface.co/upstream/component",
                "requires_terms_acceptance": False,
                "files": [
                    {
                        "installed_path": "config.json",
                        "source_path": "source-config.json",
                        "source_sha256": "d" * 64,
                        "source_size_bytes": 7,
                    }
                ],
            }
        ],
    }
    model_manifest = ModelManifest.from_dict(manifest)
    manifest_bytes = (
        json.dumps(model_manifest.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    bundle = LoadedManifestBundle(
        index=ManifestBundleIndex.model_validate(
            {
                "schema_version": 1,
                "registry_revision": 1,
                "facts_as_of": "2026-09-03",
                "generated_at": "2026-09-05T00:00:00Z",
                "generator": {
                    "name": "classscribe-model-manifest",
                    "version": "1",
                    "lock_sha256": "0" * 64,
                },
                "manifests": [
                    {
                        "model_id": model_manifest.model_id,
                        "path": f"{model_manifest.model_id}.json",
                        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    }
                ],
            }
        ),
        manifests=(model_manifest,),
    )
    service, _ = service_fixture(
        tmp_path,
        model_downloader=FixtureModelDownloader(content),
        model_health_check=healthy,
        manifest_bundle=bundle,
        model_licenses={
            cast(str, manifest["repository"]): ModelLicense.model_validate(
                {
                    "repository": manifest["repository"],
                    "license_id": manifest["license_id"],
                    "license_url": manifest["license_url"],
                    "requires_terms_acceptance": False,
                    "verified_as_of": "2026-09-03",
                }
            ),
            "upstream/component": ModelLicense.model_validate(
                {
                    "repository": "upstream/component",
                    "license_id": "CC-BY-4.0",
                    "license_url": "https://huggingface.co/upstream/component",
                    "requires_terms_acceptance": False,
                    "verified_as_of": "2026-09-03",
                }
            ),
        },
    )
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)

    async def scenario() -> None:
        headers = {**auth(token, csrf, write=True), "Content-Type": "application/json"}
        model_rows = await request(app, "GET", "/api/v1/models", headers=auth(token, csrf))
        model_row = next(item for item in model_rows.json() if item["id"] == manifest["model_id"])
        assert model_row["installable"] is True
        assert model_row["install_block_reason"] is None
        assert model_row["remote_code_file_count"] == 1
        assert model_row["component_source_count"] == 1
        assert model_row["component_sources"][0]["repository"] == "upstream/component"
        audio = health_wav_bytes()
        uploaded = await request(
            app,
            "POST",
            "/api/v1/recordings",
            headers={
                **headers,
                "X-ClassScribe-Filename": "health.wav",
                "X-ClassScribe-Duration-Samples": "3200",
                "X-ClassScribe-Channels": "1",
                "X-ClassScribe-Sample-Rate": "16000",
            },
            body=audio,
        )
        assert uploaded.status == 201
        planned = await request(
            app,
            "POST",
            "/api/v1/models/moss_td_0_9b/install",
            headers=headers,
            body=b"{}",
        )
        assert planned.status == 202
        plan_value = planned.json()
        assert plan_value["license_id"] == "Apache-2.0"
        assert plan_value["license_url"] == model_manifest.license_url
        assert plan_value["requires_terms_acceptance"] is False
        assert plan_value["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
        assert plan_value["estimated_download_bytes"] == sum(map(len, content.values()))
        assert plan_value["installed_size_bytes"] == sum(map(len, content.values()))
        assert plan_value["required_free_bytes"] > sum(map(len, content.values())) * 2
        assert plan_value["available_bytes"] >= plan_value["required_free_bytes"]
        assert plan_value["environment"] == {"worker_lock": "pinned"}
        assert plan_value["remote_code_file_count"] == 1
        assert plan_value["remote_code_files"] == ["code/modeling.py"]
        assert plan_value["component_sources"] == manifest["component_sources"]
        confirmed = await request(
            app,
            "POST",
            "/api/v1/models/moss_td_0_9b/install/confirm",
            headers=headers,
            body=json.dumps(
                {
                    "confirmation_token": plan_value["confirmation_token"],
                    "health_recording_id": uploaded.json()["id"],
                    "health_language": "ja",
                    "health_transcript": "これは健康確認です",
                    "terms_accepted": False,
                }
            ).encode(),
        )
        assert confirmed.status == 200
        assert confirmed.json()["installed"] is True
        assert confirmed.json()["health"]["measured_vram_mb"] == 4096

    asyncio.run(scenario())


def test_recording_transcript_glossary_export_and_security_flow(tmp_path: Path) -> None:
    service, sessions = service_fixture(tmp_path)
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)

    async def scenario() -> None:
        upload_headers = {
            **auth(token, csrf),
            "Content-Type": "application/octet-stream",
            "X-ClassScribe-Filename": "lecture.wav",
            "X-ClassScribe-Duration-Samples": "320000",
            "X-ClassScribe-Channels": "1",
            "X-ClassScribe-Sample-Rate": "16000",
        }
        rejected = await request(
            app, "POST", "/api/v1/recordings", headers=upload_headers, body=b"RIFFtest"
        )
        assert rejected.status == 403
        assert rejected.json()["error"]["code"] == "AUTH_INVALID"

        uploaded = await request(
            app,
            "POST",
            "/api/v1/recordings",
            headers={**upload_headers, "X-ClassScribe-CSRF-Token": csrf},
            body=b"RIFFtest",
        )
        assert uploaded.status == 201
        recording = uploaded.json()
        assert "path" not in recording and recording["media_url"].endswith("/media")

        created = await request(
            app,
            "POST",
            "/api/v1/jobs",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "recording_id": recording["id"],
                    "language": "ja",
                    "speaker_count": "2",
                    "model_selection": "auto_best",
                    "accuracy_mode": "balanced",
                    "outputs": ["json", "md", "srt", "vtt"],
                }
            ).encode(),
        )
        assert created.status == 201
        job_id = created.json()["job_id"]
        with sessions.begin() as session:
            segment = TranscriptSegment(
                job_id=job_id,
                start_sample=0,
                end_sample=320_000,
                speaker_id="teacher",
                language=LanguageMode.JAPANESE,
                raw_text="人工知能について説明します",
                faithful_text="人工知能について説明します。",
                smart_corrected_text="人工知能について説明します。",
                quality_score=0.91,
            )
            session.add(segment)
            session.flush()
            candidate = ASRCandidate(
                segment_id=segment.id,
                model_id="granite_speech_4_1_2b",
                model_revision="a" * 40,
                raw_text="人工知能について説明します",
                normalized_text="人工知能について説明します。",
                confidence_raw=0.9,
                confidence_calibrated=0.91,
                quality_features_json={"accepted": True},
                warnings_json=[],
                decode_config_json={},
                inference_metrics_json={},
            )
            session.add(candidate)
            session.flush()
            session.add(
                TokenSpan(
                    segment_id=segment.id,
                    candidate_id=None,
                    start_sample=0,
                    end_sample=320_000,
                    token="人工知能について説明します。",
                    normalized_token="人工知能について説明します。",
                    confidence=0.91,
                    provenance_json={
                        "candidate_sources": [{"candidate_id": candidate.id}],
                        "timing_source": "native",
                    },
                )
            )
            segment_id, candidate_id = segment.id, candidate.id

        transcript = await request(
            app, "GET", f"/api/v1/jobs/{job_id}/transcript", headers=auth(token, csrf)
        )
        assert transcript.status == 200
        assert transcript.json()["segments"][0]["tokens"][0]["provenance"]

        edited = await request(
            app,
            "PATCH",
            f"/api/v1/segments/{segment_id}",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "version": 1,
                    "layer": "user",
                    "text": "人工知能を説明します。",
                    "speaker_name": "先生",
                }
            ).encode(),
        )
        assert edited.status == 200
        assert edited.json()["user_text"] == "人工知能を説明します。"
        assert edited.json()["speaker_name"] == "先生"
        assert edited.json()["audit"][-1]["actor_type"] == "human"

        candidate_response = await request(
            app,
            "POST",
            f"/api/v1/segments/{segment_id}/adopt-candidate",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=json.dumps(
                {"candidate_id": candidate_id, "version": edited.json()["version"]}
            ).encode(),
        )
        assert candidate_response.status == 200
        assert candidate_response.json()["user_text"] == "人工知能について説明します。"

        glossary = await request(
            app,
            "POST",
            "/api/v1/glossaries",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=b'{"name":"AI 101","course_id":"ai-101"}',
        )
        assert glossary.status == 201
        glossary_id = glossary.json()["id"]
        terms = await request(
            app,
            "PUT",
            f"/api/v1/glossaries/{glossary_id}/terms",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=json.dumps(
                {
                    "terms": [
                        {
                            "canonical": "人工知能",
                            "reading": "じんこうちのう",
                            "language": "ja",
                            "weight": 1.0,
                            "source": "manual",
                            "confirmed": True,
                        }
                    ]
                }
            ).encode(),
        )
        assert terms.status == 200 and terms.json()["terms"][0]["confirmed"]
        material = await request(
            app,
            "POST",
            f"/api/v1/glossaries/{glossary_id}/documents",
            headers={
                **auth(token, csrf, write=True),
                "Content-Type": "application/octet-stream",
                "X-ClassScribe-Filename": "handout.txt",
                "X-ClassScribe-Material-Kind": "txt",
                "X-ClassScribe-Language": "ja",
            },
            body="人工知能 人工知能 MachineLearning".encode(),
        )
        assert material.status == 201
        assert "path" not in material.json() and material.json()["suggestion_count"] > 0

        settings = await request(
            app,
            "PUT",
            "/api/v1/settings",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=b'{"values":{"retention":{"derived_days":30}}}',
        )
        assert settings.json()["retention"]["derived_days"] == 30
        models = await request(app, "GET", "/api/v1/models", headers=auth(token, csrf))
        assert models.status == 200 and len(models.json()) >= 9
        ibus = await request(app, "GET", "/api/v1/ibus/status", headers=auth(token, csrf))
        assert ibus.status == 200
        assert ibus.json()["dictationd"]["available"] is False
        assert ibus.json()["ibus_input_source_fallback"] is True

        exported = await request(
            app,
            "POST",
            f"/api/v1/jobs/{job_id}/exports",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=b'{"output_format":"json","layer":"user","view":"sentences"}',
        )
        assert exported.status == 201
        export = exported.json()
        assert "path" not in export and export["sha256"]
        downloaded = await request(app, "GET", export["download_url"], headers=auth(token, csrf))
        assert downloaded.status == 200
        assert "人工知能について説明します" in downloaded.content.decode()
        assert "先生" in downloaded.content.decode()

        benchmark = await request(
            app,
            "POST",
            "/api/v1/benchmarks",
            headers={**auth(token, csrf, write=True), "Content-Type": "application/json"},
            body=b'{"manifest_version":"gold-v1","parameters":{"scenario":"classroom"}}',
        )
        assert benchmark.status == 202 and benchmark.json()["status"] == "running"

    asyncio.run(scenario())


def test_edit_history_repeated_undo_redo_and_new_branch(tmp_path: Path) -> None:
    from classscribe.api.schemas import SegmentPatch
    from classscribe.db.models import Job, Recording

    service, sessions = service_fixture(tmp_path)
    with sessions.begin() as session:
        job = Job(
            recording=Recording(
                source_name="a",
                source_sha256="a" * 64,
                source_path="a",
                duration_samples=16000,
                sample_rate=16000,
                channels=1,
            ),
            language_mode=LanguageMode.ENGLISH,
            profile_id="en",
        )
        segment = TranscriptSegment(
            job=job, start_sample=0, end_sample=16000, language=LanguageMode.ENGLISH, user_text="A"
        )
        session.add(segment)
        session.flush()
        identifier = segment.id
    value = service.patch_segment(identifier, SegmentPatch(version=1, text="B"))
    value = service.undo_segment(identifier, value["version"])
    assert value["user_text"] == "A"
    value = service.redo_segment(identifier, value["version"])
    assert value["user_text"] == "B"
    value = service.undo_segment(identifier, value["version"])
    assert value["user_text"] == "A"
    value = service.patch_segment(identifier, SegmentPatch(version=value["version"], text="C"))
    with pytest.raises(ClassScribeError, match="nothing to redo"):
        service.redo_segment(identifier, value["version"])
    value = service.undo_segment(identifier, value["version"])
    assert value["user_text"] == "A"


@pytest.mark.parametrize(
    ("filename", "encoded"),
    [
        ("录音 2.wav", True),
        ("授業 🎙️.wav", True),
        ("100% + %E5.wav", True),
        ("lecture.wav", False),
        ("literal%20name.wav", False),
    ],
)
def test_upload_filename_round_trip(tmp_path: Path, filename: str, encoded: bool) -> None:
    from urllib.parse import quote

    service, _sessions = service_fixture(tmp_path)
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)
    audio = BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    headers = {
        **auth(token, csrf, write=True),
        "Content-Type": "application/octet-stream",
        "X-ClassScribe-Filename": quote(filename, safe="") if encoded else filename,
        "X-ClassScribe-Duration-Samples": "1600",
        "X-ClassScribe-Channels": "1",
        "X-ClassScribe-Sample-Rate": "16000",
    }
    if encoded:
        headers["X-ClassScribe-Filename-Encoding"] = "utf-8-percent"
    response = asyncio.run(
        request(app, "POST", "/api/v1/recordings", headers=headers, body=audio.getvalue())
    )
    assert response.status == 201
    assert response.json()["source_name"] == filename
    assert response.json()["audio_qc"]["health_wav_eligible"] is True


def test_delete_glossary_removes_terms_and_materials_without_affecting_others(
    tmp_path: Path,
) -> None:
    from classscribe.api.schemas import GlossaryCreate, GlossaryTermInput, GlossaryTermsUpdate
    from classscribe.db.models import Glossary, GlossaryMaterial, GlossaryTerm
    from classscribe.terminology import TermSource
    from sqlalchemy import select

    service, sessions = service_fixture(tmp_path)
    target = service.create_glossary(GlossaryCreate(name="删除此词典"))
    other = service.create_glossary(GlossaryCreate(name="保留此词典"))
    service.update_terms(
        target["id"],
        GlossaryTermsUpdate(
            terms=(
                GlossaryTermInput(
                    canonical="光合作用",
                    reading="こうごうせい",
                    language="ja",
                    source="manual",
                    confirmed=True,
                ),
            )
        ),
    )
    service.add_glossary_document(
        target["id"],
        source_name="notes.txt",
        source_kind=TermSource.TXT,
        language="ja",
        content=b"notes",
    )
    with sessions() as session:
        material = session.scalar(
            select(GlossaryMaterial).where(GlossaryMaterial.glossary_id == target["id"])
        )
        assert material is not None
        path = service.paths.data_path(material.relative_path)
    assert path.exists()
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)
    endpoint = f"/api/v1/glossaries/{target['id']}"
    rejected = asyncio.run(request(app, "DELETE", endpoint, headers=auth(token, csrf)))
    assert rejected.status == 403
    assert path.exists()
    response = asyncio.run(request(app, "DELETE", endpoint, headers=auth(token, csrf, write=True)))
    assert response.status == 200
    assert response.json()["deleted"] is True
    assert not path.exists()
    with sessions() as session:
        assert session.get(Glossary, target["id"]) is None
        assert session.get(Glossary, other["id"]) is not None
        assert (
            session.scalar(select(GlossaryTerm).where(GlossaryTerm.glossary_id == target["id"]))
            is None
        )
        assert (
            session.scalar(
                select(GlossaryMaterial).where(GlossaryMaterial.glossary_id == target["id"])
            )
            is None
        )
    missing = asyncio.run(request(app, "DELETE", endpoint, headers=auth(token, csrf, write=True)))
    assert missing.status == 404


def test_install_rejects_language_before_download_and_retries_retained_files(
    tmp_path: Path,
) -> None:
    content = {"config.json": b"{}"}
    manifest = registry_fixture_manifest("granite_speech_4_1_2b", content)
    downloader = FixtureModelDownloader(content, manifest.repository)
    health_calls = 0

    def health_check(*_args: Any) -> HealthCheckOutcome:
        nonlocal health_calls
        health_calls += 1
        model = next(item for item in service.models() if item["id"] == manifest.model_id)
        assert model["install_stage"] == "checking"
        return HealthCheckOutcome(health_calls > 1, 1024, "health result", {})

    service, _ = service_fixture(
        tmp_path,
        model_downloader=downloader,
        model_health_check=health_check,
        manifest_bundle=injected_bundle(manifest),
    )
    recording = service.create_recording(
        source_name="health.wav",
        content=health_wav_bytes(),
        duration_samples=3200,
        channels=1,
        sample_rate=16000,
    )
    plan = service.request_bundled_model_install(manifest.model_id)
    assert plan["supported_languages"] == ["ja", "en"]
    assert plan["reusing_download"] is False
    with pytest.raises(ClassScribeError, match="不受此模型支持"):
        service.confirm_model_install(
            manifest.model_id,
            confirmation_token=plan["confirmation_token"],
            health_recording_id=recording["id"],
            health_language="zh",
            health_transcript=None,
            terms_accepted=False,
        )
    assert downloader.calls == 0
    assert health_calls == 0
    # Language rejection leaves the confirmation token usable.
    with pytest.raises(ClassScribeError, match="health result"):
        service.confirm_model_install(
            manifest.model_id,
            confirmation_token=plan["confirmation_token"],
            health_recording_id=recording["id"],
            health_language="en",
            health_transcript=None,
            terms_accepted=False,
        )
    model = next(item for item in service.models() if item["id"] == manifest.model_id)
    assert model["install_stage"] == "awaiting_health_check"
    retry = service.request_bundled_model_install(manifest.model_id)
    assert retry["reusing_download"] is True
    assert retry["estimated_download_bytes"] == 0
    result = service.confirm_model_install(
        manifest.model_id,
        confirmation_token=retry["confirmation_token"],
        health_recording_id=recording["id"],
        health_language="en",
        health_transcript=None,
        terms_accepted=False,
    )
    assert result["installed"] is True
    assert downloader.calls == 1
    assert health_calls == 2
    model = next(item for item in service.models() if item["id"] == manifest.model_id)
    assert model["install_stage"] == "complete"


@pytest.mark.parametrize("prewarm", [False, True])
def test_webui_serves_during_slow_model_preparation_and_cancellation_stops_loads(
    tmp_path: Path,
    database: Any,
    monkeypatch: pytest.MonkeyPatch,
    prewarm: bool,
) -> None:
    import threading
    from dataclasses import replace
    from types import SimpleNamespace

    from tests.unit.test_resident_workers import lifecycle_fixture

    supervisor, manager, processes = lifecycle_fixture(
        database, tmp_path, monkeypatch, prewarm=prewarm
    )
    paths = replace(supervisor.paths, runtime=tmp_path.parent / "runtime-webui")
    paths.ensure()
    supervisor.paths = paths
    supervisor.manifest_path = paths.runtime / "resident-workers.json"
    entered, release = threading.Event(), threading.Event()
    original = manager.resolve_for_runtime

    def slow_verify(model_id: str) -> Path:
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test verification gate timed out")
        return original(model_id)

    monkeypatch.setattr(manager, "resolve_for_runtime", slow_verify)
    static = tmp_path / "webui"
    static.mkdir()
    (static / "index.html").write_text("<html>__CLASSSCRIBE_API_TOKEN__</html>")
    token, csrf = "t" * 43, "c" * 43
    app = create_app(
        service=cast(Any, SimpleNamespace(pipeline=None, resident_workers=supervisor)),
        api_token=token,
        csrf_token=csrf,
        enable_scheduler_ipc=True,
        runtime_paths=paths,
        static_directory=static,
    )

    async def scenario() -> None:
        try:
            async with app.router.lifespan_context(app):
                page = await asyncio.wait_for(request(app, "GET", "/"), 0.5)
                assert page.status == 200
                assert not processes
                if not prewarm:
                    assert not entered.is_set()
                    denied = await request(
                        app, "POST", "/api/v1/ibus/workers/prepare", headers=auth(token, csrf)
                    )
                    assert denied.status == 403
                    prepared = await request(
                        app,
                        "POST",
                        "/api/v1/ibus/workers/prepare",
                        headers=auth(token, csrf, write=True),
                    )
                    assert prepared.status == 202

                async def wait_entered() -> None:
                    while not entered.is_set():
                        await asyncio.sleep(0.001)

                await asyncio.wait_for(wait_entered(), 1)
                health = await asyncio.wait_for(request(app, "GET", "/healthz"), 0.5)
                assert health.status == 200
                status = await asyncio.wait_for(
                    request(app, "GET", "/api/v1/ibus/workers", headers=auth(token, csrf)), 0.5
                )
                assert status.json()["stage"] == "verifying"
                cancelled = await asyncio.wait_for(
                    request(
                        app,
                        "POST",
                        "/api/v1/ibus/workers/release",
                        headers=auth(token, csrf, write=True),
                    ),
                    0.5,
                )
                assert cancelled.status == 200
                release.set()
                await asyncio.sleep(0.02)
                assert not processes
                assert supervisor.status()["stage"] == "unloaded"
        finally:
            release.set()

    asyncio.run(scenario())


def test_job_history_lists_all_recordings_in_stable_pages(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from classscribe.db.models import Job, Recording

    service, sessions = service_fixture(tmp_path)
    with sessions.begin() as session:
        for index in range(3):
            recording = Recording(
                source_name=f"lecture-{index}.mp4",
                source_sha256=str(index) * 64,
                source_path=f"{index}.mp4",
                duration_samples=16000,
                sample_rate=16000,
                channels=1,
            )
            session.add(
                Job(
                    recording=recording,
                    language_mode=LanguageMode.JAPANESE,
                    profile_id="balanced",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index),
                )
            )
    first = service.list_jobs(limit=2)
    second = service.list_jobs(limit=2, offset=2)
    assert first["total"] == second["total"] == 3
    assert [item["source_name"] for item in first["items"]] == ["lecture-2.mp4", "lecture-1.mp4"]
    assert second["items"][0]["source_name"] == "lecture-0.mp4"
    assert first["items"][0]["created_at"].endswith("+00:00")


def test_job_history_route_validates_pagination_and_requires_auth(tmp_path: Path) -> None:
    service, _ = service_fixture(tmp_path)
    token, csrf = "t" * 43, "c" * 43
    app = create_app(api_token=token, csrf_token=csrf, service=service)

    async def check() -> None:
        response = await asyncio.wait_for(
            request(app, "GET", "/api/v1/jobs", headers=auth(token, csrf)),
            timeout=3,
        )
        assert response.status == 200
        assert response.json() == {"items": [], "total": 0}
        response = await asyncio.wait_for(
            request(
                app,
                "GET",
                "/api/v1/jobs",
                headers=auth(token, csrf),
                query={"offset": "-1"},
            ),
            timeout=3,
        )
        assert response.status == 422
        response = await asyncio.wait_for(request(app, "GET", "/api/v1/jobs"), timeout=3)
        assert response.status in {401, 403}

    asyncio.run(check())
