#!/usr/bin/env python3
"""Fail CI when the stage-1 repository or dependency boundaries drift."""

from __future__ import annotations

import ast
import json
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKERS = (
    "moss_td",
    "firered",
    "granite",
    "qwen",
    "ark",
    "moss_en",
    "nemotron",
    "pyannote",
    "funasr_experimental",
    "voxtral",
    "vibevoice",
)
WORKER_FILES = (
    "pyproject.toml",
    "uv.lock",
    "worker.py",
    "adapter.py",
    "healthcheck.py",
    "README.md",
)
HEAVY_IMPORTS = {
    "torch",
    "torchaudio",
    "transformers",
    "nemo",
    "vllm",
    "funasr",
    "pyannote",
    "modelscope",
    "huggingface_hub",
    "openai",
    "anthropic",
}
MODEL_INSTALL_ENTRYPOINTS = {"install_confirmed", "request_user_install"}
REQUIRED_PATHS = (
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "pnpm-lock.yaml",
    "backend/classscribe/api",
    "backend/classscribe/db",
    "backend/classscribe/jobs",
    "backend/classscribe/scheduler",
    "backend/classscribe/audio",
    "backend/classscribe/structure",
    "backend/classscribe/asr",
    "backend/classscribe/timeline",
    "backend/classscribe/quality",
    "backend/classscribe/consensus",
    "backend/classscribe/alignment",
    "backend/classscribe/terminology",
    "backend/classscribe/punctuation",
    "backend/classscribe/exports",
    "backend/classscribe/classroom",
    "backend/classscribe/classroom/production.py",
    "backend/classscribe/models",
    "backend/classscribe/models/download.py",
    "backend/classscribe/models/environment.py",
    "backend/classscribe/models/health.py",
    "backend/classscribe/models/inference.py",
    "backend/classscribe/models/resident.py",
    "backend/classscribe/models/licenses.py",
    "frontend/src",
    "frontend/tests",
    "frontend/e2e",
    "ibus/engine",
    "ibus/dictationd",
    "ibus/hotkey_portal",
    "ibus/component/classscribe.xml",
    "protocol/schema",
    "protocol/python",
    "protocol/contract_tests",
    "benchmarks/runner",
    "benchmarks/normalizers",
    "benchmarks/scorers",
    "benchmarks/manifests",
    "packaging/rpm",
    "packaging/systemd",
    "packaging/desktop",
    "docs/architecture.md",
    "docs/timeline.md",
    "docs/model-registry.md",
    "docs/worker-protocol.md",
    "docs/ibus.md",
    "docs/benchmark.md",
    "docs/troubleshooting.md",
    "docs/database.md",
    "docs/security.md",
    "docs/recovery.md",
    "docs/diagnostics.md",
    "docs/fedora-installation.md",
    "docs/model-installation.md",
    "docs/audio-pipeline.md",
    "docs/gpu-scheduling.md",
    "docs/structure-and-speakers.md",
    "docs/body-asr.md",
    "docs/quality-and-consensus.md",
    "docs/postprocessing-and-exports.md",
    "docs/classroom-api-webui.md",
    "config/model-registry.v1.yaml",
    "config/model-revisions.lock.json",
    "config/model-licenses.v1.json",
    "config/schema/model-registry.v1.schema.json",
    "config/schema/v1/course.schema.json",
    "protocol/schema/v1/worker-request.schema.json",
    "protocol/schema/v1/worker-response.schema.json",
    "protocol/schema/v1/model-manifest.schema.json",
    "protocol/schema/v1/resident-workers.schema.json",
    "packaging/rpm/classscribe.spec",
    "packaging/rpm/classscribe-doctor",
    "packaging/systemd/classscribe-core.service",
    "packaging/systemd/classscribe-dictationd.service",
    "packaging/systemd/classscribe-hotkey.service",
    "packaging/desktop/classscribe.desktop",
    "tests/unit",
    "tests/integration",
    "tests/e2e",
    "tests/audio_fixtures",
    "tests/golden",
    "release/release-manifest.v1.json",
    "LICENSES/inventory.v1.json",
)


def python_files(root: Path) -> Iterable[Path]:
    yield from (path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", maxsplit=1)[0])
    return imports


def dependency_names(manifest: Path) -> set[str]:
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    dependencies = data.get("project", {}).get("dependencies", [])
    names: set[str] = set()
    for item in dependencies:
        normalized = item.split("[", maxsplit=1)[0]
        for separator in (">", "<", "=", "!", "~", " "):
            normalized = normalized.split(separator, maxsplit=1)[0]
        names.add(normalized.lower().replace("-", "_"))
    return names


def check() -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).exists():
            errors.append(f"missing required repository path: {relative}")

    core_manifest_dependencies = dependency_names(ROOT / "pyproject.toml")
    forbidden_dependencies = core_manifest_dependencies & HEAVY_IMPORTS
    if forbidden_dependencies:
        errors.append(
            f"core manifest includes model/runtime dependencies: {sorted(forbidden_dependencies)}"
        )

    for path in python_files(ROOT / "backend" / "classscribe"):
        forbidden = imported_roots(path) & (HEAVY_IMPORTS | {"workers", "ibus"})
        if forbidden:
            errors.append(
                f"core boundary violation in {path.relative_to(ROOT)}: {sorted(forbidden)}"
            )

    for path in python_files(ROOT / "backend" / "classscribe" / "consensus"):
        source = path.read_text(encoding="utf-8")
        if "Sequence" + "Matcher" in source:
            errors.append(
                f"long-text sequence matching is forbidden in consensus: {path.relative_to(ROOT)}"
            )

    for subsystem in ("jobs", "classroom"):
        for path in python_files(ROOT / "backend" / "classscribe" / subsystem):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else None
                if name in MODEL_INSTALL_ENTRYPOINTS:
                    errors.append(
                        "ordinary job execution/recovery may not enter model installation: "
                        f"{path.relative_to(ROOT)}:{node.lineno}"
                    )
                if isinstance(node.func, ast.Name) and node.func.id == "HuggingFaceDownloader":
                    errors.append(
                        "ordinary job execution/recovery may not construct a downloader: "
                        f"{path.relative_to(ROOT)}:{node.lineno}"
                    )

    for worker in WORKERS:
        worker_root = ROOT / "workers" / worker
        for filename in WORKER_FILES:
            if not (worker_root / filename).is_file():
                errors.append(f"worker {worker} is missing {filename}")
        manifest = worker_root / "pyproject.toml"
        if manifest.is_file() and "classscribe_protocol" not in dependency_names(manifest):
            errors.append(f"worker {worker} does not depend on the shared protocol package")
        for path in python_files(worker_root):
            forbidden = imported_roots(path) & {"classscribe"}
            if forbidden:
                errors.append(f"worker imports core in {path.relative_to(ROOT)}")

    process_roots = {
        "IBus engine": ROOT / "ibus" / "engine",
        "dictation daemon": ROOT / "ibus" / "dictationd",
        "portal companion": ROOT / "ibus" / "hotkey_portal",
    }
    for label, process_root in process_roots.items():
        for path in python_files(process_root):
            imports = imported_roots(path)
            forbidden = imports & (HEAVY_IMPORTS | {"workers", "classscribe"})
            if forbidden:
                errors.append(
                    f"{label} boundary violation in {path.relative_to(ROOT)}: {sorted(forbidden)}"
                )

    product_contract = json.loads(
        (ROOT / "config" / "product-contract.v1.json").read_text(encoding="utf-8")
    )
    if product_contract["product_surfaces"] != ["classroom_workbench", "ibus_dictation"]:
        errors.append("product surfaces drifted from the frozen contract")
    for schema in (ROOT / "protocol" / "schema").rglob("*.schema.json"):
        data = json.loads(schema.read_text(encoding="utf-8"))
        properties = data.get("properties", {})
        if schema.name == "model-manifest.schema.json":
            if "manifest_version" not in properties:
                errors.append(f"model schema lacks manifest_version: {schema.relative_to(ROOT)}")
        elif schema.name == "model-manifest-bundle.schema.json":
            if "schema_version" not in properties:
                errors.append(f"bundle schema lacks schema_version: {schema.relative_to(ROOT)}")
        elif schema.name != "product-contract.schema.json" and "protocol_version" not in properties:
            errors.append(f"protocol schema lacks protocol_version: {schema.relative_to(ROOT)}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("architecture boundaries: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
