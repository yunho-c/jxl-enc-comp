from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from jxl_parity.codecs import CommandResult
from jxl_parity.profiler import ProfileConfig, run_profile


class ProfilerTests(unittest.TestCase):
    def test_profile_writes_stage_timing_and_profiler_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "profile"
            corpus.mkdir()
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")

            def fake_tool_path(command: str) -> str | None:
                return f"/bin/{command}"

            encode_seconds = iter([0.10, 0.25, 0.35])

            def fake_encode(**kwargs):
                kwargs["output_path"].write_bytes(b"jxl")
                return CommandResult(["cjxl-rs", "input.png"], 0, next(encode_seconds), "", "")

            with (
                patch("jxl_parity.profiler.tool_path", side_effect=fake_tool_path),
                patch("jxl_parity.profiler.encode", side_effect=fake_encode),
            ):
                summary = run_profile(
                    ProfileConfig(
                        corpus=[corpus],
                        out_dir=out_dir,
                        cjxl="cjxl",
                        jxl_encoder="cjxl-rs",
                        encoder="jxl-encoder",
                        modes=["lossless"],
                        distances=[1.0],
                        efforts=[7],
                        max_images=None,
                        keep_work=False,
                        instrument_stages=True,
                        samples=2,
                        warmups=1,
                    )
                )

            self.assertEqual(summary.completed_cases, 1)
            self.assertEqual(summary.samples_per_case, 2)
            self.assertEqual(summary.warmups_per_case, 1)
            self.assertFalse((out_dir / "work").exists())
            profile_summary = json.loads((out_dir / "profile_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(profile_summary["samples_per_case"], 2)
            self.assertEqual(profile_summary["warmups_per_case"], 1)
            stage_timing = json.loads((out_dir / "stage_timing.json").read_text(encoding="utf-8"))
            self.assertEqual(stage_timing["stage_source"], "wall_clock_encode_total")
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["stage"], "encode_total")
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["sample_count"], 2)
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["warmup_count"], 1)
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["seconds"], 0.3)
            profile_runs = (out_dir / "profile_runs.csv").read_text(encoding="utf-8")
            self.assertIn("encode_seconds_median", profile_runs)
            self.assertIn("0.3", profile_runs)
            samples = json.loads((out_dir / "profile_samples.json").read_text(encoding="utf-8"))
            self.assertEqual(len(samples), 3)
            self.assertTrue(samples[0]["warmup"])
            self.assertFalse(samples[1]["warmup"])
            self.assertTrue((out_dir / "profile_runs.csv").exists())
            self.assertTrue((out_dir / "profile_samples.csv").exists())
            profile_report = (out_dir / "profile_report.md").read_text(encoding="utf-8")
            self.assertIn("profile_samples.csv", profile_report)
            self.assertIn("Measured samples per case: 2", profile_report)
            profiler_commands = (out_dir / "profiler_commands.md").read_text(encoding="utf-8")
            self.assertIn("perf record", profiler_commands)
            self.assertIn("<reference.png>", profiler_commands)
            self.assertIn("--keep-work", profiler_commands)

    def test_profile_reports_unsupported_inputs_as_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "profile"
            corpus.mkdir()
            (corpus / "bad.jpg").write_bytes(b"not a real jpeg")

            with patch("jxl_parity.profiler.encode") as fake_encode:
                summary = run_profile(
                    ProfileConfig(
                        corpus=[corpus],
                        out_dir=out_dir,
                        cjxl="definitely-missing-cjxl",
                        jxl_encoder="definitely-missing-cjxl-rs",
                        encoder="jxl-encoder",
                        modes=["lossless"],
                        distances=[1.0],
                        efforts=[7],
                        max_images=None,
                        keep_work=False,
                        instrument_stages=False,
                    )
                )

            self.assertEqual(summary.skipped_cases, 1)
            fake_encode.assert_not_called()
            self.assertIn(
                "unsupported input format",
                (out_dir / "profile_runs.csv").read_text(encoding="utf-8"),
            )
            self.assertIn("sample_index", (out_dir / "profile_samples.csv").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
