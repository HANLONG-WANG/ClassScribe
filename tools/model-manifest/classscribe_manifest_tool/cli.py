"""Command-line entry point for release-time model manifest operations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from classscribe_manifest_tool.credentials import (
    load_hf_token,
    redact_for_diagnostics,
    require_gated_access_approval,
)
from classscribe_manifest_tool.discovery import (
    discover_repository_tree,
    validate_selection_against_discovery,
    write_discovery_report,
)
from classscribe_manifest_tool.generator import (
    generate_release_bundle,
    read_source_date_epoch,
    write_release_bundle,
)
from classscribe_manifest_tool.inputs import ReleaseInputs, load_release_inputs
from classscribe_manifest_tool.support import (
    load_worker_support,
    model_status,
    validate_worker_support,
)
from classscribe_manifest_tool.verifier import verify_bundle


def build_parser() -> argparse.ArgumentParser:
    """Build the release-tool command surface."""
    parser = argparse.ArgumentParser(
        prog="classscribe-model-manifest",
        description="Discover, generate, and verify ClassScribe model manifests.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    discover = commands.add_parser(
        "discover", help="online discovery of fixed-revision repository trees"
    )
    discover.add_argument("--registry", type=Path, required=True)
    discover.add_argument("--selection", type=Path, required=True)
    discover.add_argument("--output", type=Path, required=True)
    discover.add_argument(
        "--model-id",
        action="append",
        default=[],
        metavar="MODEL_ID",
        help="discover only this validated registry model; repeat to select more models",
    )
    _add_credential_arguments(discover)
    discover.set_defaults(handler=_run_discover)

    generate = commands.add_parser(
        "generate", help="online generation of a deterministic manifest bundle"
    )
    generate.add_argument("--registry", type=Path, required=True)
    generate.add_argument("--revisions", type=Path, required=True)
    generate.add_argument("--licenses", type=Path, required=True)
    generate.add_argument("--selection", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    _add_credential_arguments(generate)
    generate.set_defaults(handler=_run_generate)

    verify = commands.add_parser(
        "verify", help="offline verification of a local manifest bundle"
    )
    verify.add_argument("--bundle", type=Path, required=True)
    verify.set_defaults(handler=_run_verify)

    status = commands.add_parser("status", help="check manifest status for all registry models")
    status.add_argument("--registry", type=Path, required=True)
    status.add_argument("--selection", type=Path, required=True)
    status.add_argument("--bundle", type=Path)
    status.set_defaults(handler=_run_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the manifest tool entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as error:
        token = os.environ.get("HF_TOKEN")
        diagnostic = redact_for_diagnostics(error, token=token)
        print(
            json.dumps(
                {"status": "error", "error": diagnostic},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _add_credential_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--token-file", type=Path)
    parser.add_argument(
        "--accept-gated-repository",
        action="append",
        default=[],
        metavar="OWNER/NAME",
        help="confirm the user has already accepted this repository's upstream terms",
    )


def _release_inputs_for_discovery(args: argparse.Namespace) -> ReleaseInputs:
    config = args.registry.parent
    return load_release_inputs(
        registry_path=args.registry,
        revisions_path=config / "model-revisions.lock.json",
        licenses_path=config / "model-licenses.v1.json",
        selection_path=args.selection,
        require_complete_selection=False,
    )


def _run_discover(args: argparse.Namespace) -> dict[str, object]:
    token = load_hf_token(token_file=args.token_file)
    inputs = _release_inputs_for_discovery(args)
    if args.output.absolute() == (
        inputs.root / "config/model-manifests/v1"
    ).absolute():
        raise ValueError("discovery reports must not use the runtime bundle directory")
    requested_model_ids = tuple(args.model_id)
    if len(requested_model_ids) != len(set(requested_model_ids)):
        raise ValueError("discovery model IDs must be unique")
    known_model_ids = {model.model_id for model in inputs.models}
    unknown_model_ids = sorted(set(requested_model_ids) - known_model_ids)
    if unknown_model_ids:
        raise ValueError(f"discovery model IDs are not in the registry: {unknown_model_ids}")
    selected_model_ids = set(requested_model_ids)
    models = (
        inputs.models
        if not selected_model_ids
        else tuple(model for model in inputs.models if model.model_id in selected_model_ids)
    )
    reports: list[str] = []
    for model in models:
        require_gated_access_approval(
            model.repository,
            requires_terms_acceptance=model.requires_terms_acceptance,
            accepted_repositories=args.accept_gated_repository,
            token=token,
        )
        discovery = discover_repository_tree(
            model.repository,
            model.revision,
            token=token,
        )
        selection = inputs.selections.models.get(model.model_id)
        if selection is not None:
            validate_selection_against_discovery(selection, discovery)
        report = write_discovery_report(
            model.model_id,
            discovery,
            args.output,
            token=token,
        )
        reports.append(report.name)
    return {"status": "discovered", "model_count": len(reports), "reports": reports}


def _run_generate(args: argparse.Namespace) -> dict[str, object]:
    token = load_hf_token(token_file=args.token_file)
    inputs = load_release_inputs(
        registry_path=args.registry,
        revisions_path=args.revisions,
        licenses_path=args.licenses,
        selection_path=args.selection,
        require_complete_selection=True,
    )
    epoch = read_source_date_epoch(os.environ)
    manifests, bundle = generate_release_bundle(
        inputs,
        source_date_epoch=epoch,
        token=token,
        accepted_repositories=args.accept_gated_repository,
    )
    write_release_bundle(args.output, manifests, bundle, token=token)
    verification = verify_bundle(args.output / "bundle.v1.json", root=inputs.root)
    return verification.as_dict()


def _run_verify(args: argparse.Namespace) -> dict[str, object]:
    return verify_bundle(args.bundle).as_dict()


def _run_status(args: argparse.Namespace) -> dict[str, object]:
    inputs = _release_inputs_for_discovery(args)
    support = load_worker_support(inputs.root)
    validate_worker_support(
        support, {model.model_id: model.worker for model in inputs.models}
    )
    manifest_hashes: Mapping[str, str] = {}
    bundle_status = "missing"
    if args.bundle is not None:
        verification = verify_bundle(args.bundle, root=inputs.root)
        manifest_hashes = verification.manifest_sha256
        bundle_status = "verified"
    models = []
    for model in inputs.models:
        status = model_status(
            inputs.root,
            model_id=model.model_id,
            worker=model.worker,
            enabled=model.enabled,
            experimental=model.experimental,
            manifest_sha256=manifest_hashes.get(model.model_id),
            support=support,
        )
        status["selection_frozen"] = model.model_id in inputs.selections.models
        models.append(status)
    return {"status": "checked", "bundle": bundle_status, "models": models}


if __name__ == "__main__":
    raise SystemExit(main())
