from __future__ import annotations

import io
import json
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest
from classscribe.release_check import validate_installed_release

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_RELATIVE = Path("config/model-manifests/v1/bundle.v1.json")
BUNDLE_PATH = ROOT / BUNDLE_RELATIVE
SOURCE_BUNDLE = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
MANIFEST_NAMES = tuple(entry["path"] for entry in SOURCE_BUNDLE["manifests"])
FIRST_BUNDLE_GENERATED_AT = "2026-09-02T16:00:00Z"
REQUIRED_RPM_PATHS = {
    "/usr/libexec/ibus-engine-classscribe",
    "/usr/share/ibus/component/classscribe.xml",
    "/usr/lib/systemd/user/classscribe-core.service",
    "/usr/lib/systemd/user/classscribe-dictationd.service",
    "/usr/lib/systemd/user/classscribe-hotkey.service",
    "/usr/share/applications/classscribe.desktop",
    "/usr/share/icons/hicolor/scalable/apps/classscribe.svg",
    "/usr/share/classscribe/default.yaml",
    "/usr/share/classscribe/config/model-registry.v1.yaml",
    "/usr/share/classscribe/config/model-licenses.v1.json",
    "/usr/share/classscribe/config/schema/model-file-selection.v1.schema.json",
    "/usr/share/classscribe/config/model-file-selection.v1.yaml",
    "/usr/share/classscribe/config/model-manifests/v1/bundle.v1.json",
    "/usr/share/classscribe/protocol/python/classscribe_protocol/messages.py",
    "/usr/share/classscribe/protocol/python/classscribe_protocol/resident_workers.py",
    "/usr/share/classscribe/protocol-schema/v1/model-manifest-bundle.schema.json",
    "/usr/share/classscribe/protocol-schema/v1/resident-workers.schema.json",
    "/usr/lib/classscribe/classscribe/classroom/production.py",
    "/usr/lib/classscribe/classscribe/models/resident.py",
    "/usr/share/classscribe/workers/qwen/uv.lock",
    "/usr/share/classscribe/workers/qwen/worker.py",
    "/usr/share/classscribe/frontend/dist/index.html",
    "/usr/share/classscribe/release/release-manifest.v1.json",
    "/usr/share/classscribe/release/release-readiness.json",
    "/usr/share/classscribe/release/release-waivers.v1.json",
    "/usr/share/classscribe/tests/audio_fixtures/regression-audio.v1.json",
    "/usr/share/doc/classscribe/docs/user-guide.md",
    "/usr/share/licenses/classscribe/inventory.v1.json",
    "/usr/bin/classscribe-benchmark",
    "/usr/bin/classscribe-release-check",
    "/usr/bin/classscribe-doctor",
}
REQUIRED_RPM_PATHS.update(
    f"/usr/share/classscribe/config/model-manifests/v1/{name}"
    for name in MANIFEST_NAMES
)


@pytest.mark.skipif(
    shutil.which("desktop-file-validate") is None, reason="desktop-file-utils not installed"
)
def test_desktop_ibus_and_user_services_are_valid_and_hardened() -> None:
    subprocess.run(
        ["desktop-file-validate", str(ROOT / "packaging/desktop/classscribe.desktop")],
        check=True,
        capture_output=True,
        text=True,
    )
    component = (ROOT / "ibus/component/classscribe.xml").read_text(encoding="utf-8")
    assert "<exec>/usr/libexec/ibus-engine-classscribe</exec>" in component

    for name in ("core", "dictationd", "hotkey"):
        unit = (ROOT / f"packaging/systemd/classscribe-{name}.service").read_text(encoding="utf-8")
        assert "Restart=on-failure" in unit
        assert "StartLimitIntervalSec=" in unit
        assert "StartLimitBurst=" in unit
        assert "UMask=0077" in unit
        assert "HF_HUB_OFFLINE=1" in unit
        assert "TRANSFORMERS_OFFLINE=1" in unit
        assert "User=" not in unit
        assert "setenforce" not in unit.lower()
        assert "permissive" not in unit.lower()
    core = (ROOT / "packaging/systemd/classscribe-core.service").read_text(encoding="utf-8")
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in core
    assert "IPAddressDeny=any" not in core


def _bundle_bytes(generated_at: str | None = None) -> bytes:
    value = dict(SOURCE_BUNDLE)
    if generated_at is not None:
        value["generated_at"] = generated_at
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _create_source_archive(
    destination: Path,
    version: str,
    *,
    bundle_generated_at: str | None = None,
) -> Path:
    archive = destination / "SOURCES" / f"classscribe-{version}.tar.gz"
    includes = (
        "backend",
        "protocol",
        "ibus",
        "packaging",
        "config",
        "frontend/dist",
        "workers",
        "scripts",
        "benchmarks",
        "docs",
        "release",
        "tests/audio_fixtures/regression-audio.v1.json",
        "tests/golden/regressions.v1.json",
        "README.md",
        "LICENSES",
    )
    with tarfile.open(archive, "w:gz") as output:
        for relative in includes:
            source = ROOT / relative
            paths = [source] if source.is_file() else [source, *source.rglob("*")]
            for path in paths:
                if any(part in {".venv", "__pycache__", ".pytest_cache"} for part in path.parts):
                    continue
                arcname = Path(f"classscribe-{version}") / path.relative_to(ROOT)
                if path == BUNDLE_PATH and bundle_generated_at is not None:
                    content = _bundle_bytes(bundle_generated_at)
                    info = output.gettarinfo(str(path), arcname=str(arcname))
                    info.size = len(content)
                    output.addfile(info, io.BytesIO(content))
                else:
                    output.add(path, arcname=arcname, recursive=False)
    return archive


def _build_rpm(
    topdir: Path,
    version: str,
    *,
    bundle_generated_at: str | None = None,
) -> Path:
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS", "TMP"):
        (topdir / name).mkdir(parents=True, exist_ok=True)
    _create_source_archive(
        topdir,
        version,
        bundle_generated_at=bundle_generated_at,
    )
    spec_text = (ROOT / "packaging/rpm/classscribe.spec").read_text(encoding="utf-8")
    spec_text = spec_text.replace("Version:        0.1.0", f"Version:        {version}")
    spec = topdir / "SPECS" / "classscribe.spec"
    spec.write_text(spec_text, encoding="utf-8")
    subprocess.run(
        [
            "rpmbuild",
            "-bb",
            "--define",
            f"_topdir {topdir}",
            "--define",
            f"_tmppath {topdir / 'TMP'}",
            str(spec),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    packages = list((topdir / "RPMS").rglob(f"classscribe-{version}-*.noarch.rpm"))
    assert len(packages) == 1
    return packages[0]


def _exercise_rpm_lifecycle(install_root: Path, first_source: Path, second_source: Path) -> None:
    first = install_root / first_source.name
    second = install_root / second_source.name
    shutil.copy2(first_source, first)
    shutil.copy2(second_source, second)
    database = install_root / "rpmdb"
    payload = install_root / "payload"
    database.mkdir()
    # RPM 6 requires the transaction lock to exist before a non-system db is initialized.
    (database / ".rpm.lock").touch(mode=0o600)
    payload.mkdir()
    sentinel = payload / "home/student/.local/share/classscribe/recordings.keep"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("user-owned", encoding="utf-8")
    rpm_database = ["rpm", "--dbpath", str(database)]
    relocation = ["--badreloc", "--relocate", f"/={payload}"]
    subprocess.run([*rpm_database, "--initdb"], check=True)
    subprocess.run(
        [*rpm_database, "-i", "--nodeps", "--noscripts", *relocation, str(first)],
        check=True,
    )
    for required_path in REQUIRED_RPM_PATHS:
        assert (payload / required_path.removeprefix("/")).exists()
    installed_bundle_directory = payload / "usr/share/classscribe/config/model-manifests/v1"
    assert {path.name for path in installed_bundle_directory.iterdir()} == {
        "bundle.v1.json",
        *MANIFEST_NAMES,
    }
    for installed_path in installed_bundle_directory.iterdir():
        assert stat.S_IMODE(installed_path.stat().st_mode) == 0o644
    assert (installed_bundle_directory / "bundle.v1.json").read_bytes() == _bundle_bytes(
        FIRST_BUNDLE_GENERATED_AT
    )
    for name in MANIFEST_NAMES:
        assert (installed_bundle_directory / name).read_bytes() == (
            ROOT / "config/model-manifests/v1" / name
        ).read_bytes()
    installed = validate_installed_release(payload)
    assert installed.checks["artifact_inventory"] is True
    assert installed.checks["model_revision_lock"] is True
    assert installed.checks["model_license_inventory"] is True
    assert installed.checks["model_manifest_bundle"] is True
    assert installed.checks["dependency_lock_hashes"] is True
    assert installed.checks["final_architecture"] is True
    assert installed.status == "ready_with_waivers"
    assert installed.checks["source_license"] is False
    assert installed.checks["phase12_acceptance"] is False
    assert installed.checks["desktop_matrix"] is False
    assert all(installed.effective_checks.values())
    assert installed.blocking_reasons == ()
    installed_waiver = payload / "usr/share/classscribe/release/release-waivers.v1.json"
    source_waiver = ROOT / "release/release-waivers.v1.json"
    assert installed_waiver.read_bytes() == source_waiver.read_bytes()
    assert stat.S_IMODE(installed_waiver.stat().st_mode) == 0o644
    installed_waiver.write_bytes(installed_waiver.read_bytes() + b" ")
    tampered = validate_installed_release(payload)
    assert tampered.status == "blocked"
    assert tampered.waiver_errors == (
        "release waiver SHA-256 differs from release manifest",
    )
    assert tampered.effective_checks["source_license"] is False
    installed_waiver.write_bytes(source_waiver.read_bytes())
    assert validate_installed_release(payload).status == "ready_with_waivers"
    subprocess.run(
        [*rpm_database, "-U", "--nodeps", "--noscripts", *relocation, str(second)],
        check=True,
    )
    assert (installed_bundle_directory / "bundle.v1.json").read_bytes() == BUNDLE_PATH.read_bytes()
    assert (installed_bundle_directory / "bundle.v1.json").read_bytes() != _bundle_bytes(
        FIRST_BUNDLE_GENERATED_AT
    )
    for name in MANIFEST_NAMES:
        assert (installed_bundle_directory / name).read_bytes() == (
            ROOT / "config/model-manifests/v1" / name
        ).read_bytes()
    upgraded = validate_installed_release(payload)
    assert upgraded.checks["model_manifest_bundle"] is True
    assert upgraded.status == "ready_with_waivers"
    assert upgraded.waived_gates == (
        "source_license",
        "phase12_acceptance",
        "desktop_matrix",
    )
    assert sentinel.read_text(encoding="utf-8") == "user-owned"
    subprocess.run(
        [*rpm_database, "-e", "--noscripts", "classscribe"],
        check=True,
    )
    assert sentinel.read_text(encoding="utf-8") == "user-owned"
    assert not (payload / "usr/libexec/ibus-engine-classscribe").exists()
    assert not installed_bundle_directory.exists()


@pytest.mark.skipif(shutil.which("rpmbuild") is None, reason="rpmbuild not installed")
def test_rpm_install_upgrade_remove_preserves_user_data(tmp_path: Path) -> None:
    first = _build_rpm(
        tmp_path / "rpmbuild-a",
        "0.1.0",
        bundle_generated_at=FIRST_BUNDLE_GENERATED_AT,
    )
    second = _build_rpm(tmp_path / "rpmbuild-b", "0.1.1")
    package_files = set(
        subprocess.run(
            ["rpm", "-qlp", str(first)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    assert package_files >= REQUIRED_RPM_PATHS
    assert not any(
        fragment in path
        for path in package_files
        for fragment in (
            "/.config/classscribe",
            "/.local/share/classscribe",
            "/.cache/classscribe",
            "/.cache/huggingface",
            "/.staging/",
            "/uv-cache-",
            "/generation-",
        )
    )
    assert not any(
        Path(path).suffix.lower()
        in {".bin", ".ckpt", ".gguf", ".nemo", ".onnx", ".pt", ".pth", ".safetensors"}
        for path in package_files
    )
    assert not any("hf_token" in path.lower() for path in package_files)

    with tempfile.TemporaryDirectory(prefix="classscribe-rpm-test-", dir="/tmp") as temporary:
        _exercise_rpm_lifecycle(Path(temporary), first, second)


def test_packaging_never_disables_selinux_or_changes_system_dependencies() -> None:
    packaging_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "packaging").rglob("*")
        if path.is_file()
    ).lower()
    assert "setenforce" not in packaging_text
    assert "selinux=0" not in packaging_text
    assert "dnf install" not in packaging_text
    assert "rpm-ostree install" not in packaging_text
