from __future__ import annotations

import json
import stat
from pathlib import Path

from scripts.validate_ibus_desktop import _atomic_json


def test_atomic_json_preserves_existing_parent_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    output = parent / "report.json"

    _atomic_json(output, {"status": "incomplete"})

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "incomplete"}
