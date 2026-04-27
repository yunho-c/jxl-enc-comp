from __future__ import annotations

import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from jxl_parity.codecs import CommandResult
from jxl_parity.flamegraph import FlamegraphConfig, run_flamegraph


class FlamegraphTests(unittest.TestCase):
    def test_dry_run_writes_reproducible_command_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "flamegraph"
            corpus.mkdir()
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")

            with (
                patch("jxl_parity.flamegraph.tool_path", return_value="/bin/tool"),
                patch("jxl_parity.flamegraph.tool_supports_option", return_value=True),
                patch("jxl_parity.flamegraph.run_command") as fake_run,
            ):
                summary = run_flamegraph(
                    FlamegraphConfig(
                        corpus=[corpus],
                        out_dir=out_dir,
                        cjxl="cjxl",
                        jxl_encoder="cjxl-rs",
                        encoder="jxl-encoder",
                        mode="vardct",
                        distance=1.5,
                        effort=7,
                        max_images=1,
                        flamegraph="flamegraph",
                        dry_run=True,
                        instrument_stages=True,
                    )
                )

            fake_run.assert_not_called()
            self.assertEqual(summary.status, "prepared")
            self.assertIn("flamegraph -o", summary.profiler_command)
            self.assertIn("cjxl-rs", summary.encoder_command)
            self.assertIn("-d 1.5", summary.encoder_command)
            self.assertIn("--stage-timing-json", summary.encoder_command)
            self.assertTrue((out_dir / "run_flamegraph.sh").exists())
            script = (out_dir / "run_flamegraph.sh").read_text(encoding="utf-8")
            self.assertIn(f"cd {shlex.quote(str(Path.cwd()))}", script)
            self.assertTrue((out_dir / "encoder_command.txt").exists())
            self.assertTrue((out_dir / "flamegraph_command.txt").exists())
            self.assertTrue((out_dir / "README.md").exists())
            payload = json.loads(
                (out_dir / "flamegraph_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["status"], "prepared")
            self.assertEqual(payload["mode"], "vardct")
            self.assertIsNotNone(payload["stage_timing_path"])
            self.assertTrue(Path(payload["reference_path"]).exists())

    def test_dry_run_clears_stale_output_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "flamegraph"
            encoded_dir = out_dir / "work" / "encoded"
            corpus.mkdir()
            encoded_dir.mkdir(parents=True)
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")

            with (
                patch("jxl_parity.flamegraph.tool_path", return_value="/bin/tool"),
                patch("jxl_parity.flamegraph.tool_supports_option", return_value=True),
                patch("jxl_parity.flamegraph.run_command"),
            ):
                summary = run_flamegraph(
                    FlamegraphConfig(
                        corpus=[corpus],
                        out_dir=out_dir,
                        cjxl="cjxl",
                        jxl_encoder="cjxl-rs",
                        encoder="jxl-encoder",
                        mode="vardct",
                        distance=1.0,
                        effort=7,
                        max_images=1,
                        flamegraph="flamegraph",
                        dry_run=True,
                        instrument_stages=True,
                    )
                )

            stage_timing_path = summary.stage_timing_path
            self.assertIsNotNone(stage_timing_path)
            stale_paths = [
                Path(summary.svg_path),
                Path(summary.encoded_path),
                Path(stage_timing_path),
            ]
            for path in stale_paths:
                path.write_text("stale", encoding="utf-8")

            with (
                patch("jxl_parity.flamegraph.tool_path", return_value="/bin/tool"),
                patch("jxl_parity.flamegraph.tool_supports_option", return_value=True),
                patch("jxl_parity.flamegraph.run_command") as fake_run,
            ):
                rerun_summary = run_flamegraph(
                    FlamegraphConfig(
                        corpus=[corpus],
                        out_dir=out_dir,
                        cjxl="cjxl",
                        jxl_encoder="cjxl-rs",
                        encoder="jxl-encoder",
                        mode="vardct",
                        distance=1.0,
                        effort=7,
                        max_images=1,
                        flamegraph="flamegraph",
                        dry_run=True,
                        instrument_stages=True,
                    )
                )

            fake_run.assert_not_called()
            self.assertEqual(rerun_summary.status, "prepared")
            for path in stale_paths:
                self.assertFalse(path.exists())

    def test_runs_flamegraph_around_libjxl_lossless_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "flamegraph"
            corpus.mkdir()
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")
            commands: list[list[str]] = []

            def fake_run(args: list[str]) -> CommandResult:
                commands.append(args)
                return CommandResult(args, 0, 0.25, "", "")

            with (
                patch("jxl_parity.flamegraph.tool_path", return_value="/bin/tool"),
                patch("jxl_parity.flamegraph.run_command", side_effect=fake_run),
            ):
                summary = run_flamegraph(
                    FlamegraphConfig(
                        corpus=[corpus],
                        out_dir=out_dir,
                        cjxl="cjxl",
                        jxl_encoder="cjxl-rs",
                        encoder="libjxl",
                        mode="lossless",
                        distance=None,
                        effort=3,
                        max_images=1,
                        flamegraph="flamegraph",
                        dry_run=False,
                        instrument_stages=False,
                    )
                )

            self.assertEqual(summary.status, "completed")
            self.assertEqual(summary.returncode, 0)
            self.assertEqual(
                commands[0][0:4],
                ["flamegraph", "-o", str(out_dir / "flamegraph.svg"), "--"],
            )
            self.assertIn("cjxl", commands[0])
            self.assertIn("-d", commands[0])
            self.assertIn("0.0", commands[0])


if __name__ == "__main__":
    unittest.main()
