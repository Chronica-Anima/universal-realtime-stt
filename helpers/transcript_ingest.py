import asyncio
from logging import getLogger

from universal_realtime_stt_tts.stt_provider import TranscriptEvent

logger = getLogger(__name__)


async def transcript_ingest_task(transcript_queue: asyncio.Queue[TranscriptEvent | None]) -> list[str]:
    """
    Collect the stripped, non-empty text of final TranscriptEvents until the None sentinel.

    Partial (non-final) events are ignored. Exceptions propagate, so a failed run is never
    scored as a complete transcript.
    """
    segments: list[str] = []
    while (event := await transcript_queue.get()) is not None:
        text = event.text.strip()
        if event.is_final and text:
            logger.debug("[INGEST] Received: %s", text[:100])
            segments.append(text)
    logger.info("[INGEST] Received stop signal.")
    return segments
