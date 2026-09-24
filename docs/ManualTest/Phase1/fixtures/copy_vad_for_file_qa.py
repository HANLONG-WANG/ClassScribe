"""Copy one small existing model for file-only QA. Never load or infer."""

import json
import shutil
from pathlib import Path

from classscribe.db import create_sqlite_engine, make_session_factory
from classscribe.db.models import ModelHealth, ModelInstallation
from classscribe.models.manager import ModelManager, ModelManifest
from sqlalchemy import select

source = Path("/home/hubery-fedora/.cache/classscribe/models/firered_vad")
dest = Path("/tmp/classscribe-manual-phase1/cache/classscribe/models/firered_vad")
assert not dest.exists(), "Refuse to overwrite test state"
assert not any(p.is_symlink() for p in source.rglob("*")), "Refuse symlink copying"
revision = json.loads((source / "active.json").read_text())["revision"]
audit = json.loads((source / "revisions" / revision / "supply-chain.json").read_text())
manifest = ModelManifest.from_dict(audit["manifest"])
digest = ModelManager._verify_payload(source / "revisions" / revision, manifest, allow_audit=True)
assert digest == audit["aggregate_sha256"]
shutil.copytree(source, dest)
sessions = make_session_factory(
    create_sqlite_engine(
        Path("/tmp/classscribe-manual-phase1/data/classscribe/classscribe.sqlite3")
    )
)
with sessions.begin() as s:
    assert (
        s.scalar(select(ModelInstallation).where(ModelInstallation.model_id == "firered_vad"))
        is None
    )
    s.add(
        ModelInstallation(
            model_id="firered_vad",
            repository=manifest.repository,
            revision=revision,
            local_path=str(dest / "revisions" / revision),
            sha256=digest,
            environment_json=dict(manifest.environment),
            health_status=ModelHealth.UNKNOWN,
        )
    )
report = {
    "model_id": "firered_vad",
    "revision": revision,
    "file_hash_verification": True,
    "files": len(manifest.files),
    "bytes": sum(f.size_bytes for f in manifest.files),
    "copied_to_isolated_root": True,
    "inference_run": False,
    "health_status": "unknown",
}
(Path(__file__).parent.parent / "evidence" / "vad-file-copy.json").write_text(
    json.dumps(report, indent=2)
)
print(json.dumps(report, indent=2))
