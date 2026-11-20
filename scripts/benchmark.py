"""Measure real local-video analysis through the running GTA Clip Studio API."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import platform
import sys
import tempfile
import time
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
CSV_FIELDS: tuple[str, ...] = (
    "source_path", "project_id", "job_id", "run", "status", "started_at",
    "duration", "wall_seconds", "speed_ratio", "peak_rss", "peak_rss_unit",
    "cache_state", "cache_note", "os_cache_state", "error", "cancel_requested",
    "settings_json", "stage_metrics_json", "memory_json",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def finite_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def calculate_speed_ratio(duration, wall_seconds) -> float | None:
    """Source seconds per measured wall second; undefined inputs stay absent."""
    duration, wall_seconds = finite_number(duration), finite_number(wall_seconds)
    if duration is None or wall_seconds is None or duration <= 0 or wall_seconds <= 0:
        return None
    ratio = duration / wall_seconds
    return ratio if math.isfinite(ratio) else None


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Local paths/settings should not be forwarded through an environment proxy.
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        request = Request(
            self.base_url + path, data=data, method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
            return json.loads(raw) if raw else None
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path}: HTTP {exc.code}: {detail}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise RuntimeError(f"{method} {path}: {exc}") from exc


class MemorySampler:
    """Optional sampled RSS sum for an explicitly identified backend process tree."""

    def __init__(self, pid: int | None = None):
        self.pid = pid
        self.process = None
        self.psutil = None
        if pid is not None:
            try:
                psutil = import_module("psutil")
            except ImportError as exc:
                raise RuntimeError("--pid requires: python -m pip install psutil") from exc
            self.psutil = psutil
            try:
                self.process = psutil.Process(pid)
            except psutil.Error as exc:
                raise RuntimeError(f"Cannot inspect backend PID {pid}: {exc}") from exc
        self.reset()

    def reset(self):
        self.peak = None
        self.samples = 0
        self.last_error = None

    def sample(self):
        process, psutil = self.process, self.psutil
        if process is None or psutil is None:
            return
        try:
            if not process.is_running():
                self.last_error = "Backend PID exited or was reused"
                return
            rss = process.memory_info().rss
            for child in process.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    continue
            self.peak = max(self.peak or 0, rss)
            self.samples += 1
        except psutil.Error as exc:
            self.last_error = str(exc)

    def report(self) -> dict:
        return {
            "backend_pid": self.pid,
            "samples": self.samples,
            "method": "sampled backend-plus-descendants RSS sum" if self.pid else "unavailable: no --pid supplied",
            "last_error": self.last_error,
            "limitations": "Not an OS high-water mark; short-lived children may be missed and shared pages may be counted twice.",
        }


def stage_sample(job: dict, wall_seconds: float) -> dict:
    return {
        "observed_wall_seconds": wall_seconds,
        "status": job.get("status"),
        "stage": job.get("stage"),
        "processed_seconds": finite_number(job.get("processed_seconds")),
        "total_seconds": finite_number(job.get("total_seconds")),
        "api_elapsed_seconds": finite_number(job.get("elapsed_seconds")),
        "api_speed_ratio": finite_number(job.get("speed_ratio")),
        "api_eta_seconds": finite_number(job.get("eta_seconds")),
        "progress": finite_number(job.get("progress")),
        "detail": job.get("detail"),
    }


def summarize_stages(samples: list[dict]) -> list[dict]:
    """Summarize observations, not inferred exact stage start/finish times."""
    stages = {}
    for sample in samples:
        name = sample["stage"] or "unknown"
        if name not in stages:
            stages[name] = {
                "stage": name, "observations": 0,
                "first_observed_wall_seconds": sample["observed_wall_seconds"],
            }
        stage = stages[name]
        stage["observations"] += 1
        stage["last_observed_wall_seconds"] = sample["observed_wall_seconds"]
        for field in ("processed_seconds", "total_seconds", "api_elapsed_seconds", "api_speed_ratio", "api_eta_seconds", "progress"):
            stage["last_" + field] = sample[field]
    return list(stages.values())


def profile_analysis(
    client: ApiClient, project: dict, settings: dict, *, run_index: int,
    cache_state: str, cache_note: str, poll_interval: float = 1,
    timeout: float = 86400, memory: MemorySampler | None = None, quiet: bool = False,
) -> dict:
    record = {
        "source_path": project["source_path"], "project_id": project["id"],
        "job_id": None, "run": run_index, "status": "error", "started_at": utc_now(),
        "duration": finite_number((project.get("metadata") or {}).get("duration")),
        "project_metadata": project.get("metadata") or {},
        "cache_state": cache_state, "cache_note": cache_note, "os_cache_state": "uncontrolled",
        "settings": dict(settings), "stage_samples": [], "error": None,
        "cancel_requested": False,
    }
    memory = memory or MemorySampler()
    memory.reset()
    memory.sample()
    # Import/probe and settings fetch are deliberately outside the analysis timer.
    started = time.perf_counter()
    try:
        job = client.request("POST", f"/api/projects/{project['id']}/analyze", {"force": True, "auto_enhance": False})
        if not isinstance(job, dict) or not job.get("id"):
            raise RuntimeError("Analyze response did not contain a job ID")
        record["job_id"] = job["id"]
        last_display = None
        while True:
            elapsed = time.perf_counter() - started
            memory.sample()
            record["stage_samples"].append(stage_sample(job, elapsed))
            display = (job.get("status"), job.get("stage"), job.get("detail"))
            if not quiet and display != last_display:
                print(f"[{project['id']} run {run_index}, {elapsed:.1f}s] "
                      f"{display[0]} / {display[1]}: {display[2] or ''}", flush=True)
                last_display = display
            status = job.get("status")
            if status in TERMINAL_STATUSES:
                record["status"] = status
                record["error"] = job.get("error")
                break
            if status not in {"queued", "running"}:
                raise RuntimeError(f"Unexpected job status: {status!r}")
            if elapsed >= timeout:
                raise TimeoutError(f"Analysis exceeded --timeout {timeout:g}s (including queue wait)")
            time.sleep(min(poll_interval, timeout - elapsed))
            jobs = client.request("GET", "/api/jobs")
            if not isinstance(jobs, list):
                raise TypeError("GET /api/jobs did not return an array")
            job = next((item for item in jobs if isinstance(item, dict) and item.get("id") == record["job_id"]), None)
            if job is None:
                raise RuntimeError(f"Job {record['job_id']} disappeared from GET /api/jobs")
    except KeyboardInterrupt:
        record["status"] = "interrupted"
        record["error"] = "Interrupted by user"
    except TimeoutError as exc:
        record["status"] = "timeout"
        record["error"] = str(exc)
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        record["error"] = str(exc)
    # Stop timing before cancellation/report writing; never substitute API timing.
    record["wall_seconds"] = time.perf_counter() - started
    if record["status"] not in TERMINAL_STATUSES and record["job_id"]:
        try:
            client.request("POST", f"/api/jobs/{record['job_id']}/cancel")
            record["cancel_requested"] = True
        except (RuntimeError, OSError, ValueError) as exc:
            record["cancel_error"] = str(exc)
    record["finished_at"] = utc_now()
    record["speed_ratio"] = (
        calculate_speed_ratio(record["duration"], record["wall_seconds"])
        if record["status"] == "completed" else None
    )
    record["peak_rss"] = memory.peak
    record["peak_rss_unit"] = "bytes"
    record["memory"] = memory.report()
    record["stage_metrics"] = summarize_stages(record["stage_samples"])
    return record


def report_paths(output: Path) -> tuple[Path, Path]:
    output = Path(output).expanduser()
    if output.is_dir():
        output = output / "benchmark"
    if output.suffix.lower() in {".json", ".csv"}:
        output = output.with_suffix("")
    return Path(str(output) + ".json"), Path(str(output) + ".csv")


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_reports(records: list[dict], output: Path, api: str) -> tuple[Path, Path]:
    """Write paired real-run reports; absent numeric measurements remain blank/null."""
    json_path, csv_path = report_paths(output)
    document = {
        "schema_version": 1, "generated_at": utc_now(), "api": api,
        "client_system": {"platform": platform.platform(), "python": platform.python_version()},
        "measurement": "CLI perf_counter from analyze POST to observed terminal status, including queue/poll/network time; import excluded",
        "runs": records,
    }
    json_content = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for record in records:
        row = {key: record.get(key) for key in CSV_FIELDS}
        for field in ("settings", "stage_metrics", "memory"):
            row[field + "_json"] = json.dumps(record.get(field), ensure_ascii=False, allow_nan=False)
        writer.writerow(row)
    atomic_write(json_path, json_content)
    atomic_write(csv_path, stream.getvalue())
    return json_path, csv_path


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be an integer greater than zero")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="No synthetic inputs, automatic model downloads, cache deletion, settings changes or stored API benchmark timing are used.",
    )
    parser.add_argument("videos", nargs="+", type=Path, help="real local video paths, resolved to absolute server-local paths")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="API base URL (default: %(default)s)")
    parser.add_argument("--output", required=True, type=Path, help="report prefix or .json/.csv filename; writes BOTH formats (existing directory: benchmark.json/csv)")
    parser.add_argument("--repeat", type=positive_int, default=1, help="analyses per imported video; successful repeats reuse feature caches (default: %(default)s)")
    parser.add_argument("--poll-interval", type=positive_float, default=1, help="seconds between job observations (default: %(default)s)")
    parser.add_argument("--timeout", type=positive_float, default=86400, help="per-analysis wall timeout in seconds, including queue wait (default: %(default)s)")
    parser.add_argument("--request-timeout", type=positive_float, default=30, help="individual HTTP request timeout in seconds (default: %(default)s)")
    parser.add_argument("--pid", type=positive_int, help="actual local backend PID; optionally sample its process-tree RSS using psutil")
    parser.add_argument("--quiet", action="store_true", help="suppress per-stage progress messages")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    url = urlsplit(args.api)
    if url.scheme not in {"http", "https"} or not url.netloc or url.query or url.fragment:
        parser.error("--api must be an HTTP(S) base URL without a query or fragment")
    videos = []
    for video in args.videos:
        try:
            video = video.expanduser().resolve(strict=True)
            if not video.is_file():
                raise ValueError("not a regular file")
        except (OSError, ValueError) as exc:
            parser.error(f"Invalid video path {video}: {exc}")
        videos.append(video)
    try:
        memory = MemorySampler(args.pid)
    except RuntimeError as exc:
        parser.error(str(exc))
    client = ApiClient(args.api, args.request_timeout)
    records = []
    stop = False
    try:
        # Also verifies the report destination before queuing expensive work.
        paths = write_reports(records, args.output, client.base_url)
        for video in videos:
            known = client.request("GET", "/api/projects")
            if not isinstance(known, list):
                raise TypeError("GET /api/projects did not return an array")
            known_ids = {item["id"] for item in known if isinstance(item, dict) and "id" in item}
            project = client.request("POST", "/api/projects/local", {"path": str(video)})
            if not isinstance(project, dict) or not project.get("id") or not project.get("source_path"):
                raise RuntimeError("Import response is missing project ID/source_path")
            prior_settings = None
            prior_completed = False
            for run_index in range(1, args.repeat + 1):
                # Refresh real project duration/settings instead of trusting a benchmark entry.
                project = client.request("GET", f"/api/projects/{project['id']}")
                if not isinstance(project, dict) or not project.get("id") or not project.get("source_path"):
                    raise RuntimeError("Project response is missing project ID/source_path")
                metadata = project.get("metadata")
                duration = finite_number(metadata.get("duration")) if isinstance(metadata, dict) else None
                if duration is None or duration < 0:
                    raise RuntimeError("Project metadata.duration is missing or invalid; no benchmark duration will be invented")
                settings = client.request("GET", "/api/settings")
                if not isinstance(settings, dict):
                    raise TypeError("GET /api/settings did not return an object")
                if run_index == 1 and project["id"] not in known_ids:
                    cache_state, cache_note = "cold", "New project ID: no prior project-keyed feature chunks; OS/file caches uncontrolled"
                elif prior_completed and settings == prior_settings:
                    cache_state, cache_note = "warm", "Same project/settings after a completed run; cache eviction or invalidation may still cause misses"
                else:
                    cache_state, cache_note = "unknown", "Existing project, changed settings or incomplete prior run; feature cache may be partial"
                record = profile_analysis(
                    client, project, settings, run_index=run_index, cache_state=cache_state,
                    cache_note=cache_note, poll_interval=args.poll_interval,
                    timeout=args.timeout, memory=memory, quiet=args.quiet,
                )
                records.append(record)
                paths = write_reports(records, args.output, client.base_url)
                ratio = record["speed_ratio"]
                speed = f"{ratio:.2f}x" if ratio is not None else "n/a"
                print(f"{video.name} run {run_index}: {record['status']}, "
                      f"{record['wall_seconds']:.2f}s, {speed}, cache={cache_state}", flush=True)
                if record["error"]:
                    print(record["error"], file=sys.stderr)
                prior_settings, prior_completed = settings, record["status"] == "completed"
                if record["status"] not in TERMINAL_STATUSES:
                    stop = True
                    break
            if stop:
                break
        print(f"Reports: {paths[0]} and {paths[1]}")
    except KeyboardInterrupt:
        print("Interrupted outside analysis; completed run reports are preserved.", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        print(f"Benchmark failed: {exc}. Earlier reports, if written, are preserved.", file=sys.stderr)
        return 1
    if any(record["status"] == "interrupted" for record in records):
        return 130
    return 0 if records and all(record["status"] == "completed" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
