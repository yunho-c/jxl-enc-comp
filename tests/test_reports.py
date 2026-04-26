from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from jxl_parity.reports import write_paired_comparisons


class ReportTests(unittest.TestCase):
    def test_paired_comparisons_compute_ratios_and_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "paired.csv"
            rows = [
                {
                    "image_id": "sample",
                    "source_path": "sample.png",
                    "encoder": "libjxl",
                    "mode": "vardct",
                    "distance": 1.0,
                    "effort": 7,
                    "status": "passed",
                    "bits_per_pixel": 2.0,
                    "psnr": 40.0,
                    "ssimulacra2": 80.0,
                    "butteraugli": 1.2,
                    "encode_seconds": 0.2,
                    "megapixels": 0.1,
                },
                {
                    "image_id": "sample",
                    "source_path": "sample.png",
                    "encoder": "jxl-encoder",
                    "mode": "vardct",
                    "distance": 1.0,
                    "effort": 7,
                    "status": "passed",
                    "bits_per_pixel": 3.0,
                    "psnr": 38.5,
                    "ssimulacra2": 76.0,
                    "butteraugli": 1.7,
                    "encode_seconds": 0.6,
                    "megapixels": 0.1,
                },
            ]

            write_paired_comparisons(output, rows)

            with output.open(newline="", encoding="utf-8") as handle:
                [comparison] = list(csv.DictReader(handle))

            self.assertEqual(comparison["image_id"], "sample")
            self.assertEqual(float(comparison["bpp_ratio_jxl_encoder_to_libjxl"]), 1.5)
            self.assertEqual(float(comparison["psnr_delta_jxl_encoder_minus_libjxl"]), -1.5)
            self.assertEqual(float(comparison["ssimulacra2_delta_jxl_encoder_minus_libjxl"]), -4.0)
            self.assertEqual(float(comparison["butteraugli_delta_jxl_encoder_minus_libjxl"]), 0.5)
            self.assertAlmostEqual(float(comparison["encode_time_ratio_jxl_encoder_to_libjxl"]), 3.0)


if __name__ == "__main__":
    unittest.main()
