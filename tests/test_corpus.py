from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from jxl_parity.corpus import discover_images


class CorpusTests(unittest.TestCase):
    def test_discovers_and_prepares_png_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            work = root / "work"
            corpus.mkdir()

            png_path = corpus / "sample.png"
            jpg_path = corpus / "sample.jpg"
            Image.new("RGB", (3, 2), (10, 20, 30)).save(png_path)
            Image.new("RGB", (4, 5), (40, 50, 60)).save(jpg_path)

            records = discover_images([corpus], work)

            self.assertEqual(len(records), 2)
            self.assertTrue(all(record.reference_path.suffix == ".png" for record in records))
            self.assertTrue(all(record.reference_path.exists() for record in records))
            self.assertEqual({record.source_format for record in records}, {"PNG", "JPEG"})


if __name__ == "__main__":
    unittest.main()

