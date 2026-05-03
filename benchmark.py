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
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger, INFO
from os import getenv
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, OUT_PATH, ASSETS_DIR
from helpers.diff_report import CustomMetricResult, DiffReport
from helpers.load_assets import get_test_files, AssetPair
from helpers.stream_wav import inspect_wav
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

MAX_RETRIES = 3
RETRY_DELAY_S = 30.0
CONSECUTIVE_FAILURE_LIMIT = 5  # stop provider after this many failures in a row


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
    cls: type[Any]
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

async def run_provider(
    spec: ProviderSpec,
    pairs: list[AssetPair],
    ts: str,
    results_acc: list[BenchmarkResult],
    results_lock: asyncio.Lock,
    custom_metric_fn: Callable[[str, str], Awaitable[CustomMetricResult]] | None = None,
) -> list[BenchmarkResult]:
    """Run one provider against all asset files. Returns one result per file.

    Results are also appended to `results_acc` (under lock) for incremental TSV writes.
    """
    results: list[BenchmarkResult] = []
    total = len(pairs)
    consecutive_failures = 0

    for idx, pair in enumerate(pairs, 1):
        # Circuit breaker: stop after too many consecutive failures
        if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            msg = f"Stopped after {CONSECUTIVE_FAILURE_LIMIT} consecutive failures"
            logger.error("[%s] %s — %s", spec.name, pair.wav.name, msg)
            result = BenchmarkResult(spec.name, pair.wav.name, None, None, msg)
            results.append(result)
            async with results_lock:
                results_acc.append(result)
            continue

        logger.info("[%s] (%d/%d) Processing %s ...", spec.name, idx, total, pair.wav.name)
        t0 = time.monotonic()
        report_path = OUT_PATH / f"{ts}_{spec.name}_{pair.wav.stem}.diff.html"

        result = await _run_single_file(spec, pair, report_path, custom_metric_fn)

        elapsed = time.monotonic() - t0
        if result.report:
            consecutive_failures = 0
            logger.info("[%s] (%d/%d) %s — WER: %.1f%%, CER: %.1f%% (%.1fs)",
                        spec.name, idx, total, pair.wav.name,
                        result.report.word_error_rate, result.report.character_error_rate, elapsed)
        else:
            consecutive_failures += 1
            logger.error("[%s] (%d/%d) %s — FAILED (attempt exhausted, %.1fs): %s",
                         spec.name, idx, total, pair.wav.name, elapsed, result.error)

        results.append(result)
        async with results_lock:
            results_acc.append(result)

    return results


async def _run_single_file(
    spec: ProviderSpec,
    pair: AssetPair,
    report_path: Path,
    custom_metric_fn: Callable[[str, str], Awaitable[CustomMetricResult]] | None,
) -> BenchmarkResult:
    """Attempt to transcribe a single file with retry and timeout."""
    # Timeout scales with file duration: real-time streaming + generous headroom
    wav_info = inspect_wav(pair.wav)
    timeout_s = wav_info.n_frames / wav_info.sample_rate + 120

    last_error: str = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            provider = spec.cls(spec.config)
            report = await asyncio.wait_for(
                transcribe_and_diff(
                    provider,
                    pair.wav,
                    pair.txt,
                    report_path,
                    chunk_ms=CHUNK_MS,
                    sample_rate=AUDIO_SAMPLE_RATE,
                    realtime_factor=TEST_REALTIME_FACTOR,
                    silence_s=FINAL_SILENCE_S,
                    custom_metric_fn=custom_metric_fn,
                ),
                timeout=timeout_s,
            )
            return BenchmarkResult(spec.name, pair.wav.name, report, report_path, None)

        except asyncio.CancelledError:
            raise  # propagate cancellation, don't retry

        except asyncio.TimeoutError:
            last_error = f"Timed out after {timeout_s:.0f}s (attempt {attempt}/{MAX_RETRIES})"
            logger.warning("[%s] %s — %s", spec.name, pair.wav.name, last_error)

        except Exception as exc:
            last_error = f"{exc} (attempt {attempt}/{MAX_RETRIES})"
            logger.warning("[%s] %s — %s", spec.name, pair.wav.name, last_error)

        # Delay before retry (but not after the last attempt)
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY_S)

    return BenchmarkResult(spec.name, pair.wav.name, None, None, last_error)


# ---------------------------------------------------------------------------
# TSV report writer
# ---------------------------------------------------------------------------

def write_tsv(results: list[BenchmarkResult], ts: str) -> Path | None:
    """Write all benchmark results to a single TSV file, overwriting any previous version.

    Called periodically and on shutdown with the full accumulated result list —
    each write produces a complete snapshot, not an incremental append.
    Columns are derived from DiffReport.to_metrics_dict().
    """
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

    try:
        tsv_path = OUT_PATH / f"{ts}_benchmark.tsv"
        tsv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.exception("Could not write file: %r", exc)
        print(rows)
        return None

    return tsv_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # SIGTERM cancels this task so the finally block can flush results
    main_task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda *_: main_task.cancel())  # noqa

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

    # Append-only accumulator — periodic flush and final write both rewrite the full TSV
    all_results: list[BenchmarkResult] = []
    results_lock = asyncio.Lock()

    # Periodic TSV flush task
    async def periodic_flush():
        while True:
            await asyncio.sleep(300)
            async with results_lock:
                if all_results:
                    write_tsv(list(all_results), ts)

    flush_task = asyncio.create_task(periodic_flush())

    # Run all providers in parallel with return_exceptions=True
    try:
        nested = await asyncio.gather(
            *(run_provider(spec, pairs, ts, all_results, results_lock,
                           custom_metric_fn=semantic_understanding_fn)
              for spec in specs),
            return_exceptions=True,
        )
        # Handle any provider-level exceptions
        for spec, result in zip(specs, nested):
            if isinstance(result, BaseException):
                logger.error("[%s] Provider task crashed: %s", spec.name, result)

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Benchmark interrupted — saving partial results.")

    finally:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        tsv_path = write_tsv(list(all_results), ts) if all_results else None

    if not all_results:
        logger.error("No results collected.")
        sys.exit(1)

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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(130)