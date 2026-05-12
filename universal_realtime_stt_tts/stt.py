"""
STT Session — the core bridge between audio input and transcript output.

Communication is fully queue-based:

    audio_queue  (bytes | None)  →  provider  →  transcript_queue  (TranscriptEvent | None)

Internally two concurrent tasks handle the plumbing:

  _audio_sender   — pulls PCM chunks from audio_queue, forwards them to the
                    provider via send_audio(). A None chunk signals end-of-audio
                    and triggers provider.end_audio().

                    Silence keepalive is a backstop only: if no audio arrives
                    for several seconds, a small silence chunk is sent to keep
                    the provider session open (some providers close the stream
                    after an extended idle). The threshold is deliberately long
                    so normal pacing jitter does not interleave silence with
                    real audio — that interleaving corrupts provider VAD and
                    in particular caused Google's gRPC stream to stop responding.

  _event_receiver — iterates the provider's event stream. All transcript events
                    (partial and final) are pushed into transcript_queue as
                    TranscriptEvent objects. The consumer decides how to handle
                    each based on is_final. When the provider closes the stream
                    cleanly, a None sentinel is pushed to signal end-of-transcripts.
"""
import asyncio
from logging import getLogger

from universal_realtime_stt_tts.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)

_SILENCE_CHUNK = b"\x00\x00" * 1600  # 100ms silence at 16kHz mono 16-bit
_AUDIO_IDLE_KEEPALIVE_S = 5.0  # only fires after extended idle, not on normal jitter


async def _audio_sender(
        provider: RealtimeSttProvider,
        audio_queue: asyncio.Queue[bytes | None],
        conversation_running: asyncio.Event,
) -> None:
    try:
        while conversation_running.is_set():
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=_AUDIO_IDLE_KEEPALIVE_S)
            except asyncio.TimeoutError:
                logger.debug("[STT] No audio for %.1fs, sending silence keepalive.", _AUDIO_IDLE_KEEPALIVE_S)
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
