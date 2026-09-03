from __future__ import annotations

import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest
from classscribe.release_check import validate_installed_release

ROOT = Path(__file__).resolve().parents[2]
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
    "/usr/share/classscribe/protocol/python/classscribe_protocol/messages.py",
    "/usr/share/classscribe/protocol/python/classscribe_protocol/resident_workers.py",
    "/usr/share/classscribe/protocol-schema/v1/resident-workers.schema.json",
    "/usr/lib/classscribe/classscribe/classroom/production.py",
    "/usr/lib/classscribe/classscribe/models/resident.py",
    "/usr/share/classscribe/workers/qwen/uv.lock",
    "/usr/share/classscribe/workers/qwen/worker.py",
    "/usr/share/classscribe/frontend/dist/index.html",
    "/usr/share/classscribe/release/release-manifest.v1.json",
    "/usr/share/classscribe/release/release-readiness.json",
    "/usr/share/classscribe/tests/audio_fixtures/regression-audio.v1.json",
    "/usr/share/doc/classscribe/docs/user-guide.md",
    "/usr/share/licenses/classscribe/inventory.v1.json",
    "/usr/bin/classscribe-benchmark",
    "/usr/bin/classscribe-release-check",
    "/usr/bin/classscribe-doctor",
}


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


def _create_source_archive(destination: Path, version: str) -> Path:
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
                output.add(path, arcname=arcname, recursive=False)
    return archive


def _build_rpm(topdir: Path, version: str) -> Path:
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS", "TMP"):
        (topdir / name).mkdir(parents=True, exist_ok=True)
    _create_source_archive(topdir, version)
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
    for path in REQUIRED_RPM_PATHS:
        assert (payload / path.removeprefix("/")).exists()
    installed = validate_installed_release(payload)
    assert installed.checks["artifact_inventory"] is True
    assert installed.checks["model_revision_lock"] is True
    assert installed.checks["model_license_inventory"] is True
    assert installed.checks["dependency_lock_hashes"] is True
    assert installed.checks["final_architecture"] is True
    assert installed.status == "blocked"
    subprocess.run(
        [*rpm_database, "-U", "--nodeps", "--noscripts", *relocation, str(second)],
        check=True,
    )
    assert sentinel.read_text(encoding="utf-8") == "user-owned"
    subprocess.run(
        [*rpm_database, "-e", "--noscripts", "classscribe"],
        check=True,
    )
    assert sentinel.read_text(encoding="utf-8") == "user-owned"
    assert not (payload / "usr/libexec/ibus-engine-classscribe").exists()


@pytest.mark.skipif(shutil.which("rpmbuild") is None, reason="rpmbuild not installed")
def test_rpm_install_upgrade_remove_preserves_user_data(tmp_path: Path) -> None:
    first = _build_rpm(tmp_path / "rpmbuild-a", "0.1.0")
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
        for fragment in ("/.config/classscribe", "/.local/share/classscribe", "/.cache/classscribe")
    )

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
