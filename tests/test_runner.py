from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from jxl_parity.runner import RunConfig, run_suite


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


if __name__ == "__main__":
    unittest.main()

