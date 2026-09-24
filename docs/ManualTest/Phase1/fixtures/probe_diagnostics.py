"""Inspect real diagnostics response bytes, not browser download history."""

# ruff: noqa: E402
import asyncio
import io
import json
import os
import runpy
import zipfile
from pathlib import Path

for key, folder in [
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_STATE_HOME", "state"),
    ("XDG_RUNTIME_DIR", "runtime"),
]:
    os.environ[key] = "/tmp/classscribe-manual-phase1/" + folder
from classscribe.api.app import create_app

request = runpy.run_path("tests/integration/test_api_v1.py")["request"]
app = create_app(api_token="qa-diagnostics-only", csrf_token="qa-diagnostics-only")
r = asyncio.run(
    request(
        app,
        "GET",
        "/api/v1/diagnostics/bundle",
        headers={"Authorization": "Bearer qa-diagnostics-only"},
    )
)
assert r.status == 200
with zipfile.ZipFile(io.BytesIO(r.content)) as z:
    names = z.namelist()
    data = z.read("diagnostics.json")
    parsed = json.loads(data)
report = {
    "status": r.status,
    "content_type": r.headers.get("content-type"),
    "content_disposition": r.headers.get("content-disposition"),
    "archive_members": names,
    "json_valid": True,
    "json_keys": sorted(parsed),
    "contains_home_path": b"/home/hubery-fedora" in data,
    "contains_test_token": b"qa-diagnostics-only" in data,
    "contains_transcript_fixture": any(
        t.encode() in data
        for t in [
            "数据库忙后恢复保存的测试文本",
            "これはテストです",
            "这是用于字幕边界测试的人工文本",
        ]
    ),
    "bytes": len(r.content),
}
p = Path(__file__).parent.parent / "evidence"
(p / "diagnostics-response.zip").write_bytes(r.content)
(p / "diagnostics-response-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
