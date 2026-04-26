from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from jxl_parity.codecs import CommandResult
from jxl_parity.corpus import ImageRecord
from jxl_parity.metrics import PixelComparison
from jxl_parity.runner import RunConfig, _run_case, run_suite


class RunnerTests(unittest.TestCase):
    def test_missing_tools_are_reported_as_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "out"
            corpus.mkdir()
            Image.new("RGB", (2, 2), (1, 2, 3)).save(corpus / "sample.png")

            summary = run_suite(
                RunConfig(
                    corpus=[corpus],
                    out_dir=out_dir,
                    cjxl="definitely-missing-cjxl",
                    djxl="definitely-missing-djxl",
                    jxl_encoder="definitely-missing-cjxl-rs",
                    modes=["lossless"],
                    distances=[1.0],
                    efforts=[1],
                    max_images=None,
                    metrics=["psnr"],
                    keep_work=False,
                )
            )

            self.assertEqual(summary.total_cases, 2)
            self.assertEqual(summary.skipped_cases, 2)
            self.assertTrue((out_dir / "summary.json").exists())
            self.assertTrue((out_dir / "feature_coverage.md").exists())
            self.assertFalse((out_dir / "work").exists())

    def test_unsupported_inputs_are_reported_as_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            out_dir = root / "out"
            corpus.mkdir()
            (corpus / "bad.jpg").write_bytes(b"not a real jpeg")

            summary = run_suite(
                RunConfig(
                    corpus=[corpus],
                    out_dir=out_dir,
                    cjxl="definitely-missing-cjxl",
                    djxl="definitely-missing-djxl",
                    jxl_encoder="definitely-missing-cjxl-rs",
                    modes=["lossless"],
                    distances=[1.0],
                    efforts=[1],
                    max_images=None,
                    metrics=["psnr"],
                    keep_work=False,
                )
            )

            self.assertEqual(summary.total_cases, 2)
            self.assertEqual(summary.skipped_cases, 2)
            results = (out_dir / "per_image_results.csv").read_text(encoding="utf-8")
            self.assertIn("unsupported input format", results)

    def test_visual_diff_paths_are_report_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.png"
            Image.new("RGB", (2, 2), (1, 2, 3)).save(reference)

            image = ImageRecord(
                image_id="sample",
                source_path=reference,
                reference_path=reference,
                width=2,
                height=2,
                mode="RGB",
                source_format="PNG",
                has_alpha=False,
                bit_depth=8,
            )
            out_dir = root / "reports" / "parity"
            encoded_dir = out_dir / "work" / "encoded"
            decoded_dir = out_dir / "work" / "decoded"
            diff_dir = out_dir / "visual_diffs"
            encoded_dir.mkdir(parents=True)
            decoded_dir.mkdir(parents=True)

            def fake_encode(**kwargs):
                kwargs["output_path"].write_bytes(b"jxl")
                return CommandResult(["cjxl"], 0, 0.01, "", "")

            def fake_decode(*args):
                return CommandResult(["djxl"], 0, 0.01, "", "")

            mismatch = PixelComparison(
                same_size=True,
                same_mode=True,
                equal_pixels=False,
                max_channel_delta=1,
                psnr=42.0,
                reference_mode="RGB",
                decoded_mode="RGB",
                reference_size=(2, 2),
                decoded_size=(2, 2),
            )

            with (
                patch("jxl_parity.runner.encode", side_effect=fake_encode),
                patch("jxl_parity.runner.decode", side_effect=fake_decode),
                patch("jxl_parity.runner.compare_pixels", return_value=mismatch),
                patch("jxl_parity.runner.write_visual_diff", return_value=True),
            ):
                result = _run_case(
                    config=RunConfig(
                        corpus=[],
                        out_dir=out_dir,
                        cjxl="cjxl",
                        djxl="djxl",
                        jxl_encoder="cjxl-rs",
                        modes=["lossless"],
                        distances=[1.0],
                        efforts=[1],
                        max_images=None,
                        metrics=[],
                        keep_work=False,
                    ),
                    image=image,
                    encoder_name="libjxl",
                    encoder_command="cjxl",
                    encoder_available=True,
                    djxl_available=True,
                    mode="lossless",
                    effort=1,
                    distance=None,
                    encoded_dir=encoded_dir,
                    decoded_dir=decoded_dir,
                    diff_dir=diff_dir,
                )

            self.assertEqual(result.visual_diff_path, "visual_diffs/sample-libjxl-lossless-lossless-e1.png")


if __name__ == "__main__":
    unittest.main()
