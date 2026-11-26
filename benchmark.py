"""
STT Provider Benchmark
======================

Runs every configured STT provider in parallel against every WAV/TXT asset pair and
writes the HTML diffs and `<timestamp>_benchmark.tsv` to `out/`.

Usage
-----
    source .venv/bin/activate
    python benchmark.py
    python benchmark.py --providers Speechmatics,Deepgram
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger, INFO
from os import getenv
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv

from helpers.diff_report import CustomMetricResult, DiffReport
from helpers.load_assets import get_test_files, AssetPair
from helpers.stream_wav import wav_duration_s
from helpers.transcribe import transcribe_and_diff
from pytest_config import CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, OUT_PATH, ASSETS_DIR, LOG_PATH
from universal_realtime_stt_tts.config import AUDIO_SAMPLE_RATE
from universal_realtime_stt_tts.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from universal_realtime_stt_tts.stt_provider_deepgram import DeepgramRealtimeProvider, DeepgramSttConfig
from universal_realtime_stt_tts.stt_provider_elevenlabs import ElevenLabsSttProvider, ElevenLabsSttConfig
from universal_realtime_stt_tts.stt_provider_gemini_live import GeminiLiveProvider, GeminiLiveSttConfig
from universal_realtime_stt_tts.stt_provider_google import GoogleRealtimeProvider, GoogleSttConfig
from universal_realtime_stt_tts.stt_provider_speechmatics import SpeechmaticsSttProvider, SpeechmaticsSttConfig
from universal_realtime_stt_tts.utils import setup_logging


setup_logging(INFO, log_dir=LOG_PATH)
logger = getLogger(__name__)
load_dotenv()


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


@dataclass(frozen=True)
class ProviderSpec:
    """Everything needed to instantiate and run one provider."""
    name: str
    cls: type[Any]
    config: Any


# (credential env var, benchmark name, provider class, config built from the credential).
# Google authenticates through ADC, so its credentials path only decides whether it runs.
PROVIDERS: list[tuple[str, str, type[Any], Callable[[str], Any]]] = [
    ("CARTESIA_API_KEY", "Cartesia", CartesiaInkProvider, lambda credential: CartesiaSttConfig(api_key=credential)),
    ("DEEPGRAM_API_KEY", "Deepgram", DeepgramRealtimeProvider, lambda credential: DeepgramSttConfig(api_key=credential)),
    ("ELEVENLABS_API_KEY", "ElevenLabs", ElevenLabsSttProvider,
     lambda credential: ElevenLabsSttConfig(api_key=credential)),
    ("ELEVENLABS_API_KEY", "ElevenLabsV3", ElevenLabsSttProvider,
     lambda credential: ElevenLabsSttConfig(api_key=credential, model="scribe_v3_live")),
    ("GOOGLE_APPLICATION_CREDENTIALS", "Google", GoogleRealtimeProvider, lambda credential: GoogleSttConfig()),
    ("SPEECHMATICS_API_KEY", "Speechmatics", SpeechmaticsSttProvider,
     lambda credential: SpeechmaticsSttConfig(api_key=credential)),
    ("GEMINI_API_KEY", "GeminiLive", GeminiLiveProvider, lambda credential: GeminiLiveSttConfig(api_key=credential)),
]


def build_provider_specs() -> list[ProviderSpec]:
    """Build list of providers that have valid credentials configured."""
    specs: list[ProviderSpec] = []
    for env_var, name, provider_class, make_config in PROVIDERS:
        credential = getenv(env_var)
        if credential:
            specs.append(ProviderSpec(name, provider_class, make_config(credential)))
        else:
            logger.warning("%s not set, skipping %s.", env_var, name)
    return specs


async def run_provider(
    spec: ProviderSpec,
    pairs: list[AssetPair],
    timestamp: str,
    all_results: list[BenchmarkResult],
    custom_metric_fn: Callable[[str, str], Awaitable[CustomMetricResult]] | None = None,
) -> None:
    """Run one provider against all asset files, appending each result to all_results and rewriting the TSV."""
    total = len(pairs)
    consecutive_failures = 0

    for position, pair in enumerate(pairs, 1):
        if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            message = f"Stopped after {CONSECUTIVE_FAILURE_LIMIT} consecutive failures"
            logger.error("[%s] %s: %s", spec.name, pair.wav.name, message)
            result = BenchmarkResult(spec.name, pair.wav.name, None, None, message)
        else:
            logger.info("[%s] (%d/%d) Processing %s ...", spec.name, position, total, pair.wav.name)
            started = time.monotonic()
            report_path = OUT_PATH / f"{timestamp}_{spec.name}_{pair.wav.stem}.diff.html"
            result = await _run_single_file(spec, pair, report_path, custom_metric_fn)
            elapsed = time.monotonic() - started
            if result.report:
                consecutive_failures = 0
                logger.info("[%s] (%d/%d) %s: WER %.1f%%, CER %.1f%% (%.1fs)",
                            spec.name, position, total, pair.wav.name,
                            result.report.word_error_rate, result.report.character_error_rate, elapsed)
            else:
                consecutive_failures += 1
                logger.error("[%s] (%d/%d) %s: FAILED (attempts exhausted, %.1fs): %s",
                             spec.name, position, total, pair.wav.name, elapsed, result.error)

        all_results.append(result)
        write_tsv(all_results, timestamp)


async def _run_single_file(
    spec: ProviderSpec,
    pair: AssetPair,
    report_path: Path,
    custom_metric_fn: Callable[[str, str], Awaitable[CustomMetricResult]] | None,
) -> BenchmarkResult:
    """Attempt to transcribe a single file with retry and timeout."""
    # Timeout scales with file duration: real-time streaming + generous headroom
    timeout_s = wav_duration_s(pair.wav) + 120

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
                    provider_name=spec.name,
                ),
                timeout=timeout_s,
            )
            return BenchmarkResult(spec.name, pair.wav.name, report, report_path, None)

        except asyncio.CancelledError:
            raise  # propagate cancellation, don't retry

        except asyncio.TimeoutError:
            last_error = f"Timed out after {timeout_s:.0f}s (attempt {attempt}/{MAX_RETRIES})"
            logger.warning("[%s] %s: %s", spec.name, pair.wav.name, last_error)

        except Exception as error:
            last_error = f"{error} (attempt {attempt}/{MAX_RETRIES})"
            logger.warning("[%s] %s: %s", spec.name, pair.wav.name, last_error)

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY_S)

    return BenchmarkResult(spec.name, pair.wav.name, None, None, last_error)


def write_tsv(results: list[BenchmarkResult], timestamp: str) -> Path | None:
    """Rewrite the full TSV snapshot of results, atomically.

    Deliberately synchronous: with no await inside, providers running in parallel on the
    event loop cannot interleave writes or append to results mid-write.
    """
    results_sorted = sorted(results, key=lambda result: (result.provider_name, result.file_name))

    # Union over all reports: a column such as custom_metric can be missing from some of them.
    metric_columns = list(dict.fromkeys(
        column for result in results_sorted if result.report for column in result.report.to_metrics_dict()
    ))

    header = ["provider", "file"] + metric_columns + ["diff_report", "error"]
    rows = ["\t".join(header)]
    for result in results_sorted:
        if result.report:
            metrics = result.report.to_metrics_dict()
            report_name = result.report_path.name if result.report_path else ""
            row = [metrics.get(column, "") for column in metric_columns] + [report_name, ""]
        else:
            row = [""] * len(metric_columns) + ["", result.error or "unknown error"]
        rows.append("\t".join([result.provider_name, result.file_name] + row))

    tsv_path = OUT_PATH / f"{timestamp}_benchmark.tsv"
    # Write beside the target, then rename: a crash mid-write leaves the previous snapshot intact.
    partial_path = tsv_path.with_name(tsv_path.name + ".partial")
    try:
        partial_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(partial_path, tsv_path)
    except OSError as error:
        logger.exception("Could not write %s: %r", tsv_path, error)
        print("\n".join(rows))
        return None

    return tsv_path


def select_specs(specs: list[ProviderSpec], requested_names: list[str]) -> list[ProviderSpec]:
    """Filter built specs down to the requested provider names (case-insensitive)."""
    available = {spec.name.lower(): spec for spec in specs}
    unknown = [name for name in requested_names if name.lower() not in available]
    if unknown:
        raise SystemExit(
            f"Unknown provider(s): {', '.join(unknown)}. "
            f"Available: {', '.join(spec.name for spec in specs)}"
        )
    return [available[name.lower()] for name in requested_names]


async def main(requested_names: list[str] | None = None) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    specs = build_provider_specs()
    if not specs:
        logger.error("No providers configured. Set API keys in .env and retry.")
        sys.exit(1)

    if requested_names:
        specs = select_specs(specs, requested_names)

    pairs = list(get_test_files(ASSETS_DIR))
    if not pairs:
        logger.error("No WAV/TXT asset pairs found in %s.", ASSETS_DIR)
        sys.exit(1)

    semantic_understanding_fn = None
    gemini_key = getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from helpers.semantic_understanding import SemanticUnderstandingAnalyzer
            semantic_understanding_fn = SemanticUnderstandingAnalyzer(api_key=gemini_key).compare
            logger.info("Semantic understanding metric enabled (Gemini).")
        except ImportError:
            logger.warning(
                "GEMINI_API_KEY is set but google-genai is not installed, so the "
                "semantic understanding metric is disabled. "
                "Install it with: pip install -e \".[gemini]\""
            )
    else:
        logger.warning("GEMINI_API_KEY not set, semantic understanding metric disabled.")

    logger.info("Benchmark starting: %d provider(s), %d file(s).", len(specs), len(pairs))

    all_results: list[BenchmarkResult] = []
    try:
        outcomes = await asyncio.gather(
            *(run_provider(spec, pairs, timestamp, all_results, custom_metric_fn=semantic_understanding_fn)
              for spec in specs),
            return_exceptions=True,
        )
        for spec, outcome in zip(specs, outcomes):
            if isinstance(outcome, BaseException):
                logger.error("[%s] Provider task crashed: %s", spec.name, outcome)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Benchmark interrupted, the TSV holds every finished file.")

    if not all_results:
        logger.error("No results collected.")
        sys.exit(1)

    tsv_path = OUT_PATH / f"{timestamp}_benchmark.tsv"
    logger.info("Benchmark complete. TSV report: %s", tsv_path)

    width = 72
    print(f"\n{'=' * width}")
    print(f"BENCHMARK RESULTS, {timestamp}")
    print(f"{'=' * width}")
    print(f"{'Provider':<16} {'File':<14} {'WER%':>6} {'CER%':>6} {'SER%':>6} {'Match%':>7} {'Exp':>5} {'Got':>5}")
    print(f"{'-' * width}")
    for result in sorted(all_results, key=lambda result: (result.provider_name, result.file_name)):
        report = result.report
        if report:
            semantic = f"{report.custom_metric.score:>5.1f}%" if report.custom_metric is not None else f"{'-':>6}"
            print(f"{result.provider_name:<16} {result.file_name:<14} {report.word_error_rate:>5.1f}% "
                  f"{report.character_error_rate:>5.1f}% {semantic} {report.match_percentage:>6.1f}% "
                  f"{report.chars_expected:>5} {report.chars_got:>5}")
        else:
            print(f"{result.provider_name:<16} {result.file_name:<14} {'FAILED':>6}  {result.error or ''}")
    print(f"{'=' * width}")
    print(f"TSV: {tsv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the STT provider benchmark.")
    parser.add_argument(
        "--providers",
        help="Comma-separated provider names to run (default: all configured).",
    )
    args = parser.parse_args()
    selected = [name.strip() for name in args.providers.split(",") if name.strip()] if args.providers else None

    try:
        asyncio.run(main(selected))
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(130)
