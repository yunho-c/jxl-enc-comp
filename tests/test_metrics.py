from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from jxl_parity.metrics import compare_pixels, write_visual_diff


class MetricsTests(unittest.TestCase):
    def test_compare_pixels_detects_exact_match_and_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.png"
            same = root / "same.png"
            different = root / "different.png"

            Image.new("RGB", (2, 2), (10, 20, 30)).save(reference)
            Image.new("RGB", (2, 2), (10, 20, 30)).save(same)
            Image.new("RGB", (2, 2), (11, 20, 30)).save(different)

            exact = compare_pixels(reference, same)
            mismatch = compare_pixels(reference, different)

            self.assertTrue(exact.equal_pixels)
            self.assertTrue(math.isinf(exact.psnr or 0))
            self.assertFalse(mismatch.equal_pixels)
            self.assertEqual(mismatch.max_channel_delta, 1)

    def test_write_visual_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.png"
            different = root / "different.png"
            diff = root / "diff.png"

            Image.new("RGB", (2, 2), (0, 0, 0)).save(reference)
            Image.new("RGB", (2, 2), (12, 0, 0)).save(different)

            self.assertTrue(write_visual_diff(reference, different, diff))
            self.assertTrue(diff.exists())


if __name__ == "__main__":
    unittest.main()

