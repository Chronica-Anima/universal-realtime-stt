from __future__ import annotations

from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator

from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class SpeechmaticsSttConfig:
    api_key: str
    base_url: str = "wss://eu.rt.speechmatics.com/v2/"
    language: str = "cs"
    operating_point: str = "enhanced"
    max_delay_s: float = 1.0
    sample_rate: int = 16000
    diarization: str = "speaker"  # "speaker" | "channel" | "none"
    speaker_diarization_config: dict | None = None
    end_of_utterance_silence_trigger: float = 1.0


class SpeechmaticsSttProvider:
    def __init__(self, cfg: SpeechmaticsSttConfig) -> None:
        self._cfg = cfg
        self._eq = SttEventQueue(logger)
        self._client = None
        self._utterance_buf: list[str] = []
        self._utterance_speaker: str | None = None

    async def __aenter__(self) -> "SpeechmaticsSttProvider":
        from speechmatics.rt import (
            AsyncClient, ServerMessageType, TranscriptionConfig,
            AudioFormat, AudioEncoding, ConversationConfig,
            SpeakerDiarizationConfig, StaticKeyAuth, OperatingPoint,
        )

        self._client = AsyncClient(auth=StaticKeyAuth(api_key=self._cfg.api_key))
        await self._client.__aenter__()

        @self._client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
        def on_partial(msg):
            text = msg.get("metadata", {}).get("transcript", "").strip()
            if text:
                combined = " ".join(self._utterance_buf + [text])
                speaker = self._extract_speaker(msg) or self._utterance_speaker
                self._eq.put_nowait(TranscriptEvent(text=combined, is_final=False, speaker=speaker))

        @self._client.on(ServerMessageType.ADD_TRANSCRIPT)
        def on_final(msg):
            text = msg.get("metadata", {}).get("transcript", "").strip()
            if text:
                self._utterance_buf.append(text)
                speaker = self._extract_speaker(msg)
                if speaker:
                    self._utterance_speaker = speaker

        @self._client.on(ServerMessageType.END_OF_UTTERANCE)
        def on_utterance_end(msg):
            self._flush_utterance()

        @self._client.on(ServerMessageType.END_OF_TRANSCRIPT)
        def on_end(msg):
            self._flush_utterance()
            self._eq.put_sentinel()

        diarization_cfg = None
        if self._cfg.diarization != "none" and self._cfg.speaker_diarization_config:
            diarization_cfg = SpeakerDiarizationConfig(
                **self._cfg.speaker_diarization_config,
            )

        await self._client.start_session(
            transcription_config=TranscriptionConfig(
                language=self._cfg.language,
                enable_partials=True,
                max_delay=self._cfg.max_delay_s,
                operating_point=OperatingPoint(self._cfg.operating_point),
                enable_entities=True,
                diarization=self._cfg.diarization if self._cfg.diarization != "none" else None,
                speaker_diarization_config=diarization_cfg,
                conversation_config=ConversationConfig(
                    end_of_utterance_silence_trigger=self._cfg.end_of_utterance_silence_trigger,
                ),
            ),
            audio_format=AudioFormat(
                encoding=AudioEncoding.PCM_S16LE,
                sample_rate=self._cfg.sample_rate,
            ),
        )

        logger.info("[STT] Speechmatics: SDK session started.")
        return self

    def _flush_utterance(self) -> None:
        if not self._utterance_buf:
            return
        text = " ".join(self._utterance_buf).strip()
        speaker = self._utterance_speaker
        self._utterance_buf.clear()
        self._utterance_speaker = None
        if text:
            self._eq.put_nowait(TranscriptEvent(text=text, is_final=True, speaker=speaker))

    @staticmethod
    def _extract_speaker(msg: dict) -> str | None:
        results = msg.get("results", [])
        if not results:
            return None
        speakers = [
            w.get("alternatives", [{}])[0].get("speaker")
            for w in results if w.get("alternatives")
        ]
        speakers = [s for s in speakers if s and s != "UU"]
        if not speakers:
            return None
        # Majority vote: most frequent speaker label
        return max(set(speakers), key=speakers.count)

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if self._eq.error:
            raise self._eq.error
        if self._client:
            await self._client.send_audio(bytes(pcm_chunk))

    async def end_audio(self) -> None:
        if self._client:
            await self._client.end_session()

    def events(self) -> AsyncIterator[TranscriptEvent]:
        return self._eq.events()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            try:
                await self._client.__aexit__(exc_type, exc, tb)
            except Exception:
                pass
        self._eq.put_sentinel()
