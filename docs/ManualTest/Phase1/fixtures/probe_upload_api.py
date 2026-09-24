"""Supplementary API QA using real routes and FFmpeg without inference."""

# ruff: noqa: E402
import asyncio
import json
import os
import runpy
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

app = create_app(api_token="qa-upload-only", csrf_token="qa-upload-only")
request = runpy.run_path("tests/integration/test_api_v1.py")["request"]
fixtures = Path(__file__).parent
results = []
for i, name in enumerate(["empty.wav", "broken.wav", "terms.txt", "silence-2s.wav"], 101):
    rid = f"10000000-0000-4000-8000-{i:012d}"
    response = asyncio.run(
        request(
            app,
            "PUT",
            f"/api/v1/recordings/{rid}/upload",
            headers={
                "Authorization": "Bearer qa-upload-only",
                "X-ClassScribe-CSRF-Token": "qa-upload-only",
                "X-ClassScribe-Filename": name,
                "Content-Type": "application/octet-stream",
            },
            body=(fixtures / name).read_bytes(),
        )
    )
    try:
        body = response.json()
    except ValueError:
        body = response.content.decode()
    results.append({"fixture": name, "id": rid, "status": response.status, "body": body})
rid = "10000000-0000-4000-8000-000000000104"
response = asyncio.run(
    request(
        app,
        "PUT",
        f"/api/v1/recordings/{rid}/upload",
        headers={
            "Authorization": "Bearer qa-upload-only",
            "X-ClassScribe-CSRF-Token": "qa-upload-only",
            "X-ClassScribe-Filename": "silence-2s.wav",
            "Content-Type": "application/octet-stream",
        },
        body=(fixtures / "silence-2s.wav").read_bytes(),
    )
)
results.append({"case": "repeat_same_uuid", "status": response.status, "body": response.json()})
response = asyncio.run(
    request(
        app,
        "POST",
        "/api/v1/jobs",
        headers={
            "Authorization": "Bearer qa-upload-only",
            "X-ClassScribe-CSRF-Token": "qa-upload-only",
            "Content-Type": "application/json",
        },
        body=json.dumps(
            {
                "recording_id": rid,
                "language": "zh",
                "accuracy_mode": "balanced",
                "model_selection": "auto_best",
                "outputs": ["md"],
            }
        ).encode(),
    )
)
results.append(
    {"case": "missing_models_preflight", "status": response.status, "body": response.json()}
)
out = fixtures.parent / "evidence" / "upload-api.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(out)
print(json.dumps(results, ensure_ascii=False, indent=2))
