"""Small deterministic CLI/report tests: no API server, model, or real video needed."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import benchmark, download_model


class MathTests(unittest.TestCase):
    def test_speed_is_source_seconds_per_own_wall_second(self):
        self.assertEqual(benchmark.calculate_speed_ratio(3600, 120), 30)
        self.assertEqual(benchmark.calculate_speed_ratio(10, 20), 0.5)

    def test_unknown_zero_negative_and_nonfinite_inputs_stay_absent(self):
        for value in (None, 0, -1, float("nan"), float("inf"), True, "3600"):
            with self.subTest(value=value):
                self.assertIsNone(benchmark.calculate_speed_ratio(value, 1))
                self.assertIsNone(benchmark.calculate_speed_ratio(1, value))
        self.assertIsNone(benchmark.calculate_speed_ratio(1e308, 1e-308))

    def test_stage_metrics_preserve_stage_local_values(self):
        scan = benchmark.stage_sample({"stage": "scan", "processed_seconds": 20, "total_seconds": 60, "elapsed_seconds": 4, "speed_ratio": 5}, 6)
        rank = benchmark.stage_sample({"stage": "rank", "processed_seconds": 0, "total_seconds": 60, "elapsed_seconds": 0, "speed_ratio": None}, 7)
        stages = benchmark.summarize_stages([scan, rank])
        self.assertEqual([stage["stage"] for stage in stages], ["scan", "rank"])
        self.assertEqual(stages[0]["last_api_speed_ratio"], 5)
        self.assertEqual(stages[1]["last_processed_seconds"], 0)
        self.assertEqual(stages[1]["first_observed_wall_seconds"], 7)
        self.assertIsNone(stages[1]["last_api_speed_ratio"])


class OutputTests(unittest.TestCase):
    def test_json_csv_pair_roundtrip_and_missing_rss(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = {
                "source_path": 'C:\\videos\\game, "one".mp4', "project_id": "p", "job_id": "j",
                "run": 1, "status": "completed", "duration": 60, "wall_seconds": 4,
                "speed_ratio": 15, "peak_rss": None, "peak_rss_unit": "bytes", "cache_state": "cold",
                "settings": {"cpu_workers": 2, "memory_mode": "low", "output_folder": "exports/é"},
                "stage_metrics": [{"stage": "scan", "last_processed_seconds": 60}],
                "memory": {"backend_pid": None, "samples": 0},
            }
            output = Path(temporary) / "nested" / "results.json"
            json_path, csv_path = benchmark.write_reports([record], output, "http://127.0.0.1:8000")
            self.assertEqual(json_path, output)
            self.assertEqual(csv_path, output.with_suffix(".csv"))
            document = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(document["runs"], [record])
            self.assertIsNone(document["runs"][0]["peak_rss"])
            with csv_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_path"], record["source_path"])
            self.assertEqual(rows[0]["peak_rss"], "")
            self.assertEqual(json.loads(rows[0]["settings_json"]), record["settings"])
            self.assertEqual(json.loads(rows[0]["stage_metrics_json"]), record["stage_metrics"])
            self.assertFalse(any(path.name.startswith("tmp") for path in output.parent.iterdir()))

    def test_output_prefix_csv_suffix_and_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected = (directory / "run.json", directory / "run.csv")
            self.assertEqual(benchmark.report_paths(directory / "run"), expected)
            self.assertEqual(benchmark.report_paths(directory / "run.csv"), expected)
            self.assertEqual(benchmark.report_paths(directory), (directory / "benchmark.json", directory / "benchmark.csv"))


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.project = {"id": "p", "source_path": "real-input.mp4", "metadata": {"duration": 60}}
        self.queued = {"id": "j", "status": "queued", "stage": "queued"}
        self.completed = {
            "id": "j", "status": "completed", "stage": "scan", "processed_seconds": 60,
            "total_seconds": 60, "elapsed_seconds": 0.5, "speed_ratio": 99999,
        }

    def profile(self, client, **kwargs):
        return benchmark.profile_analysis(
            client, self.project, {"cpu_workers": 2}, run_index=1,
            cache_state="cold", cache_note="unit test", quiet=True, **kwargs,
        )

    def test_force_poll_by_id_and_own_wall_not_api_stage_speed(self):
        client = Mock()
        client.request.side_effect = [self.queued, [{"id": "another", "status": "completed"}, self.completed]]
        with patch.object(benchmark.time, "perf_counter", side_effect=[100, 100, 104, 104]), patch.object(benchmark.time, "sleep"):
            record = self.profile(client)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["wall_seconds"], 4)
        self.assertEqual(record["duration"], 60)
        self.assertEqual(record["speed_ratio"], 15)
        self.assertEqual(record["stage_samples"][-1]["api_speed_ratio"], 99999)
        self.assertIsNone(record["peak_rss"])
        self.assertEqual(client.request.call_args_list[0].args, ("POST", "/api/projects/p/analyze", {"force": True, "auto_enhance": False}))
        self.assertEqual(client.request.call_args_list[1].args, ("GET", "/api/jobs"))
        self.assertFalse(any("benchmarks" in call.args[1] for call in client.request.call_args_list))

    def test_failure_does_not_report_full_source_throughput(self):
        client = Mock()
        client.request.return_value = {"id": "j", "status": "failed", "error": "bad codec"}
        record = self.profile(client)
        self.assertEqual(record["error"], "bad codec")
        self.assertIsNone(record["speed_ratio"])
        self.assertGreaterEqual(record["wall_seconds"], 0)

    def test_timeout_preserves_measurements_and_requests_cancel(self):
        client = Mock()
        client.request.side_effect = [self.queued, {"status": "cancelled"}]
        with patch.object(benchmark.time, "perf_counter", side_effect=[100, 103, 103]):
            record = self.profile(client, timeout=2)
        self.assertEqual(record["status"], "timeout")
        self.assertEqual(record["wall_seconds"], 3)
        self.assertIsNone(record["speed_ratio"])
        self.assertTrue(record["cancel_requested"])
        self.assertEqual(client.request.call_args_list[-1].args, ("POST", "/api/jobs/j/cancel"))

    def test_interrupt_preserves_partial_report_and_requests_cancel(self):
        client = Mock()
        client.request.side_effect = [self.queued, {"status": "cancelled"}]
        with patch.object(benchmark.time, "sleep", side_effect=KeyboardInterrupt):
            record = self.profile(client)
        self.assertEqual(record["status"], "interrupted")
        self.assertEqual(len(record["stage_samples"]), 1)
        self.assertTrue(record["cancel_requested"])

    def test_sampled_backend_rss_is_recorded_with_provenance(self):
        client = Mock()
        client.request.return_value = self.completed
        memory = Mock()
        memory.peak = 123456
        memory.report.return_value = {"backend_pid": 123, "samples": 2, "method": "test sampler"}
        record = self.profile(client, memory=memory)
        self.assertEqual(record["peak_rss"], 123456)
        self.assertEqual(record["peak_rss_unit"], "bytes")
        self.assertEqual(record["memory"]["backend_pid"], 123)


class MemoryTests(unittest.TestCase):
    def test_samples_only_requested_backend_tree_and_keeps_observed_peak(self):
        root, child = Mock(), Mock()
        root.is_running.return_value = True
        root.memory_info.side_effect = [types.SimpleNamespace(rss=100), types.SimpleNamespace(rss=50)]
        root.children.return_value = [child]
        child.memory_info.return_value = types.SimpleNamespace(rss=25)
        psutil = types.SimpleNamespace(Process=Mock(return_value=root), Error=RuntimeError, NoSuchProcess=ProcessLookupError)
        with patch.dict(sys.modules, {"psutil": psutil}):
            sampler = benchmark.MemorySampler(123)
            sampler.sample()
            sampler.sample()
        psutil.Process.assert_called_once_with(123)
        self.assertEqual(sampler.peak, 125)
        self.assertEqual(sampler.report()["samples"], 2)
        root.children.assert_called_with(recursive=True)

    def test_unavailable_pid_does_not_turn_into_zero_rss(self):
        root = Mock()
        root.is_running.return_value = True
        root.memory_info.side_effect = RuntimeError("access denied")
        psutil = types.SimpleNamespace(Process=Mock(return_value=root), Error=RuntimeError, NoSuchProcess=ProcessLookupError)
        with patch.dict(sys.modules, {"psutil": psutil}):
            sampler = benchmark.MemorySampler(123)
            sampler.sample()
        self.assertIsNone(sampler.peak)
        self.assertEqual(sampler.report()["samples"], 0)
        self.assertEqual(sampler.report()["last_error"], "access denied")


class ModelDownloadTests(unittest.TestCase):
    def test_explicit_helper_populates_runtime_cache_and_project_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            cached = Path(temporary) / "hf-snapshot"
            cached.mkdir()
            for name in download_model.REQUIRED_FILES:
                (cached / name).write_bytes(b"mock model file")
            destination = Path(temporary) / "storage" / "models" / "tiny.en"
            utils = types.SimpleNamespace(download_model=Mock(return_value=str(cached)))
            modules = {"faster_whisper": types.ModuleType("faster_whisper"), "faster_whisper.utils": utils}
            with patch.dict(sys.modules, modules), patch.object(download_model, "MODEL_DIR", destination):
                actual_destination, actual_cached = download_model.download_tiny_en()
            utils.download_model.assert_called_once_with("tiny.en")
            self.assertEqual(actual_destination, destination)
            self.assertEqual(actual_cached, cached.resolve())
            for name in download_model.REQUIRED_FILES:
                self.assertEqual((destination / name).read_bytes(), (cached / name).read_bytes())

    def test_incomplete_snapshot_is_not_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            cached = Path(temporary) / "hf-snapshot"
            cached.mkdir()
            destination = Path(temporary) / "tiny.en"
            utils = types.SimpleNamespace(download_model=Mock(return_value=str(cached)))
            modules = {"faster_whisper": types.ModuleType("faster_whisper"), "faster_whisper.utils": utils}
            with (
                patch.dict(sys.modules, modules),
                patch.object(download_model, "MODEL_DIR", destination),
                self.assertRaisesRegex(RuntimeError, "incomplete"),
            ):
                download_model.download_tiny_en()
            self.assertFalse(destination.exists())


class CliTests(unittest.TestCase):
    def test_help_requires_no_server_or_optional_packages(self):
        for module in (benchmark, download_model):
            with self.subTest(module=module.__name__), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as raised:
                    module.main(["--help"])
                self.assertEqual(raised.exception.code, 0)
                self.assertIn("usage:", output.getvalue())

    def test_model_download_requires_explicit_opt_in(self):
        with (
            patch.object(download_model, "download_tiny_en") as download,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            download_model.main([])
        self.assertEqual(raised.exception.code, 2)
        download.assert_not_called()

    def test_multiple_paths_repeat_cache_labels_and_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            videos = [directory / "first.mp4", directory / "second.mp4"]
            for video in videos:
                video.touch()  # The API boundary is mocked; these are not benchmark inputs.
            projects = {
                "p1": {"id": "p1", "source_path": str(videos[0]), "metadata": {"duration": 60}},
                "p2": {"id": "p2", "source_path": str(videos[1]), "metadata": {"duration": 90}},
            }
            imported = []
            client = Mock(base_url="http://127.0.0.1:8000")

            def request(method, path, payload=None):
                if path == "/api/projects":
                    return [projects[key] for key in imported]
                if path == "/api/projects/local":
                    assert payload is not None
                    key = "p1" if payload["path"] == str(videos[0].resolve()) else "p2"
                    imported.append(key)
                    return projects[key]
                if path == "/api/settings":
                    return {"cpu_workers": 2, "sampling_interval": 5}
                if path.endswith("/analyze"):
                    return {"id": "job", "status": "completed", "stage": "done"}
                return projects[path.rsplit("/", 1)[-1]]

            client.request.side_effect = request
            output = directory / "reports" / "run"
            with patch.object(benchmark, "ApiClient", return_value=client), contextlib.redirect_stdout(io.StringIO()):
                code = benchmark.main([str(video) for video in videos] + ["--repeat", "2", "--output", str(output), "--quiet"])
            self.assertEqual(code, 0)
            runs = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))["runs"]
            self.assertEqual([run["cache_state"] for run in runs], ["cold", "warm", "cold", "warm"])
            self.assertEqual([run["duration"] for run in runs], [60, 60, 90, 90])
            self.assertEqual(runs[0]["settings"], {"cpu_workers": 2, "sampling_interval": 5})

    def test_invalid_paths_fail_before_network_access(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(benchmark, "ApiClient") as client,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            benchmark.main([str(Path(temporary) / "missing.mp4"), "--output", str(Path(temporary) / "report")])
        self.assertEqual(raised.exception.code, 2)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
