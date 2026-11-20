"""Tiny.en setup for default offline captions, also called by run.dev."""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import import_module
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[1] / "storage" / "models" / "tiny.en"
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")


def download_tiny_en() -> tuple[Path, Path]:
    try:
        download_model = import_module("faster_whisper.utils").download_model
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Install the optional dependency in the backend environment first: "
            "python -m pip install faster-whisper"
        ) from exc
    # Runtime prefers the project copy, with the HF snapshot as a legacy fallback.
    cached = Path(download_model("tiny.en")).resolve()
    if not all((cached / name).is_file() for name in REQUIRED_FILES):
        raise RuntimeError(f"Downloaded tiny.en snapshot is incomplete: {cached}")
    # Keep a plain offline copy at the project's documented model location too.
    # Copying the existing snapshot does not make a second network request.
    if cached != MODEL_DIR.resolve():
        shutil.copytree(cached, MODEL_DIR, dirs_exist_ok=True)
    return MODEL_DIR, cached


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Analysis/export never invoke this helper. Runtime uses local files only; no video/audio is uploaded.",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="opt in to contacting Hugging Face and downloading tiny.en (uses disk and network data)",
    )
    args = parser.parse_args(argv)
    if not args.download:
        parser.error("No download requested. Read --help, then explicitly pass --download to opt in.")
    print("Downloading optional tiny.en from Hugging Face; no media will be uploaded.", flush=True)
    try:
        destination, cached = download_tiny_en()
    except Exception as exc:  # noqa: BLE001 - CLI boundary for version-dependent model-hub errors.
        print(f"Model setup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Project model files: {destination}")
    print(f"Runtime Hugging Face cache: {cached}")
    print("Local captions use the project model copy; restart the backend after setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
