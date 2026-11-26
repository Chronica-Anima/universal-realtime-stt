from __future__ import annotations

import asyncio
import math
import wave
from itertools import chain, repeat
from logging import getLogger
from pathlib import Path

logger = getLogger(__name__)

_PCM16_SAMPLE_WIDTH_BYTES = 2


class QueueFullError(Exception):
    """Raised when audio queue is full and consumer is not consuming."""


async def _put_with_timeout(queue: asyncio.Queue, item: bytes | None, timeout: float = 5.0) -> None:
    """Put item to queue with timeout. Raises QueueFullError if queue stays full."""
    try:
        await asyncio.wait_for(queue.put(item), timeout=timeout)
    except asyncio.TimeoutError:
        raise QueueFullError(
            f"Audio queue full for {timeout}s - consumer not reading. "
            f"Queue size: {queue.qsize()}/{queue.maxsize}"
        ) from None  # the timeout itself carries no information beyond this message


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


async def stream_wav_file(file: Path, audio_queue: asyncio.Queue, chunk_ms: int, expected_sample_rate: int, *,
                          realtime_factor: float, silence_s: float, running: asyncio.Event | None = None) -> None:
    """
    Stream a mono PCM16 WAV file to an async queue with real-time pacing, then push a None sentinel.

    Silence is streamed before and after the audio: without the leading silence providers often cut off
    the first word, and the trailing silence lets the VAD commit the last utterance.

    Args:
        realtime_factor: 1.0 = real-time, 0.5 = 2x faster, 0.0 = no delay between chunks.
        running: when cleared, streaming stops early (the STT session has already failed).

    Raises:
        ValueError: the WAV is not uncompressed mono PCM16 at expected_sample_rate.
        QueueFullError: the consumer stopped reading the audio queue.
    """
    frames_per_chunk = expected_sample_rate * chunk_ms // 1000
    if frames_per_chunk <= 0:
        raise ValueError(f"chunk_ms={chunk_ms} is too small")

    with wave.open(str(file), "rb") as wav:
        actual_format = (wav.getcomptype(), wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
        expected_format = ("NONE", 1, _PCM16_SAMPLE_WIDTH_BYTES, expected_sample_rate)
        if actual_format != expected_format:
            raise ValueError(
                f"{file.name}: (comptype, channels, sample width, rate) is {actual_format}, expected {expected_format}"
            )

        silence_chunk = b"\x00" * _PCM16_SAMPLE_WIDTH_BYTES * frames_per_chunk
        silence_chunk_count = math.ceil(silence_s * 1000 / chunk_ms)
        audio_chunks = iter(lambda: wav.readframes(frames_per_chunk), b"")
        chunk_delay_s = chunk_ms / 1000 * realtime_factor

        logger.debug("[WAV] streaming %s, %d frames per chunk", file, frames_per_chunk)
        for chunk in chain(repeat(silence_chunk, silence_chunk_count), audio_chunks,
                           repeat(silence_chunk, silence_chunk_count)):
            if running is not None and not running.is_set():
                break
            await _put_with_timeout(audio_queue, chunk)
            await asyncio.sleep(chunk_delay_s)

    logger.info("[WAV] streaming done, pushing None to the audio queue.")
    await _put_with_timeout(audio_queue, None)
