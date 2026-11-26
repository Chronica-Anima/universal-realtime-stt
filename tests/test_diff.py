"""
Tests for diff report generation and the benchmark TSV, on sample Czech text with no STT
provider or audio involved.

    pytest tests/test_diff.py -v -k "not llm"    # the llm test calls Gemini and is billed
"""
from __future__ import annotations

import asyncio
import csv
import unittest
import unittest.mock
from os import getenv
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from helpers.diff_report import CustomMetricResult, DiffReport
from helpers.report_summary import FileScores, RunSpec, load_scores

FIXTURES = Path(__file__).parent / "fixtures" / "diff-report"

# EXPECTED is the ground truth; GOT drops one comma and the word "v", as STT output often does.
EXPECTED = (FIXTURES / "expected.txt").read_text(encoding="utf-8")
GOT = (FIXTURES / "got.txt").read_text(encoding="utf-8")


class TestDiffReport(unittest.TestCase):

    def test_metrics_ignore_punctuation_and_count_the_dropped_word(self) -> None:
        self.assertEqual(DiffReport(EXPECTED, GOT).to_metrics_dict(), {
            "char_levenshtein": "2",
            "chars_expected": "187",
            "words_expected": "30",
            "chars_got": "185",
            "words_got": "29",
            "chars_matched": "185",
            "chars_inserted": "0",
            "chars_deleted": "2",
            "word_levenshtein": "1",
            "character_error_rate": "1.1",
            "word_error_rate": "3.3",
            "match_percentage": "98.9",
        })

    def test_write_html_renders_title_and_error_rates(self) -> None:
        with TemporaryDirectory() as directory:
            html_path = DiffReport(EXPECTED, GOT).write_html(
                Path(directory) / "sample.diff.html", title="lecture intro", detail="unit test",
            )
            html = html_path.read_text(encoding="utf-8")

        self.assertIn("<h1>lecture intro: 3.3% WER / 1.1% CER</h1>", html)
        self.assertIn('<div class="stat-value">3.3%</div>', html)
        self.assertIn('<div class="stat-value">1.1%</div>', html)
        self.assertIn("<div class='hint'>unit test</div>", html)

    def test_write_tsv_output_reads_back_through_load_scores(self) -> None:
        """The custom_metric column comes from a later report, so the first report's row leaves it blank."""
        import benchmark
        from benchmark import BenchmarkResult

        results = [
            BenchmarkResult("Alpha", "lecture.wav", DiffReport(EXPECTED, GOT), Path("alpha.diff.html"), None),
            BenchmarkResult("Beta", "lecture.wav",
                            DiffReport(EXPECTED, GOT, CustomMetricResult(12.5, "one fact missing")),
                            Path("beta.diff.html"), None),
            BenchmarkResult("Gamma", "lecture.wav", None, None, "connection timeout"),
        ]
        with TemporaryDirectory() as directory, unittest.mock.patch.object(benchmark, "OUT_PATH", Path(directory)):
            tsv_path = benchmark.write_tsv(results, "20260101_000000")
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["20260101_000000_benchmark.tsv"])

            self.assertEqual(load_scores(RunSpec("Alpha", tsv_path, "Alpha")),
                             {"lecture.wav": FileScores(3.3, 1.1, None)})
            self.assertEqual(load_scores(RunSpec("Beta", tsv_path, "Beta")),
                             {"lecture.wav": FileScores(3.3, 1.1, 12.5)})
            with self.assertRaisesRegex(ValueError, "No scored rows for provider 'Gamma'"):
                load_scores(RunSpec("Gamma", tsv_path, "Gamma"))

            with tsv_path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))

        self.assertEqual([(row["provider"], row["diff_report"], row["error"]) for row in rows], [
            ("Alpha", "alpha.diff.html", ""),
            ("Beta", "beta.diff.html", ""),
            ("Gamma", "", "connection timeout"),
        ])

    def test_llm_semantic_compare(self) -> None:
        """Calls Gemini, so it needs google-genai and GEMINI_API_KEY, and fails rather than skips without them.

            pytest tests/test_diff.py -v -k llm
        """
        try:
            from helpers.semantic_understanding import SemanticUnderstandingAnalyzer
        except ImportError as error:
            self.fail(f"google-genai not installed, run: pip install google-genai\n{error}")

        load_dotenv()
        api_key = getenv("GEMINI_API_KEY")
        if not api_key:
            self.fail("GEMINI_API_KEY not set, add it to .env to run this test")

        result = asyncio.run(SemanticUnderstandingAnalyzer(api_key).compare(EXPECTED, GOT))

        self.assertGreaterEqual(result.score, 0.0)
        self.assertLess(result.score, 50.0, "GOT only drops a comma and one preposition, so SER should be low")
        self.assertTrue(result.detail.strip())
