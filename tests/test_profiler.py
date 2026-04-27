from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from jxl_parity.codecs import CommandResult
from jxl_parity.profiler import (
    ProfileConfig,
    _example_command,
    _example_commands,
    run_profile,
)


class ProfilerTests(unittest.TestCase):
    def test_profiler_fallback_command_uses_selected_vardct_distance(self) -> None:
        command = _example_command(
            ProfileConfig(
                corpus=[Path("corpus")],
                out_dir=Path("reports/profile"),
                cjxl="cjxl",
                jxl_encoder="cjxl-rs",
                encoder="jxl-encoder",
                modes=["vardct"],
                distances=[2.5],
                efforts=[9],
                max_images=None,
                keep_work=False,
                instrument_stages=True,
            )
        )

        self.assertIn("-e 9", command)
        self.assertIn("-d 2.5", command)
        self.assertNotIn("--lossless", command)

    def test_profiler_fallback_commands_cover_both_encoders(self) -> None:
        commands = _example_commands(
            ProfileConfig(
                corpus=[Path("corpus")],
                out_dir=Path("reports/profile"),
                cjxl="cjxl",
                jxl_encoder="cjxl-rs",
                encoder="both",
                modes=["lossless"],
                distances=[1.0],
                efforts=[3],
                max_images=None,
                keep_work=False,
                instrument_stages=True,
            )
        )

        labels = [label for label, _command in commands]
        command_text = "\n".join(command for _label, command in commands)
        self.assertEqual(
            labels, ["libjxl fallback command", "jxl-encoder fallback command"]
        )
        self.assertIn("cjxl '<reference.png>'", command_text)
        self.assertIn("cjxl-rs '<reference.png>'", command_text)

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
                self.assertIsNone(kwargs["stage_timing_path"])
                kwargs["output_path"].write_bytes(b"jxl")
                return CommandResult(
                    ["cjxl-rs", "input.png"], 0, next(encode_seconds), "", ""
                )

            with (
                patch("jxl_parity.profiler.tool_path", side_effect=fake_tool_path),
                patch(
                    "jxl_parity.profiler.tool_supports_option", return_value=False
                ),
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
            self.assertFalse(summary.tool_status["jxl_encoder_stage_timing_ingested"])
            self.assertFalse((out_dir / "work").exists())
            profile_summary = json.loads(
                (out_dir / "profile_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(profile_summary["samples_per_case"], 2)
            self.assertEqual(profile_summary["warmups_per_case"], 1)
            stage_timing = json.loads(
                (out_dir / "stage_timing.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stage_timing["stage_source"], "wall_clock_encode_total")
            self.assertEqual(
                stage_timing["runs"][0]["stages"][0]["stage"], "encode_total"
            )
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["sample_count"], 2)
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["warmup_count"], 1)
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["seconds"], 0.3)
            profile_runs_json = json.loads(
                (out_dir / "profile_runs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(profile_runs_json[0]["sample_count"], 2)
            profile_runs = (out_dir / "profile_runs.csv").read_text(encoding="utf-8")
            self.assertIn("encode_seconds_median", profile_runs)
            self.assertIn("0.3", profile_runs)
            samples = json.loads(
                (out_dir / "profile_samples.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(samples), 3)
            self.assertTrue(samples[0]["warmup"])
            self.assertFalse(samples[1]["warmup"])
            self.assertTrue((out_dir / "profile_runs.csv").exists())
            self.assertTrue((out_dir / "profile_samples.csv").exists())
            profile_report = (out_dir / "profile_report.md").read_text(encoding="utf-8")
            self.assertIn("profile_samples.csv", profile_report)
            self.assertIn("profile_stage_summary.csv", profile_report)
            self.assertIn("Measured samples per case: 2", profile_report)
            self.assertIn("Stage Timing Feasibility", profile_report)
            self.assertIn("Per-Stage Summary", profile_report)
            self.assertIn("encode_total", profile_report)
            self.assertIn("profile_plots/stage-seconds-per-mp.svg", profile_report)
            self.assertIn("cannot attribute time to color transform", profile_report)
            stage_summary = (out_dir / "profile_stage_summary.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn("percent_of_encode_total", stage_summary)
            self.assertIn("encode_total", stage_summary)
            self.assertTrue(
                (out_dir / "profile_plots" / "stage-seconds-per-mp.svg").exists()
            )
            profiler_commands = (out_dir / "profiler_commands.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("perf record", profiler_commands)
            self.assertIn("<reference.png>", profiler_commands)
            self.assertIn("--keep-work", profiler_commands)
            self.assertIn("Named Stage Timing", profiler_commands)
            self.assertIn("use a custom\n`jxl-encoder` build", profiler_commands)

    def test_profile_ingests_jxl_encoder_stage_timing_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "profile"
            corpus.mkdir()
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")

            def fake_tool_path(command: str) -> str | None:
                return f"/bin/{command}"

            encode_seconds = iter([0.10, 0.25, 0.35])
            stage_seconds = iter([0.005, 0.010, 0.030])

            def fake_encode(**kwargs):
                kwargs["output_path"].write_bytes(b"jxl")
                stage_timing_path = kwargs["stage_timing_path"]
                self.assertIsNotNone(stage_timing_path)
                seconds = next(stage_seconds)
                stage_timing_path.write_text(
                    json.dumps(
                        {
                            "stage_source": "rust_encoder_stage_spans",
                            "elapsed_wall_seconds": seconds + 0.001,
                            "total_stage_wall_seconds": seconds,
                            "unattributed_wall_seconds": 0.001,
                            "stages": [
                                {
                                    "stage": "color_xyb",
                                    "wall_seconds": seconds,
                                    "calls": 1,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return CommandResult(
                    [
                        "cjxl-rs",
                        "input.png",
                        "--stage-timing-json",
                        str(stage_timing_path),
                    ],
                    0,
                    next(encode_seconds),
                    "",
                    "",
                )

            with (
                patch("jxl_parity.profiler.tool_path", side_effect=fake_tool_path),
                patch("jxl_parity.profiler.tool_supports_option", return_value=True),
                patch("jxl_parity.profiler.encode", side_effect=fake_encode),
            ):
                summary = run_profile(
                    ProfileConfig(
                        corpus=[corpus],
                        out_dir=out_dir,
                        cjxl="cjxl",
                        jxl_encoder="cjxl-rs",
                        encoder="jxl-encoder",
                        modes=["vardct"],
                        distances=[2.0],
                        efforts=[7],
                        max_images=None,
                        keep_work=False,
                        instrument_stages=True,
                        samples=2,
                        warmups=1,
                    )
                )

            self.assertTrue(summary.tool_status["jxl_encoder_stage_timing_ingested"])
            samples = json.loads(
                (out_dir / "profile_samples.json").read_text(encoding="utf-8")
            )
            self.assertEqual(samples[0]["stage_timing"]["stages"][0]["seconds"], 0.005)
            self.assertEqual(samples[1]["stage_timing"]["stages"][0]["seconds"], 0.01)

            stage_timing = json.loads(
                (out_dir / "stage_timing.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stage_timing["stage_source"], "jxl_encoder_stage_sidecar")
            stages = {
                stage["stage"]: stage for stage in stage_timing["runs"][0]["stages"]
            }
            self.assertEqual(stages["encode_total"]["seconds"], 0.3)
            self.assertEqual(stages["color_xyb"]["seconds"], 0.02)
            self.assertEqual(stages["color_xyb"]["sample_count"], 2)
            self.assertEqual(stages["color_xyb"]["warmup_count"], 1)
            self.assertEqual(
                stage_timing["runs"][0]["stage_accounting"]["sample_count"], 2
            )
            self.assertEqual(
                stage_timing["runs"][0]["stage_accounting"][
                    "sidecar_total_stage_seconds"
                ],
                0.02,
            )
            self.assertAlmostEqual(
                stage_timing["runs"][0]["stage_accounting"][
                    "harness_unattributed_seconds"
                ],
                0.27999999999999997,
            )
            aggregates = {entry["stage"]: entry for entry in stage_timing["aggregates"]}
            self.assertEqual(aggregates["color_xyb"]["avg_seconds"], 0.02)
            profile_report = (out_dir / "profile_report.md").read_text(encoding="utf-8")
            self.assertIn("Named Stage Shares", profile_report)
            self.assertIn("color_xyb", profile_report)
            self.assertIn("profile_plots/stage-share.svg", profile_report)
            stage_summary = (out_dir / "profile_stage_summary.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn("color_xyb", stage_summary)
            self.assertTrue((out_dir / "profile_plots" / "stage-share.svg").exists())

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
            self.assertIn(
                "sample_index",
                (out_dir / "profile_samples.csv").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
