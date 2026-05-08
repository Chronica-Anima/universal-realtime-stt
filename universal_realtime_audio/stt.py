"""
STT Session — the core bridge between audio input and transcript output.

Communication is fully queue-based:

    audio_queue  (bytes | None)  →  provider  →  transcript_queue  (TranscriptEvent | None)

Internally two concurrent tasks handle the plumbing:

  _audio_sender   — pulls PCM chunks from audio_queue, forwards them to the
                    provider via send_audio(). Sends silence keepalive when no
                    audio arrives within 200ms. A None chunk signals end-of-audio
                    and triggers provider.end_audio().

  _event_receiver — iterates the provider's event stream. All transcript events
                    (partial and final) are pushed into transcript_queue as
                    TranscriptEvent objects. The consumer decides how to handle
                    each based on is_final. When the provider closes the stream
                    cleanly, a None sentinel is pushed to signal end-of-transcripts.
"""
import asyncio
from logging import getLogger

from universal_realtime_audio.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)

_SILENCE_CHUNK = b"\x00\x00" * 1600  # 100ms silence at 16kHz mono 16-bit
_AUDIO_TIMEOUT_S = 0.2


async def _audio_sender(
        provider: RealtimeSttProvider,
        audio_queue: asyncio.Queue[bytes | None],
        conversation_running: asyncio.Event,
) -> None:
    try:
        while conversation_running.is_set():
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=_AUDIO_TIMEOUT_S)
            except asyncio.TimeoutError:
                chunk = _SILENCE_CHUNK
            if chunk is None:
                break
            await provider.send_audio(chunk)
    finally:
        await provider.end_audio()


async def _event_receiver(
        provider: RealtimeSttProvider,
        transcript_queue: asyncio.Queue[TranscriptEvent | None],
        conversation_running: asyncio.Event,
) -> None:
    async for ev in provider.events():
        if not conversation_running.is_set():
            break
        if ev.text.strip():
            await transcript_queue.put(ev)
    await transcript_queue.put(None)


async def stt_session_task(
        provider: RealtimeSttProvider,
        audio_queue: asyncio.Queue[bytes | None],
        transcript_queue: asyncio.Queue[TranscriptEvent | None],
        conversation_running: asyncio.Event,
) -> None:
    async with provider:
        sender = asyncio.create_task(_audio_sender(provider, audio_queue, conversation_running))
        receiver = asyncio.create_task(_event_receiver(provider, transcript_queue, conversation_running))

        try:
            await receiver
        finally:
            if not sender.done():
                sender.cancel()
            try:
                await sender
            except (asyncio.CancelledError, Exception):
                pass
