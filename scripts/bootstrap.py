"""Idempotent Windows development setup. No administrator rights or AI downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "storage" / "tools"
FFMPEG_DIR = TOOLS / "ffmpeg"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
CHECKSUM_URL = FFMPEG_URL + ".sha256"
UI_URL = "http://127.0.0.1:5173"
MAX_DOWNLOAD_BYTES = 512 * 1024**2


def message(text: str) -> None:
    print(f"[setup] {text}", flush=True)


def execute(command: list[str], *, capture=False, timeout=1200):
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                            capture_output=capture, timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "See the output above.").strip()[-3000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {subprocess.list2cmdline(command)}\n{detail}")
    return result


@contextmanager
def setup_lock():
    TOOLS.mkdir(parents=True, exist_ok=True)
    with (TOOLS / "bootstrap.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another launcher is installing dependencies. Wait for it to finish.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def digest_files(paths: list[Path], extra="") -> str:
    checksum = hashlib.sha256(extra.encode("utf-8"))
    for path in paths:
        checksum.update(path.name.encode("utf-8"))
        checksum.update(path.read_bytes() if path.is_file() else b"<missing>")
    return checksum.hexdigest()


def write_stamp(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def stamp_matches(path: Path, expected: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == expected
    except OSError:
        return False


def ensure_python() -> Path:
    if sys.version_info < (3, 11) or struct.calcsize("P") != 8:
        raise RuntimeError("Run this launcher with 64-bit Python 3.11 or newer (3.11/3.12 recommended).")
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        message("Creating .venv ...")
        execute([sys.executable, "-m", "venv", str(ROOT / ".venv")], timeout=120)
    execute([str(python), "-c", "import sys,struct; assert sys.version_info >= (3,11) and struct.calcsize('P') == 8, 'Use 64-bit Python 3.11+'"], capture=True, timeout=30)
    expected = digest_files([ROOT / "requirements.txt"], str(python.resolve()))
    stamp = ROOT / ".venv" / ".gta-requirements.sha256"
    check = [str(python), "-c", "import fastapi,uvicorn,numpy,psutil,multipart,yt_dlp,httpx,jwt; from jwt.algorithms import RSAAlgorithm"]
    healthy = subprocess.run(check, cwd=ROOT, capture_output=True, timeout=60, check=False).returncode == 0
    if not healthy or not stamp_matches(stamp, expected):
        message("Installing/checking base Python dependencies ...")
        execute([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", str(ROOT / "requirements.txt")])
        execute(check, capture=True, timeout=60)
        execute([str(python), "-m", "pip", "check"], capture=True, timeout=60)
        write_stamp(stamp, expected)
    else:
        message("Python dependencies are already installed.")
    return python


def ensure_captions(python: Path) -> None:
    """Set up the small offline speech model, unless captions/cloud opt-out wins."""
    import sqlite3
    database = Path(os.environ.get("GTA_STORAGE_DIR") or ROOT / "storage").expanduser().resolve() / "studio.sqlite3"
    if database.is_file():
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
                row = db.execute("SELECT data FROM settings WHERE id=1").fetchone()
            preferences = json.loads(row[0]) if row else {}
            if preferences.get("caption_style") == "none" or preferences.get("caption_provider") == "groq":
                message("Local caption model setup skipped: saved settings use No captions or Groq.")
                return
        except (sqlite3.Error, ValueError):
            pass
    requirement = ROOT / "requirements-captions.txt"
    expected = digest_files([requirement], str(python.resolve()))
    stamp = ROOT / ".venv" / ".gta-captions.sha256"
    package_check = [str(python), "-c", "import faster_whisper"]
    installed = subprocess.run(package_check, cwd=ROOT, capture_output=True, timeout=60, check=False).returncode == 0
    if not installed or not stamp_matches(stamp, expected):
        message("Installing local automatic-caption dependencies ...")
        execute([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", str(requirement)])
        execute(package_check, capture=True, timeout=60)
        write_stamp(stamp, expected)
    cached = subprocess.run([str(python), "-c", "from backend.processing.transcription import model_is_cached; raise SystemExit(0 if model_is_cached() else 1)"],
                            cwd=ROOT, capture_output=True, timeout=30, check=False).returncode == 0
    if not cached:
        message("Downloading tiny.en for default offline captions (small model, no video uploads) ...")
        execute([str(python), str(ROOT / "scripts/download_model.py"), "--download"], timeout=1200)
    message("Automatic local captions are ready; choose No captions in Settings to opt out.")


def find_node() -> tuple[str, str, Path]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js 20+ is required. Install Node.js LTS from https://nodejs.org, reopen this terminal, and run again.")
    version = execute([node, "--version"], capture=True, timeout=15).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 20:
        raise RuntimeError(f"Node.js 20+ is required; found {version}. Install a current LTS release.")
    npm = shutil.which("npm")
    candidates = [Path(node).parent / "node_modules/npm/bin/npm-cli.js"]
    if npm:
        resolved = Path(npm).resolve()
        candidates.extend([resolved, resolved.parent / "npm-cli.js", resolved.parent / "node_modules/npm/bin/npm-cli.js"])
    npm_cli = next((p for p in candidates if p.name == "npm-cli.js" and p.is_file()), None)
    if not npm_cli:
        raise RuntimeError("npm was not found alongside Node.js. Repair/reinstall Node.js with npm included.")
    return node, version, npm_cli


def ensure_javascript(node: str, version: str, npm_cli: Path) -> None:
    manifests = [ROOT / "package.json", ROOT / "package-lock.json", ROOT / "frontend/package.json", ROOT / "frontend/package-lock.json"]
    stamp = TOOLS / "javascript.sha256"
    expected = digest_files(manifests, version)
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    react = ROOT / "frontend/node_modules/react/package.json"
    if not vite.is_file() or not react.is_file() or not stamp_matches(stamp, expected):
        message("Installing/checking JavaScript dependencies ...")
        # Invoking npm's JS entry point avoids Windows .cmd quoting/shell issues.
        # The root postinstall script installs the frontend dependencies too.
        execute([node, str(npm_cli), "install", "--no-audit", "--no-fund"])
        if not vite.is_file() or not react.is_file():
            raise RuntimeError("Frontend dependencies were not installed. Check npm output and run npm install again.")
        write_stamp(stamp, digest_files(manifests, version))
    else:
        message("JavaScript dependencies are already installed.")


def binary_works(path: str | Path, name: str) -> bool:
    try:
        result = execute([str(path), "-version"], capture=True, timeout=20)
        if not result.stdout.lower().startswith(name + " version"):
            return False
        if name == "ffmpeg":
            encoders = execute([str(path), "-hide_banner", "-encoders"], capture=True, timeout=20).stdout
            filters = execute([str(path), "-hide_banner", "-filters"], capture=True, timeout=20).stdout
            return all(re.search(rf"\b{item}\b", encoders) for item in ("libx264", "aac")) and all(
                re.search(rf"\b{item}\b", filters) for item in ("ass", "scale", "overlay", "boxblur"))
        return True
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return False


def resolve_binary(name: str) -> Path | None:
    configured = os.environ.get(f"{name.upper()}_PATH")
    if configured:
        path = Path(configured.strip('"')).expanduser()
        if not binary_works(path, name):
            raise RuntimeError(f"{name.upper()}_PATH is invalid or lacks required codecs/filters: {path}. Correct or remove that environment variable and retry.")
        return path.resolve()
    filename = name + (".exe" if os.name == "nt" else "")
    bundled = FFMPEG_DIR / "bin" / filename
    if bundled.is_file() and binary_works(bundled, name):
        return bundled
    system = shutil.which(name)
    return Path(system) if system and binary_works(system, name) else None


def request_url(url: str, timeout=90, *, offset=0):
    headers = {"User-Agent": "GTA-Clip-Studio-Setup/1.0", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def download_archive(url: str, destination: Path, expected: str) -> None:
    """Resume a checksum-addressed partial download without trusting its bytes."""
    checksum = hashlib.sha256()
    received = destination.stat().st_size if destination.is_file() else 0
    if received > MAX_DOWNLOAD_BYTES:
        destination.unlink()
        raise RuntimeError("Cached FFmpeg download exceeds the safety size limit; retry")
    if received:
        with destination.open("rb") as cached:
            while block := cached.read(1024 * 1024):
                checksum.update(block)
        if checksum.hexdigest().lower() == expected.lower():
            return
        message(f"Resuming FFmpeg download from {received / 1024**2:.1f} MiB ...")
    started = last_message = time.monotonic()
    try:
        response = request_url(url, offset=received) if received else request_url(url)
    except urllib.error.HTTPError as exc:
        if received and exc.code == 416:
            # A full-sized but corrupt cache cannot be repaired by appending.
            destination.unlink(missing_ok=True)
            raise OSError("Cached download did not match its checksum; restarting from zero") from exc
        raise
    with response:
        if not response.geturl().startswith("https://"):
            raise RuntimeError("Refusing an insecure FFmpeg download redirect")
        if received and getattr(response, "status", 200) != 206:
            # Servers may ignore Range. Restart rather than append corrupt data.
            received = 0
            checksum = hashlib.sha256()
        elif received:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {received}-"):
                raise RuntimeError("FFmpeg server returned an unexpected resume range; retry")
        remaining = int(response.headers.get("Content-Length") or 0)
        total = received + remaining if remaining else 0
        if total > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("FFmpeg archive is larger than the 512 MiB setup download limit")
        with destination.open("ab" if received else "wb") as output:
            while block := response.read(256 * 1024):
                received += len(block)
                if received > MAX_DOWNLOAD_BYTES or time.monotonic() - started > 900:
                    raise RuntimeError("FFmpeg download exceeded its size/time safety limit; retry on a stable connection")
                checksum.update(block)
                output.write(block)
                if time.monotonic() - last_message >= 2:
                    message(f"FFmpeg download: {received / 1024**2:.1f} MiB" + (f" / {total / 1024**2:.1f} MiB" if total else ""))
                    last_message = time.monotonic()
    if total and total != received:
        raise OSError("FFmpeg download was incomplete; partial bytes are preserved for retry")
    if checksum.hexdigest().lower() != expected.lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg SHA-256 verification failed. Nothing was installed; rerun to fetch a matching release.")


def extract_tools(archive: Path, destination: Path) -> None:
    """Extract just the two named executables; never trust ZIP entry paths."""
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            matches = [info for info in zipped.infolist() if not info.is_dir() and info.filename.replace("\\", "/").endswith("/bin/" + name)]
            if len(matches) != 1 or matches[0].file_size > MAX_DOWNLOAD_BYTES:
                raise RuntimeError(f"The FFmpeg archive does not contain exactly one valid bin/{name}")
            with zipped.open(matches[0]) as source, (destination / name).open("wb") as output:
                shutil.copyfileobj(source, output, length=256 * 1024)


def install_ffmpeg() -> None:
    if os.name != "nt" or os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64":
        raise RuntimeError("Automatic FFmpeg setup supports Windows x64. Install FFmpeg/FFprobe through your OS package manager, then retry.")
    if shutil.disk_usage(TOOLS).free < 1024**3:
        raise RuntimeError("Free at least 1 GiB on the project drive before installing FFmpeg.")
    message("FFmpeg/FFprobe missing or incompatible. Downloading a Windows x64 build from gyan.dev (up to a few hundred MB).")
    with request_url(CHECKSUM_URL) as response:
        checksum_text = response.read(4096).decode("ascii").strip()
    match = re.match(r"^([0-9a-fA-F]{64})(?:\s|$)", checksum_text)
    if not match:
        raise RuntimeError("The FFmpeg publisher returned an invalid SHA-256 checksum")
    downloads = TOOLS / "downloads"
    downloads.mkdir(exist_ok=True)
    archive = downloads / f"ffmpeg-{match[1].lower()}.zip.part"
    for attempt in range(3):
        try:
            download_archive(FFMPEG_URL, archive, match[1])
            break
        except (OSError, urllib.error.URLError) as exc:
            if attempt == 2:
                raise RuntimeError(f"FFmpeg download interrupted: {exc}. Partial download is preserved; rerun to resume.") from exc
            message(f"Download interrupted ({exc}); retrying with saved bytes ...")
            time.sleep(2)
    with tempfile.TemporaryDirectory(prefix=".ffmpeg-setup-", dir=TOOLS) as temporary:
        staging = Path(temporary)
        package = staging / "package"
        extract_tools(archive, package / "bin")
        for name in ("ffmpeg", "ffprobe"):
            if not binary_works(package / "bin" / f"{name}.exe", name):
                raise RuntimeError(f"The downloaded {name} could not run or lacks required encoding/caption support")
        (package / "source.json").write_text(json.dumps({"url": FFMPEG_URL, "sha256": match[1]}, indent=2), encoding="utf-8")
        backup = TOOLS / f".ffmpeg-backup-{uuid.uuid4().hex}"
        existed = FFMPEG_DIR.exists()
        if existed:
            FFMPEG_DIR.rename(backup)
        try:
            package.rename(FFMPEG_DIR)
        except BaseException:
            if existed:
                backup.rename(FFMPEG_DIR)
            raise
        if existed:
            shutil.rmtree(backup)
    archive.unlink(missing_ok=True)
    message("FFmpeg and FFprobe installed and verified locally; no PATH changes needed.")


def ensure_media() -> dict[str, str]:
    binaries = {name: resolve_binary(name) for name in ("ffmpeg", "ffprobe")}
    if not all(binaries.values()):
        install_ffmpeg()
        binaries = {name: resolve_binary(name) for name in binaries}
    if not all(binaries.values()):
        raise RuntimeError("Media tool verification failed; see README.md for manual FFmpeg setup")
    for name, path in binaries.items():
        message(f"{name}: {path}")
    return {f"{name.upper()}_PATH": str(path) for name, path in binaries.items()}


def wait_for_ui(child, timeout=120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError("Server startup stopped. If ports 8000/5173 are occupied, stop the old server with Ctrl+C and rerun this launcher.")
        try:
            with urllib.request.urlopen(UI_URL + "/api/health", timeout=2) as response:
                health = json.load(response)
            with urllib.request.urlopen(UI_URL, timeout=2) as response:
                html = response.read(128 * 1024)
            capabilities = health.get("capabilities", {})
            if health.get("status") == "ok" and capabilities.get("ffmpeg") and capabilities.get("ffprobe") and b'<div id="root"' in html:
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(.25)
    raise RuntimeError("The UI/API did not become ready in 120 seconds. Check server output, ports, and Windows Firewall prompts.")


def launch(node: str, python: Path, media: dict[str, str], *, open_browser=True) -> int:
    environment = {**os.environ, **media, "GTA_PYTHON": str(python)}
    child = subprocess.Popen([node, str(ROOT / "scripts/dev.mjs")], cwd=ROOT, env=environment)
    try:
        wait_for_ui(child)
        message(f"Ready: {UI_URL} — leave this window open; Ctrl+C stops the servers.")
        if open_browser:
            try:
                if not webbrowser.open(UI_URL, new=2):
                    message(f"Browser could not be opened automatically. Open {UI_URL} manually.")
            except (OSError, webbrowser.Error):
                message(f"Browser could not be opened automatically. Open {UI_URL} manually.")
        return child.wait()
    finally:
        if child.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
            else:
                child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install GTA Clip Studio dependencies, start localhost servers, and open the UI.")
    parser.add_argument("--install-only", action="store_true", help="Install/verify dependencies without starting servers or opening a browser")
    parser.add_argument("--no-open", action="store_true", help="Start normally, but do not open a browser")
    parser.add_argument("--skip-caption-setup", action="store_true", help="Skip local speech-model installation (use Groq, manual transcripts or No captions)")
    args = parser.parse_args(argv)
    try:
        node, version, npm_cli = find_node()
        with setup_lock():
            python = ensure_python()
            ensure_javascript(node, version, npm_cli)
            media = ensure_media()
            if not args.skip_caption_setup:
                ensure_captions(python)
        if args.install_only:
            message("All dependencies are ready. Run run.dev.cmd to start GTA Clip Studio.")
            return 0
        return launch(node, python, media, open_browser=not args.no_open)
    except KeyboardInterrupt:
        message("Stopped. Completed analysis chunks and project data are preserved.")
        return 0
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"\n[setup] ERROR: {exc}\nFix the problem and rerun run.dev.cmd. Existing projects are not removed.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
