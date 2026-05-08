import asyncio
from logging import getLogger
from typing import List

from universal_realtime_audio.stt_provider import TranscriptEvent

logger = getLogger(__name__)


async def transcript_ingest_task(
        app_running: asyncio.Event,
        transcript_queue: asyncio.Queue[TranscriptEvent | None],
) -> List[str]:
    result: List[str] = []
    try:
        while app_running.is_set():
            item = await transcript_queue.get()
            if item is None:
                logger.info("[INGEST] Received stop signal.")
                break
            if item.is_final:
                text = item.text.strip()
                if text:
                    logger.debug("[INGEST] Received: %s", text[:100])
                    result.append(text)
    except asyncio.CancelledError:
        logger.info("Cancelled.")
        raise
    except Exception as e:
        logger.exception("Crashed: %r", e)
    return result
