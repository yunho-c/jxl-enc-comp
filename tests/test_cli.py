from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jxl_parity.cli import main
from jxl_parity.flamegraph import FlamegraphSummary
from jxl_parity.profiler import ProfileSummary


class CliTests(unittest.TestCase):
    def test_empty_corpus_returns_clean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "corpus"
            corpus.mkdir()
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                exit_code = main(["run", "--corpus", str(corpus), "--out", str(Path(tmp) / "out")])

            self.assertEqual(exit_code, 2)
            self.assertIn("error: no image files found", stderr.getvalue())

    def test_invalid_run_mode_is_a_cli_error(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["run", "--modes", "lossless,typo"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--modes contains unsupported value", stderr.getvalue())

    def test_vardct_requires_at_least_one_distance(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["run", "--modes", "vardct", "--distances", ""])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--distances must include at least one value", stderr.getvalue())

    def test_profile_rejects_empty_efforts(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["profile", "--efforts", ""])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--efforts must include at least one value", stderr.getvalue())

    def test_rejects_non_finite_distance(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["run", "--distances", "nan"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--distances values must be finite numbers", stderr.getvalue())

    def test_rejects_non_positive_max_images(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["run", "--max-images", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--max-images must be at least 1", stderr.getvalue())

    def test_profile_rejects_non_positive_max_images(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["profile", "--max-images", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--max-images must be at least 1", stderr.getvalue())

    def test_profile_rejects_non_positive_samples(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["profile", "--samples", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--samples must be at least 1", stderr.getvalue())

    def test_profile_rejects_negative_warmups(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["profile", "--warmups", "-1"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--warmups must be at least 0", stderr.getvalue())

    def test_profile_reports_enabled_stage_sidecars(self) -> None:
        stdout = io.StringIO()
        summary = ProfileSummary(
            out_dir=Path("reports/profile"),
            images=1,
            total_cases=1,
            completed_cases=1,
            failed_cases=0,
            skipped_cases=0,
            encoder="jxl-encoder",
            instrument_stages=True,
            samples_per_case=1,
            warmups_per_case=0,
            tool_status={
                "cjxl": True,
                "jxl_encoder": True,
                "jxl_encoder_stage_timing": True,
                "jxl_encoder_stage_timing_ingested": True,
            },
        )

        with (
            patch("jxl_parity.cli.run_profile", return_value=summary),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["profile", "--instrument-stages"])

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "stage_timing=jxl-encoder sidecars enabled", stdout.getvalue()
        )

    def test_profile_reports_encode_total_stage_fallback(self) -> None:
        stdout = io.StringIO()
        summary = ProfileSummary(
            out_dir=Path("reports/profile"),
            images=1,
            total_cases=1,
            completed_cases=1,
            failed_cases=0,
            skipped_cases=0,
            encoder="jxl-encoder",
            instrument_stages=True,
            samples_per_case=1,
            warmups_per_case=0,
            tool_status={
                "cjxl": True,
                "jxl_encoder": True,
                "jxl_encoder_stage_timing": False,
                "jxl_encoder_stage_timing_ingested": False,
            },
        )

        with (
            patch("jxl_parity.cli.run_profile", return_value=summary),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["profile", "--instrument-stages"])

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "stage_timing=encode_total only (cjxl-rs lacks --stage-timing-json)",
            stdout.getvalue(),
        )

    def test_profile_reports_no_named_stage_sidecars(self) -> None:
        stdout = io.StringIO()
        summary = ProfileSummary(
            out_dir=Path("reports/profile"),
            images=1,
            total_cases=1,
            completed_cases=1,
            failed_cases=0,
            skipped_cases=0,
            encoder="jxl-encoder",
            instrument_stages=True,
            samples_per_case=1,
            warmups_per_case=0,
            tool_status={
                "cjxl": True,
                "jxl_encoder": True,
                "jxl_encoder_stage_timing": True,
                "jxl_encoder_stage_timing_ingested": False,
            },
        )

        with (
            patch("jxl_parity.cli.run_profile", return_value=summary),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["profile", "--instrument-stages"])

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "stage_timing=encode_total only (no named sidecar stages ingested)",
            stdout.getvalue(),
        )

    def test_flamegraph_dry_run_reports_command(self) -> None:
        stdout = io.StringIO()
        summary = FlamegraphSummary(
            out_dir=Path("reports/flamegraph"),
            image_id="sample",
            source_path="sample.png",
            reference_path="reports/flamegraph/work/reference/sample.png",
            encoded_path="reports/flamegraph/work/encoded/sample.jxl",
            svg_path="reports/flamegraph/flamegraph.svg",
            stage_timing_path=None,
            encoder="jxl-encoder",
            mode="vardct",
            distance=1.0,
            effort=7,
            status="prepared",
            reason="dry run; profiler command was not executed",
            returncode=None,
            elapsed_seconds=None,
            encoder_command="cjxl-rs input.png output.jxl -e 7 -d 1.0",
            profiler_command="flamegraph -o flamegraph.svg -- cjxl-rs input.png output.jxl",
            tool_status={"encoder": False, "flamegraph": False},
            stderr=None,
        )

        with (
            patch("jxl_parity.cli.run_flamegraph", return_value=summary),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["flamegraph", "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Wrote flamegraph artifacts", stdout.getvalue())
        self.assertIn("status=prepared image=sample", stdout.getvalue())
        self.assertIn("command=flamegraph -o", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
