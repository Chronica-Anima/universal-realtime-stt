from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional

from helpers.diff_report import CustomMetricResult, DiffReport
from helpers.stream_wav import stream_wav_file, QueueFullError, logger
from helpers.transcript_ingest import transcript_ingest_task
from universal_realtime_stt_tts.stt import stt_session_task
from universal_realtime_stt_tts.stt_provider import RealtimeSttProvider


async def transcribe_wav_realtime(
        provider: RealtimeSttProvider,
        wav_path: Path,
        *,
        chunk_ms: int = 200,
        sample_rate: int = 16_000,
        realtime_factor: float = 1.0,
        silence_s: float = 2.0,
) -> str:
    """
    Transcribe a WAV file using the given STT provider.

    Sets up the full streaming pipeline (audio queue, transcript queue,
    sender/receiver tasks), streams audio with real-time pacing, collects
    committed transcripts, and returns the joined result.

    This is a single-session wrapper — it is not designed for session
    respawn. If the provider drops the connection mid-stream the exception
    propagates to the caller (see error handling below).

    Normal flow:
        1. stream_wav_file feeds PCM chunks into audio_queue, then pushes
           None to signal end-of-audio.
        2. stt_session_task's sender forwards chunks to the provider; on
           receiving None it calls end_audio().
        3. The provider commits its final results and closes; the receiver
           drains events, pushes None into transcript_queue, and exits.
        4. transcript_ingest_task collects all segments and returns them.

    Error flow (provider drops connection early):
        1. stt_session_task raises the provider's exception and cancels its
           internal sender.
        2. A done-callback on stt_task clears the `running` flag, which
           stops stream_wav_file's audio chunk loop on its next iteration.
           If the queue fills before that fires, stream_wav_file raises
           QueueFullError (swallowed here — the real error is in stt_task).
        3. After stream_wav_file exits, stt_task is awaited and its exception
           re-raised. Before re-raising, a None sentinel is placed in
           transcript_queue to unblock and cleanly shut down ingest_task.
        4. The provider exception propagates to the caller.

    Args:
        provider: An already-instantiated (but not yet entered) RealtimeSttProvider.
        wav_path: Path to the WAV file (must be PCM 16kHz mono 16-bit).
        chunk_ms: Audio chunk duration in milliseconds.
        sample_rate: Expected sample rate in Hz.
        realtime_factor: Playback speed (1.0 = real-time, 0.0 = no delay).
        silence_s: Silence padding (seconds) added before and after audio for VAD.

    Returns:
        The full transcript as a single string (segments joined by space).

    Raises:
        Any exception raised by stt_session_task (e.g. provider connection
        errors). QueueFullError from the audio stream is suppressed when
        it is a symptom of an STT failure.
    """
    input_audio_queue: asyncio.Queue = asyncio.Queue(maxsize=40)
    output_transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    running = asyncio.Event()
    running.set()

    stt_task = asyncio.create_task(stt_session_task(provider, input_audio_queue, output_transcript_queue, running))
    ingest_task = asyncio.create_task(transcript_ingest_task(running, output_transcript_queue))

    # When STT exits early due to a provider error, clear `running` so the wav
    # chunk loop stops on its next iteration instead of filling the queue.
    stt_task.add_done_callback(
        lambda t: running.clear() if not t.cancelled() and t.exception() else None
    )

    try:
        await stream_wav_file(
            wav_path,
            input_audio_queue,
            chunk_ms,
            sample_rate,
            realtime_factor=realtime_factor,
            silence=silence_s,
            running=running,
        )
    except QueueFullError:
        pass  # STT exited early and cancelled its sender; queue backed up. Real error is in stt_task.

    # Wait for STT to finish; propagate any provider error.
    try:
        await stt_task
    except Exception:
        # _receiver raised without putting None in transcript_queue — signal ingest to stop.
        await output_transcript_queue.put(None)
        await ingest_task
        raise

    # Similarly wait for the ingest loop to exit and collect drained transcripts.
    segments = await ingest_task
    running.clear()

    return " ".join(segments)


async def transcribe_and_diff(
        provider: RealtimeSttProvider,
        wav_path: Path,
        txt_path: Path,
        out_path: Path,
        *,
        chunk_ms: int = 200,
        sample_rate: int = 16_000,
        realtime_factor: float = 1.0,
        silence_s: float = 2.0,
        custom_metric_fn: Optional[Callable[[str, str], Awaitable[CustomMetricResult]]] = None,
) -> DiffReport:
    """
    Transcribe a WAV file and compare against ground-truth text.

    Runs the full pipeline: stream audio to the provider, collect the
    transcript, read the expected text, generate an HTML diff report,
    and return the DiffReport with accuracy metrics.

    Args:
        provider: An already-instantiated (but not yet entered) RealtimeSttProvider.
        wav_path: Path to the WAV file (must be PCM 16kHz mono 16-bit).
        txt_path: Path to the ground-truth transcript text file.
        out_path: Path where the HTML diff report will be written.
        chunk_ms: Audio chunk duration in milliseconds.
        sample_rate: Expected sample rate in Hz.
        realtime_factor: Playback speed (1.0 = real-time, 0.0 = no delay).
        silence_s: Silence padding (seconds) added before and after audio for VAD.
        custom_metric_fn: Optional async callable (expected, got) -> CustomMetricResult.
            When supplied, the result is embedded in the DiffReport and shown in the
            HTML report and TSV export. See helpers/semantic_understanding.py for an example.

    Returns:
        DiffReport with accuracy metrics and paths.
    """
    provider_name = provider.__class__.__name__

    transcript_raw = await transcribe_wav_realtime(
        provider,
        wav_path,
        chunk_ms=chunk_ms,
        sample_rate=sample_rate,
        realtime_factor=realtime_factor,
        silence_s=silence_s,
    )
    logger.info("Final transcript raw: %r", transcript_raw)

    # read ground truth and compute diff
    expected_raw = txt_path.read_text(encoding="utf-8")

    custom_metric = None
    if custom_metric_fn:
        try:
            custom_metric = await custom_metric_fn(expected_raw, transcript_raw)
        except Exception as exc:
            logger.warning("Custom metric failed, skipping: %s", exc)

    # Build the repot and write it.
    report = DiffReport(expected_raw, transcript_raw, custom_metric=custom_metric)
    report.write_html(
        out_path,
        title=f"{wav_path.name}: {provider_name}",
        detail=f"Provider: {provider_name}\nSound: {wav_path.name}\nExpected: {txt_path.name}\nReport: {out_path.name}",
    )
    return report
