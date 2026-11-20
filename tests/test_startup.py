"""Opt-in bounded smoke checks of the actual single-command launchers.

GTA_VERIFY_STARTUP=1 python -m pytest tests/test_startup.py -q
Requires installed frontend dependencies and a production build. Does not leave
servers running or touch regular application storage.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from backend.core.process import managed_process

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("GTA_VERIFY_STARTUP") != "1", reason="Opt-in network-port startup test")


def _available(port):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


@pytest.mark.parametrize("production", [False, True], ids=["development", "production"])
def test_single_command_launcher_terminates_cleanly(tmp_path, monkeypatch, production):
    node = shutil.which("node")
    if not node or not (ROOT / "frontend/node_modules/vite/bin/vite.js").is_file():
        pytest.skip("Install frontend dependencies first")
    if production and not (ROOT / "frontend/dist/index.html").is_file():
        pytest.skip("Build the frontend first")
    ports = [8000] if production else [8000, 5173]
    if not all(_available(port) for port in ports):
        pytest.skip("A user-owned server is already using a test port")
    monkeypatch.setenv("GTA_STORAGE_DIR", str(tmp_path / "studio"))
    port = 8000 if production else 5173
    url = f"http://127.0.0.1:{port}"
    command = [node, str(ROOT / "scripts/dev.mjs"), *(["--production"] if production else [])]
    with managed_process(command) as child:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            assert child.poll() is None, "The development coordinator exited before readiness"
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                    health = json.load(response)
                if health.get("status") == "ok":
                    break
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(.2)
        else:
            pytest.fail("Launcher did not become ready in 40 seconds")
        with urllib.request.urlopen(url, timeout=5) as response:
            assert b'<div id="root"' in response.read()
        with urllib.request.urlopen(url + "/projects/deep-link", timeout=5) as response:
            assert b'<div id="root"' in response.read()
        with urllib.request.urlopen(url + "/api/events", timeout=5) as response:
            assert response.readline().strip() == b"event: update"
            assert response.readline().startswith(b"data: ")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not all(_available(p) for p in ports):
        time.sleep(.1)
    assert all(_available(p) for p in ports), "Launcher leaked a server process"
