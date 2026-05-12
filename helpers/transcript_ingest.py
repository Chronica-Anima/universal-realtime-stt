import asyncio
from logging import getLogger
from typing import List

from universal_realtime_stt_tts.stt_provider import TranscriptEvent

logger = getLogger(__name__)


async def transcript_ingest_task(
        app_running: asyncio.Event,
        transcript_queue: asyncio.Queue[TranscriptEvent | None],
) -> List[str]:
    """
    Collect committed transcript segments from the queue until end-of-stream.

    Reads items one at a time. Each non-None item is a committed transcript
    segment produced by the STT receiver. A None item signals that the
    provider has closed and no more segments will arrive.

    Args:
        app_running: Event flag; consumption continues while set. Clear it
            to request an early stop (e.g. on user cancellation).
        transcript_queue: The queue written to by the STT session. Yields
            str segments and a final None sentinel.

    Returns:
        List of transcript segments in the order they were received.
    """
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
