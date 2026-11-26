from __future__ import annotations

import asyncio
from logging import getLogger
from pathlib import Path
from typing import Awaitable, Callable

from helpers.diff_report import CustomMetricResult, DiffReport
from helpers.stream_wav import QueueFullError, stream_wav_file
from helpers.transcript_ingest import transcript_ingest_task
from universal_realtime_stt_tts.stt import stt_session_task
from universal_realtime_stt_tts.stt_provider import RealtimeSttProvider

logger = getLogger(__name__)


async def transcribe_and_diff(
        provider: RealtimeSttProvider,
        wav_path: Path,
        txt_path: Path,
        out_path: Path,
        *,
        chunk_ms: int,
        sample_rate: int,
        realtime_factor: float,
        silence_s: float,
        custom_metric_fn: Callable[[str, str], Awaitable[CustomMetricResult]] | None = None,
        provider_name: str | None = None,
) -> DiffReport:
    """
    Stream a WAV file through one STT session, diff the final transcript against the ground-truth
    text and write the HTML report to out_path.

    Error flow (provider drops connection early):
        1. stt_session_task raises the provider's exception and cancels its internal sender.
        2. A done-callback on stt_task clears `running`, which stops stream_wav_file on its next chunk.
           If the queue fills before that, stream_wav_file raises QueueFullError, swallowed here
           because the real error is in stt_task.
        3. stt_task is awaited and its exception re-raised, after a None sentinel unblocks ingest_task.

    custom_metric_fn: optional async (expected, got) -> CustomMetricResult embedded in the report.
    provider_name: label in the HTML report, defaults to the provider's class name. Pass one when the
        same class runs with different models.
    """
    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=40)
    transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    running = asyncio.Event()
    running.set()

    stt_task = asyncio.create_task(stt_session_task(provider, audio_queue, transcript_queue, running))
    ingest_task = asyncio.create_task(transcript_ingest_task(transcript_queue))
    stt_task.add_done_callback(
        lambda task: running.clear() if not task.cancelled() and task.exception() else None
    )

    try:
        try:
            await stream_wav_file(wav_path, audio_queue, chunk_ms, sample_rate,
                                  realtime_factor=realtime_factor, silence_s=silence_s, running=running)
        except QueueFullError:
            pass

        try:
            await stt_task
        except Exception:
            # The receiver raised without putting None in transcript_queue.
            await transcript_queue.put(None)
            await ingest_task
            raise

        transcript_raw = " ".join(await ingest_task)
    finally:
        # On cancellation (the benchmark's timeout), an orphaned session would keep streaming
        # and holding a billed provider connection while the retry opens another.
        child_tasks = [task for task in (stt_task, ingest_task) if not task.done()]
        for task in child_tasks:
            task.cancel()
        await asyncio.gather(*child_tasks, return_exceptions=True)
    running.clear()
    logger.info("Final transcript raw: %r", transcript_raw)

    expected_raw = txt_path.read_text(encoding="utf-8")

    custom_metric = None
    if custom_metric_fn:
        try:
            custom_metric = await custom_metric_fn(expected_raw, transcript_raw)
        except Exception as exc:
            logger.warning("Custom metric failed, skipping: %s", exc)

    provider_name = provider_name or provider.__class__.__name__
    report = DiffReport(expected_raw, transcript_raw, custom_metric=custom_metric)
    report.write_html(
        out_path,
        title=f"{wav_path.name}: {provider_name}",
        detail=f"Provider: {provider_name}\nSound: {wav_path.name}\nExpected: {txt_path.name}\nReport: {out_path.name}",
    )
    return report
