"""
STT Provider Benchmark
======================

Runs all STT providers in parallel against every WAV/TXT asset pair,
collects accuracy metrics, and writes a TSV summary report.

Architecture
------------
1. **Provider registry** — a list of (provider_class, config) tuples built
   from environment variables. Providers whose API key is missing are skipped
   with a warning (except Google which uses ADC).

2. **Parallel execution** — each provider gets its own async task via
   asyncio.gather. Within a provider task, asset files are processed
   sequentially (streaming is real-time so we can't rush it). The design
   isolates providers from each other: one provider failing does not affect
   the others.

   Future extension: the inner file loop can be parallelised too — each
   file would get its own provider instance and task. The result collection
   is already flat (provider x file), so this requires no structural change.

3. **Result collation** — every (provider, file) pair produces a DiffReport.
   These are flattened into a single list, sorted by provider then file,
   and written as a TSV file to the `out/` directory. The TSV includes:
   provider, file, chars_expected, chars_got, CER%, match%, char_levenshtein,
   matched/inserted/deleted char counts, and the path to the HTML diff.

Usage
-----
    source .venv/bin/activate
    python benchmark.py
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger, INFO
from os import getenv
from pathlib import Path
from typing import Any, Type, List

from dotenv import load_dotenv

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, OUT_PATH, ASSETS_DIR
from helpers.diff_report import DiffReport
from helpers.load_assets import get_test_files, AssetPair
from helpers.transcribe import transcribe_and_diff
from lib.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from lib.stt_provider_deepgram import DeepgramRealtimeProvider, DeepgramSttConfig
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider, ElevenLabsSttConfig
from lib.stt_provider_gemini_live import GeminiLiveProvider, GeminiLiveSttConfig
from lib.stt_provider_google import GoogleRealtimeProvider, GoogleSttConfig
from lib.stt_provider_speechmatics import SpeechmaticsRealtimeProvider, SpeechmaticsSttConfig
from lib.utils import setup_logging

setup_logging(INFO)
logger = getLogger(__name__)
load_dotenv()


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BenchmarkResult:
    provider_name: str
    file_name: str
    report: DiffReport | None  # None when the run failed
    report_path: Path | None  # path to HTML diff report
    error: str | None  # error message if failed


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderSpec:
    """Everything needed to instantiate and run one provider."""
    name: str
    cls: Type[Any]
    config: Any


def build_provider_specs() -> list[ProviderSpec]:
    """Build list of providers that have valid credentials configured."""
    specs: list[ProviderSpec] = []

    key = getenv("CARTESIA_API_KEY")
    if key:
        specs.append(ProviderSpec("Cartesia", CartesiaInkProvider, CartesiaSttConfig(api_key=key)))
    else:
        logger.warning("CARTESIA_API_KEY not set — skipping Cartesia.")

    key = getenv("DEEPGRAM_API_KEY")
    if key:
        specs.append(ProviderSpec("Deepgram", DeepgramRealtimeProvider, DeepgramSttConfig(api_key=key)))
    else:
        logger.warning("DEEPGRAM_API_KEY not set — skipping Deepgram.")

    key = getenv("ELEVENLABS_API_KEY")
    if key:
        specs.append(ProviderSpec("ElevenLabs", ElevenLabsRealtimeProvider, ElevenLabsSttConfig(api_key=key)))
    else:
        logger.warning("ELEVENLABS_API_KEY not set — skipping ElevenLabs.")

    # Google uses ADC — no API key needed, but check if credentials file is set
    if getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        specs.append(ProviderSpec("Google", GoogleRealtimeProvider, GoogleSttConfig()))
    else:
        logger.warning("GOOGLE_APPLICATION_CREDENTIALS not set — skipping Google.")

    key = getenv("SPEECHMATICS_API_KEY")
    if key:
        specs.append(ProviderSpec("Speechmatics", SpeechmaticsRealtimeProvider, SpeechmaticsSttConfig(api_key=key)))
    else:
        logger.warning("SPEECHMATICS_API_KEY not set — skipping Speechmatics.")

    key = getenv("GEMINI_API_KEY")
    if key:
        specs.append(ProviderSpec("GeminiLive", GeminiLiveProvider, GeminiLiveSttConfig(api_key=key)))
    else:
        logger.warning("GEMINI_API_KEY not set — skipping Gemini Live.")

    return specs


# ---------------------------------------------------------------------------
# Per-provider runner (processes all files sequentially)
# ---------------------------------------------------------------------------

async def run_provider(spec: ProviderSpec, pairs: list[AssetPair], ts: str, custom_metric_fn=None) -> list[BenchmarkResult]:
    """Run one provider against all asset files. Returns one result per file."""
    results: list[BenchmarkResult] = []

    for pair in pairs:
        logger.info("[%s] Processing %s ...", spec.name, pair.wav.name)
        report_path = OUT_PATH / f"{ts}_{spec.name}_{pair.wav.stem}.diff.html"
        try:
            provider = spec.cls(spec.config)
            report = await transcribe_and_diff(
                provider,
                pair.wav,
                pair.txt,
                report_path,
                chunk_ms=CHUNK_MS,
                sample_rate=AUDIO_SAMPLE_RATE,
                realtime_factor=TEST_REALTIME_FACTOR,
                silence_s=FINAL_SILENCE_S,
                custom_metric_fn=custom_metric_fn,
            )
            logger.info("[%s] %s — WER: %.1f%%, CER: %.1f%%", spec.name, pair.wav.name, report.word_error_rate, report.character_error_rate)
            results.append(BenchmarkResult(spec.name, pair.wav.name, report, report_path, None))
        except Exception as exc:
            logger.error("[%s] %s — FAILED: %s", spec.name, pair.wav.name, exc)
            results.append(BenchmarkResult(spec.name, pair.wav.name, None, None, str(exc)))

    return results


# ---------------------------------------------------------------------------
# TSV report writer
# ---------------------------------------------------------------------------

def write_tsv(results: list[BenchmarkResult], ts: str) -> Path:
    """Write benchmark results to TSV. Columns are derived from DiffReport.to_metrics_dict()."""
    results_sorted = sorted(results, key=lambda r: (r.provider_name, r.file_name))

    # Discover metric columns from the first successful report
    sample = next((r.report for r in results_sorted if r.report), None)
    metric_cols = list(sample.to_metrics_dict()) if sample else []

    header = ["provider", "file"] + metric_cols + ["diff_report", "error"]
    rows = ["\t".join(header)]
    for r in results_sorted:
        if r.report:
            metrics = r.report.to_metrics_dict()
            row = [r.provider_name, r.file_name] + [metrics.get(c, "") for c in metric_cols] + [r.report_path.name if r.report_path else "", ""]
        else:
            row = [r.provider_name, r.file_name] + [""] * len(metric_cols) + ["", r.error or "unknown error"]
        rows.append("\t".join(row))

    tsv_path = OUT_PATH / f"{ts}_benchmark.tsv"
    tsv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return tsv_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    specs = build_provider_specs()
    if not specs:
        logger.error("No providers configured. Set API keys in .env and retry.")
        sys.exit(1)

    pairs = list(get_test_files(ASSETS_DIR))
    if not pairs:
        logger.error("No WAV/TXT asset pairs found in %s.", ASSETS_DIR)
        sys.exit(1)

    semantic_understanding_fn = None
    gemini_key = getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from helpers.semantic_understanding import SemanticUnderstandingAnalyzer  # noqa: F821 — false positive, not used if import fails
            semantic_understanding_fn = SemanticUnderstandingAnalyzer(api_key=gemini_key).compare
            logger.info("Semantic understanding metric enabled (Gemini).")
        except ImportError:
            logger.warning(
                "GEMINI_API_KEY is set but google-genai is not installed — "
                "semantic understanding metric disabled. "
                "Install it with: pip install google-genai  "
                "(or uncomment google-genai in requirements.txt and run: pip install -r requirements.txt)"
            )
    else:
        logger.warning("GEMINI_API_KEY not set — semantic understanding metric disabled.")

    logger.info("Benchmark starting: %d provider(s), %d file(s).", len(specs), len(pairs))

    # Run all providers in parallel
    nested: list[list[BenchmarkResult]] = await asyncio.gather( *(run_provider(spec, pairs, ts, custom_metric_fn=semantic_understanding_fn) for spec in specs),)  # type: ignore[assignment] — asyncio.gather returns tuple, but runtime values match
    all_results: List[BenchmarkResult] = [r for provider_results in nested for r in provider_results]

    # Write TSV
    tsv_path = write_tsv(all_results, ts)
    logger.info("Benchmark complete. TSV report: %s", tsv_path)

    # Print summary to stdout
    width = 72

    print(f"\n{'=' * width}")
    print(f"BENCHMARK RESULTS — {ts}")
    print(f"{'=' * width}")
    print(f"{'Provider':<16} {'File':<14} {'WER%':>6} {'CER%':>6} {'SER%':>6} {'Match%':>7} {'Exp':>5} {'Got':>5}")
    print(f"{'-' * width}")
    for r in sorted(all_results, key=lambda x: (x.provider_name, x.file_name)):
        if r.report:
            rp = r.report
            ser = f"{rp.custom_metric.score:>5.1f}%" if rp.custom_metric is not None else f"{'—':>6}"
            print(f"{r.provider_name:<16} {r.file_name:<14} {rp.word_error_rate:>5.1f}% {rp.character_error_rate:>5.1f}% {ser} {rp.match_percentage:>6.1f}% {rp.chars_expected:>5} {rp.chars_got:>5}")
        else:
            print(f"{r.provider_name:<16} {r.file_name:<14} {'FAILED':>6}  {r.error or ''}")
    print(f"{'=' * width}")
    print(f"TSV: {tsv_path}")


if __name__ == "__main__":
    asyncio.run(main())