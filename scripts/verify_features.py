"""Real split-export and automatic-ASR smoke test, isolated from user projects.

Windows uses its installed SAPI voice to author a speech fixture. Elsewhere,
supply --speech-wav with a real spoken WAV. No cloud requests or manual caption
injection. Requires cached tiny.en and installed development dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech-wav", type=Path)
    parser.add_argument("--styled", action="store_true", help="Also save and render custom subtitle typography/position/width")
    parser.add_argument("--output", type=Path, default=ROOT / "storage/feature-validation.json")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    (ROOT / "storage").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="feature-validation-", dir=ROOT / "storage") as temporary:
        directory = Path(temporary)
        os.environ["GTA_STORAGE_DIR"] = str(directory / "studio")
        wav = args.speech_wav.resolve() if args.speech_wav else directory / "speech.wav"
        if not args.speech_wav:
            if os.name != "nt":
                parser.error("Use --speech-wav on non-Windows systems")
            script = directory / "speech.vbs"
            script.write_text(
                'Set stream = CreateObject("SAPI.SpFileStream")\n'
                f'stream.Open "{str(wav).replace(chr(34), chr(34) * 2)}", 3, False\n'
                'Set voice = CreateObject("SAPI.SpVoice")\n'
                'Set voice.AudioOutputStream = stream\n'
                'voice.Rate = 0\n'
                'voice.Speak "Look at that car. That was a crazy jump. I cannot believe we survived."\n'
                'stream.Close\n', encoding="utf-8")
            subprocess.run(["cscript.exe", "//nologo", str(script)], check=True, timeout=60)
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.core.config import get_binary
        from backend.core.process import run_process
        from backend.utils.media import probe
        from backend.utils.capabilities import capabilities

        assert capabilities()["caption_model_cached"], "Run run.dev.cmd to cache tiny.en first"
        source = directory / "embedded-streamer.mp4"
        command = [get_binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=320x180:rate=15:duration=12,drawbox=x=0:y=0:w=80:h=80:color=red:t=fill",
            "-i", str(wav), "-af", "apad", "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)]
        generated = run_process(command, timeout=60)
        assert generated.returncode == 0, generated.stderr
        report = {"fixture": "12-second synthetic gameplay with embedded red camera region and Windows SAPI speech"}
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51000)) as http:
            def request(method, path, **kwargs):
                response = http.request(method, path, **kwargs)
                assert response.is_success, f"{path}: {response.status_code} {response.text}"
                return response.json()

            def wait(job_id, timeout=240):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    job = request("GET", f"/api/jobs/{job_id}")
                    if job["status"] not in {"queued", "running"}:
                        assert job["status"] == "completed", job
                        return job
                    time.sleep(.1)
                raise TimeoutError(f"Feature smoke test job timed out: {job_id}")

            settings = request("GET", "/api/settings")
            assert settings["caption_style"] == "standard" and settings["caption_provider"] == "local"
            settings.update(clip_duration=10, max_clips=2, sampling_interval=5)
            request("PUT", "/api/settings", json=settings)
            project = request("POST", "/api/projects/local", json={"path": str(source)})
            identifier = project["id"]
            assert project["layout"]["mode"] == "split" and project["layout"]["facecam"] is None
            layout = {"mode": "split", "facecam": {"x": 0, "y": 0, "width": .25, "height": 80 / 180},
                "gameplay": {"x": 0, "y": 80 / 180, "width": 1, "height": 100 / 180}, "split_ratio": .5,
                "gameplay_fit": "fit", "facecam_fit": "fill", "caption_position": "center"}
            request("PATCH", f"/api/projects/{identifier}/layout", json=layout)
            analyze = request("POST", f"/api/projects/{identifier}/analyze")
            report["analysis_seconds"] = wait(analyze["id"])["elapsed_seconds"]
            deadline = time.monotonic() + 5
            captions = None
            while captions is None and time.monotonic() < deadline:
                jobs = request("GET", "/api/jobs")
                captions = next((item for item in jobs if item["project_id"] == identifier and item["kind"] == "captions"), None)
                if captions is None:
                    time.sleep(.05)
            assert captions is not None, "Automatic caption job was not queued"
            report["caption_job_seconds"] = wait(captions["id"])["elapsed_seconds"]
            highlights = request("GET", f"/api/projects/{identifier}/highlights")
            assert highlights and all(item["caption_status"] == "ready" for item in highlights), highlights
            highlight = highlights[0]
            assert highlight["transcript"] and any(segment["words"] for segment in highlight["transcript"])
            report["recognized_text"] = " ".join(segment["text"] for segment in highlight["transcript"])
            report["word_count"] = sum(len(segment["words"]) for segment in highlight["transcript"])
            if args.styled:
                saved = request("PATCH", f"/api/highlights/{highlight['id']}", json={"subtitle_style": {
                    "font_size": 54, "text_color": "#FFFFDD", "highlight_color": "#88FF44",
                    "outline_width": 3, "background_opacity": .25, "x": .5, "y": .7, "width": .65}})
                assert saved["transcript"] == highlight["transcript"]
                report["subtitle_style"] = saved["subtitle_style"]
            report["exports"] = []
            for style in ("standard", "animated", "none"):
                job = request("POST", f"/api/highlights/{highlight['id']}/export", json={"caption_style": style})
                done = wait(job["id"])
                path = Path(done["result"]["output_path"])
                metadata = probe(path)
                assert (metadata["width"], metadata["height"], round(metadata["fps"])) == (720, 1280, 30)
                decoded = run_process([get_binary("ffmpeg"), "-v", "error", "-nostdin", "-threads", "1", "-i", str(path), "-t", "0.3", "-f", "null", "-"], timeout=30)
                assert decoded.returncode == 0, decoded.stderr
                report["exports"].append({"style": style, "seconds": done["elapsed_seconds"], "bytes": path.stat().st_size})
            report["cloud_requests"] = request("GET", "/api/usage")["request_count"]
            assert report["cloud_requests"] == 0
            report["passed"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
