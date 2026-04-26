from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from jxl_parity.cli import main


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


if __name__ == "__main__":
    unittest.main()
