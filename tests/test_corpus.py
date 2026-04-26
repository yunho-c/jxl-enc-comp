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

    def test_normalizes_palette_and_one_bit_png_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            work = root / "work"
            corpus.mkdir()

            palette = Image.new("P", (2, 1))
            palette.putpalette([255, 0, 0, 0, 255, 0] + [0, 0, 0] * 254)
            palette.putdata([0, 1])
            palette.save(corpus / "palette.png")

            one_bit = Image.new("1", (2, 1))
            one_bit.putdata([0, 1])
            one_bit.save(corpus / "one-bit.png")

            records = discover_images([corpus], work)
            modes = {record.source_path.name: record.mode for record in records}

            self.assertEqual(modes["palette.png"], "RGB")
            self.assertEqual(modes["one-bit.png"], "L")

    def test_empty_corpus_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            work = root / "work"
            corpus.mkdir()
            (corpus / "notes.txt").write_text("not an image", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "no image files found"):
                discover_images([corpus], work)

    def test_corrupt_image_is_reported_as_unsupported_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            work = root / "work"
            corpus.mkdir()
            (corpus / "bad.jpg").write_bytes(b"not a real jpeg")

            [record] = discover_images([corpus], work)

            self.assertIsNone(record.reference_path)
            self.assertEqual(record.mode, "unsupported")
            self.assertIsNotNone(record.unsupported_reason)


if __name__ == "__main__":
    unittest.main()
