"""Setup tests never download binaries, install packages or open a browser."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.core import config
from scripts import bootstrap


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tools = tmp_path / "storage/tools"
    tools.mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "TOOLS", tools)
    monkeypatch.setattr(bootstrap, "FFMPEG_DIR", tools / "ffmpeg")
    for name in ("FFMPEG_PATH", "FFPROBE_PATH"):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(root=tmp_path, tools=tools)


class Response(io.BytesIO):
    def __init__(self, body, length=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def geturl(self):
        return bootstrap.FFMPEG_URL


@pytest.mark.parametrize("problem", ["none", "checksum", "truncated", "oversized"])
def test_streaming_download_verifies_integrity_without_network(setup, monkeypatch, problem):
    body = b"publisher ZIP bytes" * 100
    length = len(body) + 1 if problem == "truncated" else len(body)
    if problem == "oversized":
        length = bootstrap.MAX_DOWNLOAD_BYTES + 1
    monkeypatch.setattr(bootstrap, "request_url", lambda url: Response(body, length))
    expected = "0" * 64 if problem == "checksum" else hashlib.sha256(body).hexdigest()
    target = setup.tools / "download.zip"
    if problem == "none":
        bootstrap.download_archive(bootstrap.FFMPEG_URL, target, expected)
        assert target.read_bytes() == body
    else:
        with pytest.raises((RuntimeError, OSError)):
            bootstrap.download_archive(bootstrap.FFMPEG_URL, target, expected)


@pytest.mark.parametrize("honors_range", [False, True])
def test_download_resumes_or_safely_restarts_partial_file(setup, monkeypatch, honors_range):
    body = b"complete archive fixture bytes"
    target = setup.tools / "partial.zip"
    target.write_bytes(body[:8])
    response = Response(body[8:] if honors_range else body)
    if honors_range:
        response.status = 206
        response.headers["Content-Range"] = f"bytes 8-{len(body) - 1}/{len(body)}"
    request = Mock(return_value=response)
    monkeypatch.setattr(bootstrap, "request_url", request)
    bootstrap.download_archive(bootstrap.FFMPEG_URL, target, hashlib.sha256(body).hexdigest())
    request.assert_called_once_with(bootstrap.FFMPEG_URL, offset=8)
    assert target.read_bytes() == body


def test_invalid_full_sized_cache_is_removed_when_server_rejects_resume(setup, monkeypatch):
    target = setup.tools / "corrupt.zip"
    target.write_bytes(b"corrupt bytes")
    error = bootstrap.urllib.error.HTTPError(bootstrap.FFMPEG_URL, 416, "Range invalid", {}, None)
    monkeypatch.setattr(bootstrap, "request_url", Mock(side_effect=error))
    with pytest.raises(OSError, match="restarting from zero"):
        bootstrap.download_archive(bootstrap.FFMPEG_URL, target, "0" * 64)
    assert not target.exists()


def test_completed_checksum_valid_download_is_reused_offline(setup, monkeypatch):
    body = b"verified archive fixture"
    target = setup.tools / "cached.zip"
    target.write_bytes(body)
    request = Mock(side_effect=AssertionError("No network expected"))
    monkeypatch.setattr(bootstrap, "request_url", request)
    bootstrap.download_archive(bootstrap.FFMPEG_URL, target, hashlib.sha256(body).hexdigest())
    request.assert_not_called()


def test_zip_extracts_only_tools_to_fixed_destinations(setup):
    archive = setup.tools / "package.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../../outside.txt", "never extract")
        zipped.writestr("release/bin/ffmpeg.exe", b"ffmpeg fixture")
        zipped.writestr("release/bin/ffprobe.exe", b"ffprobe fixture")
    target = setup.tools / "unpacked/bin"
    bootstrap.extract_tools(archive, target)
    assert {p.name for p in target.iterdir()} == {"ffmpeg.exe", "ffprobe.exe"}
    assert (target / "ffmpeg.exe").read_bytes() == b"ffmpeg fixture"
    assert not (setup.root / "outside.txt").exists()


@pytest.mark.parametrize("duplicate", [False, True])
def test_zip_rejects_missing_or_duplicate_tools(setup, duplicate):
    archive = setup.tools / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("release/bin/ffmpeg.exe", b"fixture")
        if duplicate:
            zipped.writestr("other/bin/ffmpeg.exe", b"fixture")
    with pytest.raises(RuntimeError):
        bootstrap.extract_tools(archive, setup.tools / "out")


def test_bundled_binary_is_reused_before_path(setup, monkeypatch):
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bundled = setup.tools / "ffmpeg/bin" / name
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture")
    monkeypatch.setattr(bootstrap, "binary_works", lambda path, name: True)
    lookup = Mock(return_value="unrelated-system-tool")
    monkeypatch.setattr(bootstrap.shutil, "which", lookup)
    assert bootstrap.resolve_binary("ffmpeg") == bundled
    lookup.assert_not_called()


def test_explicit_invalid_binary_override_is_not_silently_replaced(setup, monkeypatch):
    monkeypatch.setenv("FFMPEG_PATH", str(setup.root / "missing.exe"))
    monkeypatch.setattr(bootstrap, "binary_works", lambda path, name: False)
    with pytest.raises(RuntimeError, match="FFMPEG_PATH is invalid"):
        bootstrap.resolve_binary("ffmpeg")


def test_backend_finds_local_tools_for_manual_startup(setup, monkeypatch):
    monkeypatch.setattr(config, "ROOT", setup.root)
    filename = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    binary = setup.tools / "ffmpeg/bin" / filename
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture")
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    assert config.get_binary("ffprobe") == str(binary)
    binary.unlink()
    with pytest.raises(RuntimeError, match="run.dev.cmd"):
        config.get_binary("ffprobe")


def test_media_setup_does_not_download_when_both_tools_work(setup, monkeypatch):
    monkeypatch.setattr(bootstrap, "resolve_binary", lambda name: setup.tools / f"{name}.exe")
    install = Mock()
    monkeypatch.setattr(bootstrap, "install_ffmpeg", install)
    result = bootstrap.ensure_media()
    assert set(result) == {"FFMPEG_PATH", "FFPROBE_PATH"}
    install.assert_not_called()


def test_media_setup_installs_if_either_tool_is_missing(setup, monkeypatch):
    installed = []
    monkeypatch.setattr(bootstrap, "resolve_binary", lambda name: setup.tools / f"{name}.exe" if installed or name == "ffmpeg" else None)
    monkeypatch.setattr(bootstrap, "install_ffmpeg", lambda: installed.append(True))
    assert bootstrap.ensure_media()["FFPROBE_PATH"].endswith("ffprobe.exe")
    assert installed == [True]


def test_python_setup_reuses_healthy_matching_environment(setup, monkeypatch):
    python = setup.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    (setup.root / "requirements.txt").write_text("fastapi>=0.115\n", encoding="utf-8")
    stamp = setup.root / ".venv/.gta-requirements.sha256"
    stamp.write_text(bootstrap.digest_files([setup.root / "requirements.txt"], str(python.resolve())), encoding="utf-8")
    execute = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(bootstrap, "execute", execute)
    monkeypatch.setattr(bootstrap.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0)))
    assert bootstrap.ensure_python() == python
    assert not any("install" in call.args[0] for call in execute.call_args_list)


def test_install_only_never_starts_server_or_browser(setup, monkeypatch):
    monkeypatch.setattr(bootstrap, "find_node", lambda: ("node", "v22.0.0", setup.tools / "npm-cli.js"))
    python = Mock(return_value=setup.root / ".venv/Scripts/python.exe")
    javascript = Mock()
    media = Mock(return_value={"FFMPEG_PATH": "ffmpeg", "FFPROBE_PATH": "ffprobe"})
    launch = Mock()
    monkeypatch.setattr(bootstrap, "ensure_python", python)
    monkeypatch.setattr(bootstrap, "ensure_javascript", javascript)
    monkeypatch.setattr(bootstrap, "ensure_media", media)
    monkeypatch.setattr(bootstrap, "ensure_captions", Mock())
    monkeypatch.setattr(bootstrap, "launch", launch)
    assert bootstrap.main(["--install-only"]) == 0
    python.assert_called_once()
    javascript.assert_called_once()
    media.assert_called_once()
    launch.assert_not_called()


@pytest.mark.parametrize("open_browser", [False, True])
def test_browser_opens_only_after_ui_readiness(setup, monkeypatch, open_browser):
    order = []
    child = Mock()
    child.wait.return_value = 0
    child.poll.return_value = 0
    popen = Mock(return_value=child)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", popen)
    monkeypatch.setattr(bootstrap, "wait_for_ui", lambda child: order.append("ready"))
    monkeypatch.setattr(bootstrap.webbrowser, "open", lambda *args, **kwargs: order.append("browser") or True)
    media = {"FFMPEG_PATH": "local ffmpeg.exe", "FFPROBE_PATH": "local ffprobe.exe"}
    assert bootstrap.launch("node", setup.root / "python.exe", media, open_browser=open_browser) == 0
    assert order == (["ready", "browser"] if open_browser else ["ready"])
    assert popen.call_args.kwargs["env"]["FFMPEG_PATH"] == "local ffmpeg.exe"


def test_readiness_requires_ui_and_media_capable_backend(setup, monkeypatch):
    health = {"status": "ok", "capabilities": {"ffmpeg": True, "ffprobe": True}}
    responses = iter([Response(json.dumps(health).encode()), Response(b'<div id="root"></div>')])
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", lambda *args, **kwargs: next(responses))
    bootstrap.wait_for_ui(SimpleNamespace(poll=lambda: None), timeout=1)


def test_failed_startup_does_not_open_browser(setup, monkeypatch):
    with pytest.raises(RuntimeError, match="ports 8000/5173"):
        bootstrap.wait_for_ui(SimpleNamespace(poll=lambda: 1), timeout=1)


def test_setup_error_returns_nonzero_instead_of_starting_servers(setup, monkeypatch):
    monkeypatch.setattr(bootstrap, "find_node", Mock(side_effect=RuntimeError("Node.js missing")))
    launch = Mock()
    monkeypatch.setattr(bootstrap, "launch", launch)
    assert bootstrap.main([]) == 1
    launch.assert_not_called()


def test_manifest_stamp_changes_when_dependencies_change(setup):
    requirement = setup.root / "requirements.txt"
    requirement.write_text("first", encoding="utf-8")
    first = bootstrap.digest_files([requirement])
    requirement.write_text("second", encoding="utf-8")
    assert first != bootstrap.digest_files([requirement])
    stamp = setup.tools / "stamp"
    bootstrap.write_stamp(stamp, first)
    assert bootstrap.stamp_matches(stamp, first)
    assert not bootstrap.stamp_matches(stamp, "different")
