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
                patch(
                    "jxl_parity.flamegraph.tool_path",
                    side_effect=lambda command: f"/usr/local/bin/{command}",
                ),
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
            self.assertIn(
                f"cd {shlex.quote(str((out_dir / 'flamegraph-run').resolve()))}",
                script,
            )
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
                out_dir / "folded_stacks.txt",
                out_dir / "xctrace_record_command.txt",
                out_dir / "xctrace_export_command.txt",
                out_dir / "flamegraph-run" / "xctrace-time-profile.xml",
            ]
            for path in stale_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("stale", encoding="utf-8")
            stale_trace_dir = out_dir / "flamegraph-run" / "xctrace-fallback.trace"
            stale_trace_dir.mkdir(parents=True)

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
            self.assertFalse(stale_trace_dir.exists())

    def test_runs_flamegraph_around_libjxl_lossless_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "flamegraph"
            corpus.mkdir()
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")
            commands: list[list[str]] = []
            working_dirs: list[Path | None] = []

            def fake_run(args: list[str], cwd: Path | None = None) -> CommandResult:
                commands.append(args)
                working_dirs.append(cwd)
                return CommandResult(args, 0, 0.25, "", "")

            with (
                patch(
                    "jxl_parity.flamegraph.tool_path",
                    side_effect=lambda command: f"/usr/local/bin/{command}",
                ),
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
                commands[0][0:2],
                ["flamegraph", "-o"],
            )
            self.assertEqual(Path(commands[0][2]), (out_dir / "flamegraph.svg").resolve())
            self.assertEqual(commands[0][3], "--")
            self.assertEqual(working_dirs[0], out_dir / "flamegraph-run")
            self.assertIn("/usr/local/bin/cjxl", commands[0])
            self.assertIn("-d", commands[0])
            self.assertIn("0.0", commands[0])

    def test_runs_from_isolated_directory_and_clears_stale_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "flamegraph"
            trace_dir = out_dir / "flamegraph-run" / "cargo-flamegraph.trace"
            corpus.mkdir()
            trace_dir.mkdir(parents=True)
            (trace_dir / "stale").write_text("stale", encoding="utf-8")
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")
            working_dirs: list[Path | None] = []

            def fake_run(args: list[str], cwd: Path | None = None) -> CommandResult:
                working_dirs.append(cwd)
                self.assertFalse(trace_dir.exists())
                return CommandResult(args, 0, 0.25, "", "")

            with (
                patch(
                    "jxl_parity.flamegraph.tool_path",
                    side_effect=lambda command: f"/usr/local/bin/{command}",
                ),
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
            self.assertEqual(working_dirs, [out_dir / "flamegraph-run"])

    def test_macos_xctrace_fallback_handles_nested_frame_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "flamegraph"
            corpus.mkdir()
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")
            commands: list[list[str]] = []
            xctrace_xml = """<?xml version="1.0"?>
<trace-query-result><node>
<row><backtrace id="1"><frame id="2" name="leaf"><binary id="3" name="cjxl-rs"/></frame><frame id="4" name="root"/></backtrace></row>
<row><backtrace ref="1"/></row>
<row><sentinel/></row>
</node></trace-query-result>"""

            def fake_run(args: list[str], cwd: Path | None = None) -> CommandResult:
                commands.append(args)
                if args[0] == "flamegraph":
                    return CommandResult(
                        args,
                        1,
                        0.1,
                        "",
                        "Error: unable to collapse generated profile data\n"
                        "Read xml event failed: IllFormed("
                        'MismatchedEndTag { expected: "binary", found: "frame" })',
                    )
                if args[1] == "record":
                    return CommandResult(args, 0, 0.2, "", "")
                if args[1] == "export":
                    return CommandResult(args, 0, 0.3, xctrace_xml, "")
                raise AssertionError(f"unexpected command: {args}")

            with (
                patch(
                    "jxl_parity.flamegraph.tool_path",
                    side_effect=lambda command: f"/usr/bin/{command}",
                ),
                patch("jxl_parity.flamegraph.run_command", side_effect=fake_run),
                patch("jxl_parity.flamegraph.sys.platform", "darwin"),
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
                        dry_run=False,
                        instrument_stages=False,
                    )
                )

            self.assertEqual(summary.status, "completed")
            self.assertIn("xctrace fallback", summary.reason)
            self.assertTrue((out_dir / "flamegraph.svg").exists())
            self.assertTrue((out_dir / "folded_stacks.txt").exists())
            self.assertIn(
                "root;leaf 2",
                (out_dir / "folded_stacks.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                [command[0] for command in commands],
                ["flamegraph", "/usr/bin/xctrace", "/usr/bin/xctrace"],
            )


if __name__ == "__main__":
    unittest.main()
