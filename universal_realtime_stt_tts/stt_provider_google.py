from __future__ import annotations

import asyncio
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional

from universal_realtime_stt_tts.config import AUDIO_SAMPLE_RATE, STT_INPUT_VOLUME_THRESHOLD, STT_LANGUAGE_BCP_47
from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class GoogleSttConfig:
    """
    Configuration for Google Cloud Speech-to-Text realtime provider.

    Google authenticates with Application Default Credentials (ADC), not an
    API key: point GOOGLE_APPLICATION_CREDENTIALS at a service-account JSON.
    """

    interim_results: bool = True
    language: str = STT_LANGUAGE_BCP_47
    sample_rate: int = AUDIO_SAMPLE_RATE
    # Not mapped for Google yet; whether it has an equivalent is untested.
    input_volume_threshold: float | None = STT_INPUT_VOLUME_THRESHOLD


class GoogleRealtimeProvider:
    """
    Google Cloud Speech-to-Text v1 streaming adapter.

    Runs the blocking ``SpeechClient.streaming_recognize`` in a worker thread,
    fed from an asyncio queue. Requires the ``google`` extra, imported lazily
    so this module loads without it.
    """

    def __init__(self, cfg: Optional[GoogleSttConfig] = None) -> None:
        self._cfg = cfg or GoogleSttConfig()
        if self._cfg.input_volume_threshold is not None:
            logger.debug("[STT] Google: input_volume_threshold is not mapped for this provider yet; ignored.")
        self._audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=400)
        self._eq = SttEventQueue(logger)
        self._closed = asyncio.Event()
        self._thread_task: Optional[asyncio.Task] = None

    async def __aenter__(self) -> "GoogleRealtimeProvider":
        loop = asyncio.get_running_loop()
        self._thread_task = asyncio.create_task(asyncio.to_thread(self._blocking_stream_loop, loop))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self.end_audio()
            if self._thread_task:
                try:
                    await asyncio.wait_for(self._thread_task, timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning("[STT] Google: streaming thread did not finish within 30s, forcing close")
                    self._thread_task.cancel()
        finally:
            self._eq.put_sentinel()
            self._thread_task = None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if self._eq.error:
            raise self._eq.error
        if self._closed.is_set():
            logger.warning("[STT] Google: cannot send audio, connection closed")
            return
        await self._audio_q.put(pcm_chunk)

    async def end_audio(self) -> None:
        if not self._closed.is_set():
            await self._audio_q.put(None)

    def events(self) -> AsyncIterator[TranscriptEvent]:
        return self._eq.events()

    def _blocking_stream_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        # The whole body runs under one try/finally so any failure (including
        # SpeechClient construction errors like missing GOOGLE_APPLICATION_CREDENTIALS,
        # or a missing google extra) reaches the receiver immediately. Otherwise an
        # early raise would skip the sentinel and the receiver would block until
        # __aexit__ tore the stream down.
        failure: Exception | None = None
        try:
            from google.cloud import speech

            client = speech.SpeechClient()
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,  # type: ignore[arg-type]
                sample_rate_hertz=self._cfg.sample_rate,  # type: ignore[arg-type]
                language_code=self._cfg.language,  # type: ignore[arg-type]
            )
            streaming_config = speech.StreamingRecognitionConfig(
                config=config,  # type: ignore[arg-type]
                interim_results=self._cfg.interim_results,  # type: ignore[arg-type]
            )

            def request_iter():
                while True:
                    chunk = asyncio.run_coroutine_threadsafe(self._audio_q.get(), loop).result()
                    if chunk is None:
                        break
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)  # type: ignore[arg-type]

            responses = client.streaming_recognize(streaming_config, request_iter())  # type: ignore[arg-type]
            for response_index, response in enumerate(responses):
                # The first response (even an empty one) proves the gRPC stream is live.
                # Logged at INFO so an apparent hang can be told apart from a never-connected stream.
                if response_index == 0:
                    logger.info("[STT] Google: first response received, stream is live.")
                self._publish_results(response, loop)

        except Exception as error:
            logger.exception("[STT] Google streaming crashed: %r", error)
            failure = error
        finally:
            # asyncio.Event is not thread-safe. Scheduled before the sentinel, so a
            # consumer that sees the stream end also sees send_audio refuse audio.
            loop.call_soon_threadsafe(self._closed.set)
            self._eq.finish_from_thread(loop, failure)

    def _publish_results(self, response, loop: asyncio.AbstractEventLoop) -> None:
        for result in response.results:
            if not result.alternatives:
                continue
            text = result.alternatives[0].transcript.strip()
            if not text:
                continue
            if result.is_final:
                logger.debug("[STT] Google: final transcript received.")
            asyncio.run_coroutine_threadsafe(
                self._eq.put(TranscriptEvent(text=text, is_final=result.is_final)),
                loop,
            ).result()
