from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def test_core_starts_on_fixed_loopback_port_with_clean_xdg(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "classscribe.cli"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        payload = b""
        for _ in range(60):
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise AssertionError(f"core exited before health check: {stderr}")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/healthz", timeout=0.25) as reply:
                    payload = reply.read()
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        assert b'"service":"classscribe-core"' in payload
        token = tmp_path / "config/classscribe/api-token"
        assert token.is_file()
        assert stat.S_IMODE(token.stat().st_mode) == 0o600
        for base in ("config", "data", "cache", "state", "runtime"):
            app_root = tmp_path / base / "classscribe"
            assert app_root.is_dir()
            assert stat.S_IMODE(app_root.stat().st_mode) == 0o700
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
