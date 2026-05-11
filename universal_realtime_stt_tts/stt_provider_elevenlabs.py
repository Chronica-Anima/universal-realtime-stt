from __future__ import annotations

import base64
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator

from config import (
    AUDIO_SAMPLE_RATE,
    STT_LANGUAGE_ISO_639_1,
    STT_MIN_SILENCE_DURATION_MS,
    STT_MIN_SPEECH_DURATION_MS,
    STT_VAD_SILENCE_THRESHOLD_S,
    STT_VAD_THRESHOLD,
)
from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class ElevenLabsSttConfig:
    api_key: str
    model: str = "scribe_v2_realtime"
    language: str = STT_LANGUAGE_ISO_639_1
    sample_rate: int = AUDIO_SAMPLE_RATE
    vad_silence_threshold_s: float = STT_VAD_SILENCE_THRESHOLD_S
    vad_threshold: float = STT_VAD_THRESHOLD
    min_silence_duration_ms: int = STT_MIN_SILENCE_DURATION_MS
    min_speech_duration_ms: int = STT_MIN_SPEECH_DURATION_MS


class ElevenLabsSttProvider:
    def __init__(self, cfg: ElevenLabsSttConfig) -> None:
        self._cfg = cfg
        self._eq = SttEventQueue(logger)
        self._client = None
        self._connection = None
        self._closed = False

    async def __aenter__(self) -> "ElevenLabsSttProvider":
        from elevenlabs import (
            ElevenLabs, RealtimeAudioOptions, AudioFormat,
            CommitStrategy, RealtimeEvents,
        )

        self._client = ElevenLabs(api_key=self._cfg.api_key)
        self._connection = await self._client.speech_to_text.realtime.connect(
            RealtimeAudioOptions(
                model_id=self._cfg.model,
                audio_format=AudioFormat.PCM_16000,
                sample_rate=self._cfg.sample_rate,
                commit_strategy=CommitStrategy.VAD,
                language_code=self._cfg.language,
                vad_silence_threshold_secs=self._cfg.vad_silence_threshold_s,
                vad_threshold=self._cfg.vad_threshold,
                min_silence_duration_ms=self._cfg.min_silence_duration_ms,
                min_speech_duration_ms=self._cfg.min_speech_duration_ms,
            )
        )

        self._connection.on(RealtimeEvents.PARTIAL_TRANSCRIPT, lambda d: self._on_transcript(d, False))
        self._connection.on(RealtimeEvents.COMMITTED_TRANSCRIPT, lambda d: self._on_transcript(d, True))
        self._connection.on(RealtimeEvents.ERROR, self._on_error)
        self._connection.on(RealtimeEvents.CLOSE, self._on_close)

        logger.info("[STT] ElevenLabs: SDK session started.")
        return self

    def _on_transcript(self, data, is_final: bool) -> None:
        text = data.get("text", "").strip()
        if text:
            self._eq.put_nowait(TranscriptEvent(text=text, is_final=is_final))

    def _on_error(self, err) -> None:
        logger.error("[STT] ElevenLabs: %s", err)
        self._eq.set_error(RuntimeError(f"ElevenLabs STT error: {err}"))

    def _on_close(self) -> None:
        self._eq.put_sentinel()

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if self._eq.error:
            raise self._eq.error
        if not self._connection or self._closed:
            return
        await self._connection.send({
            "audio_base_64": base64.b64encode(pcm_chunk).decode(),
            "sample_rate": self._cfg.sample_rate,
        })

    async def end_audio(self) -> None:
        if self._connection and not self._closed:
            self._closed = True
            await self._connection.close()

    def events(self) -> AsyncIterator[TranscriptEvent]:
        return self._eq.events()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._connection and not self._closed:
            self._closed = True
            try:
                await self._connection.close()
            except Exception:
                pass
        self._eq.put_sentinel()
