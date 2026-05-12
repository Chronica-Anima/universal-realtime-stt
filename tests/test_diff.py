"""
Tests for diff report generation and TSV export.

Exercises DiffReport, HTML output, and benchmark TSV writing
with sample text — no STT providers or audio files needed.

    pytest tests/test_diff.py -v
"""
from __future__ import annotations

import asyncio
import unittest
from os import getenv
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from helpers.diff_report import DiffReport
from pytest_config import OUT_PATH


# Czech sample texts — expected is ground truth, got simulates STT output with typical errors.
EXPECTED = (
    "Dobrý den, vítejte v naší přednášce o umělé inteligenci. "
    "Dnes budeme hovořit o tom, jak se strojové učení využívá v praxi. "
    "Začneme základními pojmy a postupně přejdeme k pokročilejším tématům."
)

GOT = (
    "Dobrý den vítejte v naší přednášce o umělé inteligenci. "
    "Dnes budeme hovořit o tom, jak se strojové učení využívá praxi. "
    "Začneme základními pojmy a postupně přejdeme k pokročilejším tématům."
)


class TestDiffReport(unittest.TestCase):

    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_metrics_from_construction(self) -> None:
        """All metrics are computed automatically from just the two texts."""
        report = DiffReport(EXPECTED, GOT)

        # Sanity: got is close to expected, so error rates should be low but nonzero
        self.assertGreater(report.word_error_rate, 0)
        self.assertLess(report.word_error_rate, 20)
        self.assertGreater(report.character_error_rate, 0)
        self.assertLess(report.character_error_rate, 20)

        # Word counts should be reasonable
        self.assertGreater(report.words_expected, 10)
        self.assertGreater(report.words_got, 10)

    def test_write_html(self) -> None:
        """write_html creates an HTML file with WER and CER stats."""
        report = DiffReport(EXPECTED, GOT)
        html_path = report.write_html(self.tmp / "test.diff.html", title="test sample", detail="Unit test")

        self.assertTrue(html_path.exists())
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("WER", html)
        self.assertIn("CER", html)

    def test_to_html_without_file(self) -> None:
        """to_html returns HTML string without writing to disk."""
        report = DiffReport(EXPECTED, GOT)
        html = report.to_html(title="inline test", detail="no file")

        self.assertIn("WER", html)
        self.assertIn("CER", html)
        self.assertIn("inline test", html)

    def test_to_metrics_dict(self) -> None:
        """to_metrics_dict() includes all numeric fields and computed properties, excludes str."""
        report = DiffReport(EXPECTED, GOT)
        metrics = report.to_metrics_dict()

        # Computed properties present
        self.assertIn("word_error_rate", metrics)
        self.assertIn("character_error_rate", metrics)
        self.assertIn("match_percentage", metrics)

        # Numeric fields present
        self.assertIn("chars_expected", metrics)
        self.assertIn("word_levenshtein", metrics)

        # Raw text excluded
        self.assertNotIn("text_expected", metrics)
        self.assertNotIn("text_got", metrics)

        # All values are strings (formatted for TSV)
        for k, v in metrics.items():
            self.assertIsInstance(v, str, f"metrics[{k!r}] should be str, got {type(v)}")

    def test_tsv_roundtrip(self) -> None:
        """Benchmark write_tsv produces valid TSV with auto-discovered columns.

        Writes HTML diff reports and TSV to out/ for manual inspection.
        """
        from benchmark import BenchmarkResult, write_tsv

        reports = []
        for i, (exp, got) in enumerate([(EXPECTED, GOT), (GOT, EXPECTED)]):
            report = DiffReport(exp, got)
            html_path = report.write_html(OUT_PATH / f"test_diff_{i}.diff.html", title=f"sample {i}", detail="test")
            reports.append(BenchmarkResult(f"Provider{i}", f"file{i}.wav", report, html_path, None))

        # Add a failed result
        reports.append(BenchmarkResult("FailedProvider", "fail.wav", None, None, "connection timeout"))

        tsv_path = write_tsv(reports, "test_diff")
        self.assertTrue(tsv_path.exists())

        lines = tsv_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        header = lines[0].split("\t")
        self.assertEqual(lines[0].count("\t") + 1, len(header))

        # All data rows have same number of columns as header
        for i, line in enumerate(lines[1:], 1):
            cols = line.split("\t")
            self.assertEqual(len(cols), len(header), f"Row {i} has {len(cols)} cols, header has {len(header)}")

        # Structural columns present
        self.assertEqual(header[0], "provider")
        self.assertEqual(header[1], "file")
        self.assertEqual(header[-2], "diff_report")
        self.assertEqual(header[-1], "error")

        # Metric columns auto-discovered from DiffReport
        self.assertIn("word_error_rate", header)
        self.assertIn("character_error_rate", header)

    def test_llm_semantic_compare(self) -> None:
        """Integration test for SemanticUnderstandingAnalyzer.

        Requires:
            - google-genai installed  (pip install google-genai)
            - GEMINI_API_KEY set in environment or .env

        Both conditions are checked inside the test — if either is missing the test
        *fails* explicitly (rather than being skipped) to make the requirement visible.
        Other test classes are not affected.

            pytest tests/test_diff.py::TestLLMUnderstanding -v
        """
        try:
            from helpers.semantic_understanding import SemanticUnderstandingAnalyzer
        except ImportError as exc:
            self.fail(f"google-genai not installed — run: pip install google-genai\n{exc}")

        load_dotenv()
        api_key = getenv("GEMINI_API_KEY")
        if not api_key:
            self.fail("GEMINI_API_KEY not set — add it to .env to run this test")

        result = asyncio.run(SemanticUnderstandingAnalyzer(api_key).compare(EXPECTED, GOT))

        self.assertIsInstance(result.score, float)
        self.assertGreaterEqual(result.score, 0.0)
        self.assertLessEqual(result.score, 100.0)
        self.assertIsInstance(result.detail, str)
        self.assertGreater(len(result.detail), 0)

        # GOT differs from EXPECTED only by two minor omissions — SER should be low
        self.assertLess(result.score, 50.0, "Expected low Semantic Error Rate for near-identical texts")