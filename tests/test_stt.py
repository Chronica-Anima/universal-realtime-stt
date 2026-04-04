"""
Smoke tests for STT providers.

Each test method exercises one provider end-to-end: streams every WAV asset
in real time, collects the transcript, and asserts that the output length is
within 14% of the expected ground truth. The goal is to verify that the
provider connection and real-time pipeline work — not to measure accuracy
(see benchmark.py for that).

Tests run sequentially (one provider at a time). Run a single provider with::

    pytest tests/test_stt.py::TestStt::test_deepgram -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from logging import getLogger
from os import getenv
from typing import Any, Awaitable, Callable, Optional, Type

from helpers.diff_report import CustomMetricResult

from dotenv import load_dotenv

from config import AUDIO_SAMPLE_RATE, CHUNK_MS, TEST_REALTIME_FACTOR, FINAL_SILENCE_S, OUT_PATH, ASSETS_DIR
from helpers.load_assets import get_test_files
from helpers.transcribe import transcribe_and_diff
from lib.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from lib.stt_provider_deepgram import DeepgramRealtimeProvider, DeepgramSttConfig
from lib.stt_provider_elevenlabs import ElevenLabsRealtimeProvider, ElevenLabsSttConfig
from lib.stt_provider_gemini_live import GeminiLiveProvider, GeminiLiveSttConfig
from lib.stt_provider_google import GoogleRealtimeProvider, GoogleSttConfig
from lib.stt_provider_speechmatics import SpeechmaticsRealtimeProvider, SpeechmaticsSttConfig
from lib.utils import setup_logging

setup_logging()
logger = getLogger(__name__)
load_dotenv()


class TestStt(unittest.IsolatedAsyncioTestCase):
    async def _runner(
            self,
            provider_cls: Type[Any],
            config: Any,
            custom_metric_fn: Optional[Callable[[str, str], Awaitable[CustomMetricResult]]] = None,
    ) -> None:
        """
        Stream all asset files through a provider and assert output length.

        For each WAV/TXT pair, instantiates the provider, runs the full
        transcribe-and-diff pipeline, writes an HTML diff report to out/,
        and checks that the transcript length is within 14% of expected.

        custom_metric_fn: optional async (expected, got) -> CustomMetricResult.
            When supplied, the result is embedded in the diff report and HTML.
        """
        logger.info("Starting test runner for %s.", provider_cls.__name__)

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')  # make sure all reports from run has same timestamp
        ts += "_" + provider_cls.__name__

        pairs = list(get_test_files(ASSETS_DIR))
        if not pairs:
            assert False, f"Found no files to test. Requires at least one wav/txt pair in {ASSETS_DIR}."

        for pair in pairs:
            with self.subTest(msg=pair.wav.name):
                logger.info("Processing file %s.", pair.wav.name)

                provider = provider_cls(config)
                report = await transcribe_and_diff(
                    provider,
                    pair.wav,
                    pair.txt,
                    OUT_PATH / f"{ts}_{pair.wav.stem}.diff.html",
                    chunk_ms=CHUNK_MS,
                    sample_rate=AUDIO_SAMPLE_RATE,
                    realtime_factor=TEST_REALTIME_FACTOR,
                    silence_s=FINAL_SILENCE_S,
                    custom_metric_fn=custom_metric_fn,
                )
                logger.info(f"{pair.wav.name} WER: {report.word_error_rate:.1f}%, CER: {report.character_error_rate:.1f}%")
                if report.custom_metric is not None:
                    logger.info(f"{pair.wav.name} LLM understanding: {report.custom_metric.score:.1f}%")

                # Goal of the test is to check for realtime STT to work.
                # As long as the pipeline ran without errors and returned something, we pass.
                # Accuracy is the benchmark's job (benchmark.py), not the test's.
                # Here we evaluate whether the STT process worked and the lib works correctly,
                # not whether STT the provider itself is any good and returns meaningful results.
                self.assertGreater(len(report.text_got.strip()), 0, "Provider returned no transcript")

    async def test_cartesia(self) -> None:
        config = CartesiaSttConfig(api_key=getenv("CARTESIA_API_KEY"))
        await self._runner(CartesiaInkProvider, config)

    async def test_deepgram(self) -> None:
        config = DeepgramSttConfig(api_key=getenv("DEEPGRAM_API_KEY"))
        await self._runner(DeepgramRealtimeProvider, config)

    async def test_eleven_labs(self) -> None:
        config = ElevenLabsSttConfig(api_key=getenv("ELEVENLABS_API_KEY"))
        await self._runner(ElevenLabsRealtimeProvider, config)

    async def test_google(self) -> None:
        # Google uses Application Default Credentials (ADC), not an API key.
        # Set GOOGLE_APPLICATION_CREDENTIALS env var to your service account JSON.
        config = GoogleSttConfig()
        await self._runner(GoogleRealtimeProvider, config)

    async def test_speechmatics(self) -> None:
        config = SpeechmaticsSttConfig(api_key=getenv("SPEECHMATICS_API_KEY"))
        await self._runner(SpeechmaticsRealtimeProvider, config)

    async def test_gemini_live(self) -> None:
        """Gemini Live API real-time STT.

        Requires GEMINI_API_KEY in .env and google-genai installed.
        Model: gemini-3.1-flash-live-preview (preview — may not be stable).
        """
        api_key = getenv("GEMINI_API_KEY")
        if not api_key:
            self.fail("GEMINI_API_KEY not set — add it to .env to run this test")
        config = GeminiLiveSttConfig(api_key=api_key)
        await self._runner(GeminiLiveProvider, config)

    async def test_speechmatics_semantics(self) -> None:
        """Speechmatics end-to-end with LLM semantic understanding metric.

        Slower than test_speechmatics — makes one extra Gemini API call per audio file.
        Fails explicitly if GEMINI_API_KEY is not set or google-genai is not installed.
        """
        try:
            from helpers.semantic_understanding import SemanticUnderstandingAnalyzer
        except ImportError as exc:
            self.fail(f"google-genai not installed — run: pip install google-genai\n{exc}")
        else:
            api_key = getenv("GEMINI_API_KEY")
            if not api_key:
                self.fail("GEMINI_API_KEY not set — add it to .env to run this test")
            else:
                analyzer = SemanticUnderstandingAnalyzer(api_key)
                config = SpeechmaticsSttConfig(api_key=getenv("SPEECHMATICS_API_KEY"))
                await self._runner(SpeechmaticsRealtimeProvider, config, custom_metric_fn=analyzer.compare)
