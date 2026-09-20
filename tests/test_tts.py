"""
Smoke tests for the ElevenLabs TTS provider against the live API.

One synthesis per model, asserting that usable PCM comes back and that the event
loop stays free while it does: a 50 ms ticker runs alongside and must never miss a
beat by more than MAX_TICK_GAP_S, which is what fails if the provider ever goes
back to driving a synchronous client from a coroutine.

    pytest tests/test_tts.py -v

Requires ELEVENLABS_API_KEY in .env.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from array import array
from logging import getLogger
from math import sqrt
from os import getenv
from time import monotonic

from dotenv import load_dotenv

from universal_realtime_stt_tts.tts_provider_elevenlabs import (
    ElevenLabsTtsProvider, ElevenLabsTtsConfig,
)


logger = getLogger(__name__)
load_dotenv()

PCM_SAMPLE_RATE = 24000
PCM_BYTES_PER_SAMPLE = 2
# The sentence runs about 4 s on every model measured; the band is wide enough for a
# model that paces differently and narrow enough to catch a truncated or padded reply.
MIN_AUDIO_SECONDS = 1.5
MAX_AUDIO_SECONDS = 10.0
# Silence is 0 and normal speech peaks near the int16 range, so anything above this is
# audio rather than a stream of well-formed nothing.
MIN_AUDIO_RMS = 200.0
TICK_INTERVAL_S = 0.05
MAX_TICK_GAP_S = 0.1

SPOKEN_SENTENCE = "Dobrý den, povídejte mi prosím o svém dětství."

MODELS = [
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
    "eleven_multilingual_v2",
    "eleven_v3_conversational",
]


def _root_mean_square(audio: bytes) -> float:
    """Loudness of signed 16-bit little-endian mono PCM, 0 for digital silence."""
    samples = array("h")
    samples.frombytes(audio)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return sqrt(sum(sample * sample for sample in samples) / len(samples))


async def _tick_until(stopping: asyncio.Event) -> float:
    """Tick every TICK_INTERVAL_S and return the longest interval actually observed."""
    longest_gap = 0.0
    previous = monotonic()
    while not stopping.is_set():
        await asyncio.sleep(TICK_INTERVAL_S)
        now = monotonic()
        longest_gap = max(longest_gap, now - previous)
        previous = now
    return longest_gap


class TestElevenLabsTts(unittest.IsolatedAsyncioTestCase):
    async def test_models_stream_pcm_without_blocking(self) -> None:
        api_key = getenv("ELEVENLABS_API_KEY")
        if not api_key:
            self.fail("ELEVENLABS_API_KEY not set, add it to .env to run this test")

        for model in MODELS:
            with self.subTest(model=model):
                audio, longest_gap = await self._synthesize(api_key, model)
                self.assertEqual(len(audio) % PCM_BYTES_PER_SAMPLE, 0,
                                 f"{model} returned a truncated 16-bit sample")

                duration = len(audio) / (PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE)
                loudness = _root_mean_square(audio)
                logger.info("%s: %d bytes, %.2f s, RMS %.0f, longest tick gap %.3f s.",
                            model, len(audio), duration, loudness, longest_gap)

                self.assertGreaterEqual(duration, MIN_AUDIO_SECONDS,
                                        f"{model} spoke the sentence in {duration:.2f} s")
                self.assertLessEqual(duration, MAX_AUDIO_SECONDS,
                                     f"{model} took {duration:.2f} s for the sentence")
                self.assertGreater(loudness, MIN_AUDIO_RMS,
                                   f"{model} returned {duration:.2f} s of near-silence")
                self.assertLess(longest_gap, MAX_TICK_GAP_S,
                                f"{model} starved the event loop for {longest_gap:.3f} s")

    async def _synthesize(self, api_key: str, model: str) -> tuple[bytes, float]:
        provider = ElevenLabsTtsProvider(ElevenLabsTtsConfig(
            api_key=api_key,
            model=model,
            output_format=f"pcm_{PCM_SAMPLE_RATE}",
        ))
        stopping = asyncio.Event()
        ticker = asyncio.create_task(_tick_until(stopping))
        audio = bytearray()
        try:
            async for chunk in provider.synthesize(SPOKEN_SENTENCE, language="cs"):
                audio.extend(chunk)
        finally:
            stopping.set()
            longest_gap = await ticker
        return bytes(audio), longest_gap
