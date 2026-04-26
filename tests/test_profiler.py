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

            def fake_encode(**kwargs):
                kwargs["output_path"].write_bytes(b"jxl")
                return CommandResult(["cjxl-rs", "input.png"], 0, 0.25, "", "")

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
                    )
                )

            self.assertEqual(summary.completed_cases, 1)
            self.assertFalse((out_dir / "work").exists())
            stage_timing = json.loads((out_dir / "stage_timing.json").read_text(encoding="utf-8"))
            self.assertEqual(stage_timing["stage_source"], "wall_clock_encode_total")
            self.assertEqual(stage_timing["runs"][0]["stages"][0]["stage"], "encode_total")
            self.assertTrue((out_dir / "profile_runs.csv").exists())
            self.assertIn("perf record", (out_dir / "profiler_commands.md").read_text(encoding="utf-8"))

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


if __name__ == "__main__":
    unittest.main()
