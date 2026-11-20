"""Bounded, real-media API smoke test; NOT a long-recording speed benchmark.

Generates a 70-second synthetic source in temporary storage, uses the real
worker/SQLite/FFmpeg paths, verifies all three export styles, cancellation and
restart recovery, then writes a measured JSON report. No model or network.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", help="Full FFmpeg executable path, otherwise PATH/FFMPEG_PATH")
    parser.add_argument("--ffprobe", help="Full FFprobe executable path, otherwise PATH/FFPROBE_PATH")
    parser.add_argument("--output", type=Path, default=ROOT / "storage" / "validation-results.json")
    args = parser.parse_args()
    if args.ffmpeg:
        os.environ["FFMPEG_PATH"] = args.ffmpeg
    if args.ffprobe:
        os.environ["FFPROBE_PATH"] = args.ffprobe
    sys.path.insert(0, str(ROOT))
    (ROOT / "storage").mkdir(exist_ok=True)
    report = {"kind": "synthetic-smoke-test", "source_seconds": 70,
              "platform": platform.platform(), "cpu": platform.processor(), "checks": []}
    with tempfile.TemporaryDirectory(prefix="gta-validation-", dir=ROOT / "storage") as temporary:
        directory = Path(temporary)
        # Must be set before importing any backend modules.
        os.environ["GTA_STORAGE_DIR"] = str(directory / "studio")
        import numpy as np
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.core.config import get_binary
        from backend.core.process import run_process
        from backend.database import store
        from backend.utils.media import probe

        wav = directory / "fixture.wav"
        source = directory / "synthetic.mp4"
        with wave.open(str(wav), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            for second in range(70):
                times = np.arange(16000, dtype=np.float32) / 16000 + second
                amplitude = 0.7 if 12 <= second < 15 or 41 <= second < 44 else 0.015
                data = amplitude * np.sin(2 * np.pi * (900 if amplitude > .1 else 220) * times)
                audio.writeframes((data * 32767).astype("<i2").tobytes())
        generated = run_process([get_binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-threads", "1",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=70", "-i", str(wav),
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source)], timeout=90)
        if generated.returncode:
            raise RuntimeError(generated.stderr)

        def client():
            return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 56780))

        def request(http, method, path, **kwargs) -> Any:
            response = http.request(method, path, **kwargs)
            if not response.is_success:
                raise AssertionError(f"{method} {path}: {response.status_code} {response.text}")
            return response.json() if response.content else None

        def wait(http, job_id, terminal=True, timeout=180):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                item = request(http, "GET", f"/api/jobs/{job_id}")
                if terminal and item["status"] not in {"queued", "running"}:
                    return item
                if not terminal and item["status"] == "running" and item["stage"] == "scan":
                    return item
                if not terminal and item["status"] in {"failed", "completed", "cancelled"}:
                    raise AssertionError(f"Scan ended before interruption test: {item}")
                time.sleep(.1)
            raise TimeoutError(f"Smoke job timed out after {timeout}s")

        with client() as http:
            settings = request(http, "GET", "/api/settings")
            settings.update(clip_duration=10, max_clips=3, sampling_interval=5, caption_style="none", analysis_chunk_seconds=60, default_layout="blurred")
            request(http, "PUT", "/api/settings", json=settings)
            project = request(http, "POST", "/api/projects/local", json={"path": str(source.resolve())})
            identifier = project["id"]
            assert project["metadata"]["duration"] >= 69
            partial = http.get(f"/api/projects/{identifier}/media", headers={"Range": "bytes=0-99"})
            assert partial.status_code == 206 and len(partial.content) == 100
            report["checks"].extend(["actual-local-import-and-ffprobe", "media-byte-range"])
            job = request(http, "POST", f"/api/projects/{identifier}/analyze", json={"force": True})
            completed = wait(http, job["id"])
            assert completed["status"] == "completed", completed
            report["analysis"] = completed["result"]["benchmark"]
            highlights = request(http, "GET", f"/api/projects/{identifier}/highlights")
            assert 0 < len(highlights) <= 3, highlights
            snapshot = request(http, "GET", f"/api/projects/{identifier}/events")
            assert snapshot["complete"] and snapshot["events"]
            report["checks"].extend(["real-signal-detection-ranking", "measured-event-timeline"])
            highlight = highlights[0]
            # Explicit authored test text, not an ASR result or invented dialogue.
            transcript = [{"start": .25, "end": 2, "text": "Validation clip", "words": [
                {"start": .25, "end": 1, "word": "Validation"}, {"start": 1, "end": 2, "word": "clip"}]}]
            request(http, "PATCH", f"/api/highlights/{highlight['id']}", json={"transcript": transcript})
            export_reports = []
            for style in ("none", "standard", "animated"):
                job = request(http, "POST", f"/api/highlights/{highlight['id']}/export", json={"caption_style": style})
                exported = wait(http, job["id"])
                assert exported["status"] == "completed", exported
                path = Path(exported["result"]["output_path"])
                metadata = probe(path)
                assert (metadata["width"], metadata["height"], round(metadata["fps"])) == (720, 1280, 30), metadata
                assert metadata["video_codec"] == "h264" and metadata["audio_codec"] == "aac"
                decoded = run_process([get_binary("ffmpeg"), "-v", "error", "-nostdin", "-threads", "1",
                    "-i", str(path), "-t", "0.5", "-f", "null", "-"], timeout=30)
                assert decoded.returncode == 0, decoded.stderr
                export_reports.append({"style": style, "wall_seconds": exported["elapsed_seconds"], "bytes": path.stat().st_size})
            report["exports"] = export_reports
            report["checks"].append("playable-720x1280-h264-aac-all-caption-styles")
            settings["sampling_interval"] = 1
            request(http, "PUT", "/api/settings", json=settings)
            cancel_job = request(http, "POST", f"/api/projects/{identifier}/analyze", json={"force": True})
            wait(http, cancel_job["id"], terminal=False)
            request(http, "POST", f"/api/jobs/{cancel_job['id']}/cancel")
            cancelled = wait(http, cancel_job["id"])
            assert cancelled["status"] == "cancelled", cancelled
            assert len(request(http, "GET", f"/api/projects/{identifier}/highlights")) == len(highlights)
            report["checks"].append("running-job-cancellation-preserves-results")
            resumed_job = request(http, "POST", f"/api/projects/{identifier}/analyze", json={"force": True})
            wait(http, resumed_job["id"], terminal=False)
            # Leaving TestClient runs the real shutdown hook during active work.
        interrupted = store.get("jobs", resumed_job["id"])
        assert interrupted is not None and interrupted["status"] == "queued"
        with client() as http:
            recovered = wait(http, resumed_job["id"])
            assert recovered["status"] == "completed", recovered
            assert len(request(http, "GET", "/api/exports")) == 3
            assert request(http, "GET", f"/api/projects/{identifier}")["source_path"] == str(source.resolve())
            report["checks"].append("active-job-resumes-and-results-survive-restart")
        report["passed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
