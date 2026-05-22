from __future__ import annotations

from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator

from universal_realtime_stt_tts.config import AUDIO_SAMPLE_RATE

logger = getLogger(__name__)


@dataclass(frozen=True)
class ElevenLabsTtsConfig:
    api_key: str
    voice_id: str = "MpbYQvoTmXjHkaxtLiSh"
    model: str = "eleven_turbo_v2_5"
    stability: float = 0.4
    speed: float = 0.9
    output_format: str = f"pcm_{AUDIO_SAMPLE_RATE}"
    base_url: str | None = None


class ElevenLabsTtsProvider:
    def __init__(self, config: ElevenLabsTtsConfig) -> None:
        self._config = config

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        from elevenlabs import ElevenLabs, VoiceSettings

        client = ElevenLabs(api_key=self._config.api_key, base_url=self._config.base_url)
        audio_stream = client.text_to_speech.convert(
            text=text,
            voice_id=self._config.voice_id,
            model_id=self._config.model,
            output_format=self._config.output_format,
            language_code=language,
            voice_settings=VoiceSettings(
                stability=self._config.stability,
                speed=self._config.speed,
            ),
        )
        for chunk in audio_stream:
            if chunk:
                yield chunk
