"""
Live smoke tests for the STT providers, billed by each provider.

Each test streams the first two asset files through one provider in real time and
passes when every file comes back with a non-empty transcript. That proves the
connection and the real-time pipeline work; accuracy is measured by benchmark.py.

    pytest tests/test_stt.py::TestStt::test_deepgram -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from logging import getLogger
from os import getenv
from typing import Any

from dotenv import load_dotenv

from helpers.load_assets import get_test_files
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


setup_logging(log_dir=LOG_PATH)
logger = getLogger(__name__)
load_dotenv()

# Two files keep a provider's run short; the benchmark covers every asset.
FILES_PER_PROVIDER = 2


class TestStt(unittest.IsolatedAsyncioTestCase):
    async def _runner(self, provider_class: type, config: Any) -> None:
        """Stream the first asset files through the provider and write an HTML diff per file and model to out/."""
        # The model keeps reports apart when one provider class runs several models, e.g. ElevenLabs v2 and v3.
        # GoogleSttConfig has no model field and always uses Google's default.
        model = getattr(config, "model", "default")
        report_prefix = f"{datetime.now():%Y%m%d_%H%M%S}_{provider_class.__name__}_{model}"

        pairs = list(get_test_files(ASSETS_DIR))[:FILES_PER_PROVIDER]
        self.assertTrue(pairs, f"Found no files to test. Requires at least one wav/txt pair in {ASSETS_DIR}.")

        for pair in pairs:
            with self.subTest(msg=pair.wav.name):
                report = await transcribe_and_diff(
                    provider_class(config),
                    pair.wav,
                    pair.txt,
                    OUT_PATH / f"{report_prefix}_{pair.wav.stem}.diff.html",
                    chunk_ms=CHUNK_MS,
                    sample_rate=AUDIO_SAMPLE_RATE,
                    realtime_factor=TEST_REALTIME_FACTOR,
                    silence_s=FINAL_SILENCE_S,
                )
                logger.info("%s WER: %.1f%%, CER: %.1f%%",
                            pair.wav.name, report.word_error_rate, report.character_error_rate)
                self.assertGreater(len(report.text_got.strip()), 0, "Provider returned no transcript")

    async def test_cartesia(self) -> None:
        await self._runner(CartesiaInkProvider, CartesiaSttConfig(api_key=getenv("CARTESIA_API_KEY")))

    async def test_deepgram(self) -> None:
        await self._runner(DeepgramRealtimeProvider, DeepgramSttConfig(api_key=getenv("DEEPGRAM_API_KEY")))

    async def test_eleven_labs(self) -> None:
        await self._runner(ElevenLabsSttProvider, ElevenLabsSttConfig(api_key=getenv("ELEVENLABS_API_KEY")))

    async def test_eleven_labs_v3(self) -> None:
        config = ElevenLabsSttConfig(api_key=getenv("ELEVENLABS_API_KEY"), model="scribe_v3_live")
        await self._runner(ElevenLabsSttProvider, config)

    async def test_google(self) -> None:
        # Google authenticates through Application Default Credentials: GOOGLE_APPLICATION_CREDENTIALS
        # points at a service-account JSON.
        await self._runner(GoogleRealtimeProvider, GoogleSttConfig())

    async def test_speechmatics(self) -> None:
        await self._runner(SpeechmaticsSttProvider, SpeechmaticsSttConfig(api_key=getenv("SPEECHMATICS_API_KEY")))

    async def test_gemini_live(self) -> None:
        api_key = getenv("GEMINI_API_KEY")
        if not api_key:
            self.fail("GEMINI_API_KEY not set, add it to .env to run this test")
        await self._runner(GeminiLiveProvider, GeminiLiveSttConfig(api_key=api_key))
