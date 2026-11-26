"""
Tests for helpers/report_summary.py: run parsing, the meaningful-file rule and averaging.

    pytest tests/test_report_summary.py -v
"""
from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from helpers.report_summary import (
    RunSpec, format_table, load_scores, main, meaningful_files, parse_run_spec, summarize,
)

FIXTURES = Path(__file__).parent / "fixtures" / "report-summary"
TWO_PROVIDERS_RUN = FIXTURES / "two-providers-run.tsv"
PARTIAL_RUN = FIXTURES / "partial-run.tsv"


class TestParseRunSpec(unittest.TestCase):

    def test_provider_defaults_to_label(self) -> None:
        self.assertEqual(parse_run_spec("Cartesia=out/run.tsv"), RunSpec("Cartesia", Path("out/run.tsv"), "Cartesia"))

    def test_explicit_provider_column_value(self) -> None:
        self.assertEqual(
            parse_run_spec("Speechmatics Enhanced=out/run.tsv:Speechmatics"),
            RunSpec("Speechmatics Enhanced", Path("out/run.tsv"), "Speechmatics"),
        )

    def test_missing_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_run_spec("Cartesia")


class TestReportSummary(unittest.TestCase):

    def setUp(self) -> None:
        self.alpha = load_scores(RunSpec("Alpha", TWO_PROVIDERS_RUN, "Alpha"))
        self.beta = load_scores(RunSpec("Beta", TWO_PROVIDERS_RUN, "Beta"))
        self.gamma = load_scores(RunSpec("Gamma later", PARTIAL_RUN, "Gamma"))

    def test_failed_rows_carry_no_scores(self) -> None:
        self.assertEqual(sorted(self.beta), ["borderline.wav", "clear.wav"])

    def test_blank_semantic_score_is_none(self) -> None:
        self.assertIsNone(self.alpha["borderline.wav"].semantic_error_rate)
        self.assertEqual(self.alpha["clear.wav"].semantic_error_rate, 5.0)

    def test_meaningful_files_are_those_some_provider_understood(self) -> None:
        """murky.wav is best at 85% WER; borderline.wav is exactly at the cutoff and stays."""
        self.assertEqual(
            meaningful_files([self.alpha, self.beta, self.gamma], word_error_rate_cutoff=50),
            ["borderline.wav", "clear.wav"],
        )

    def test_lower_cutoff_drops_borderline_file(self) -> None:
        self.assertEqual(meaningful_files([self.alpha, self.beta, self.gamma], word_error_rate_cutoff=40),
                         ["clear.wav"])

    def test_averages_cover_only_the_file_set(self) -> None:
        summary = summarize("Alpha", self.alpha, ["borderline.wav", "clear.wav"])
        self.assertEqual(summary.word_error_rate, 40.0)
        self.assertEqual(summary.character_error_rate, 20.0)
        self.assertEqual(summary.semantic_error_rate, 5.0)
        self.assertEqual(summary.semantic_files_scored, 1)
        self.assertEqual(summary.files_scored, 2)
        self.assertEqual(summary.missing_files, [])

    def test_missing_files_are_reported(self) -> None:
        summary = summarize("Gamma later", self.gamma, ["borderline.wav", "clear.wav"])
        self.assertEqual(summary.missing_files, ["borderline.wav"])
        self.assertEqual(summary.word_error_rate, 8.0)

    def test_table_rows(self) -> None:
        file_set = ["borderline.wav", "clear.wav"]
        table = format_table([summarize("Alpha", self.alpha, file_set), summarize("Beta", self.beta, file_set)])
        self.assertEqual(table.splitlines()[2], "| Alpha | 40.0 | 20.0 | 5.0 | 2 | 1 |")
        self.assertEqual(table.splitlines()[3], "| Beta | 45.0 | 22.5 | 22.5 | 2 | 2 |")

    def test_cli_derives_the_set_and_prints_the_table(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            main([f"Alpha={TWO_PROVIDERS_RUN}", f"Beta={TWO_PROVIDERS_RUN}", f"Gamma later={PARTIAL_RUN}:Gamma",
                  "--cutoff", "50"])
        lines = output.getvalue().splitlines()
        self.assertEqual(lines[0], "2 meaningful files (best WER <= 50%)")
        self.assertIn("| Gamma later | 8.0 | 4.0 | 2.0 | 1 | 1 |", lines)
