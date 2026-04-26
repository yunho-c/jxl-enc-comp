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


if __name__ == "__main__":
    unittest.main()
