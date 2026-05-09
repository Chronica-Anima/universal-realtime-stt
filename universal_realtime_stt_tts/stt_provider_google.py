from __future__ import annotations

import asyncio
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional

from google.cloud import speech

from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class GoogleSttConfig:
    encoding: speech.RecognitionConfig.AudioEncoding = speech.RecognitionConfig.AudioEncoding.LINEAR16
    interim_results: bool = True
    language: str = "cs-CZ"
    sample_rate: int = 16000


class GoogleRealtimeProvider(RealtimeSttProvider):
    def __init__(self, cfg: Optional[GoogleSttConfig] = None) -> None:
        self._cfg = cfg or GoogleSttConfig()
        self._audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=400)
        self._eq = SttEventQueue(logger)
        self._closed = asyncio.Event()
        self._thread_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def __aenter__(self) -> "GoogleRealtimeProvider":
        self._loop = asyncio.get_running_loop()
        self._thread_task = asyncio.create_task(self._run_stream_in_thread())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self.end_audio()
            if self._thread_task:
                await self._thread_task
        finally:
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

    async def _run_stream_in_thread(self) -> None:
        await asyncio.to_thread(self._blocking_stream_loop)

    def _blocking_stream_loop(self) -> None:
        loop = self._loop
        if loop is None:
            raise RuntimeError("GoogleRealtimeProvider: event loop not set")

        client = speech.SpeechClient()

        config = speech.RecognitionConfig(
            encoding=self._cfg.encoding,  # type: ignore[arg-type]
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

        try:
            responses = client.streaming_recognize(streaming_config, request_iter())  # type: ignore[arg-type]

            for resp in responses:
                for result in getattr(resp, "results", ()):
                    if not result.alternatives:
                        continue
                    text = (result.alternatives[0].transcript or "").strip()
                    if not text:
                        continue
                    if bool(getattr(result, "is_final", False)):
                        logger.debug("[STT] Google: final transcript received.")
                        asyncio.run_coroutine_threadsafe(
                            self._eq.put(TranscriptEvent(text=text, is_final=True)),
                            loop,
                        ).result()

        except Exception as e:
            logger.exception("[STT] Google streaming crashed: %r", e)
            self._eq._error = e
        finally:
            # Thread-safe sentinel: schedule on the event loop since asyncio.Queue is not thread-safe
            asyncio.run_coroutine_threadsafe(self._eq.put_sentinel_async(), loop).result()
            self._closed.set()
