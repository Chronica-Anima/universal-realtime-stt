from __future__ import annotations

import asyncio
from dataclasses import dataclass
from logging import getLogger
from typing import TYPE_CHECKING, Any, AsyncIterator

from universal_realtime_stt_tts.config import AUDIO_SAMPLE_RATE

if TYPE_CHECKING:
    from elevenlabs import AsyncElevenLabs

logger = getLogger(__name__)


# The reference page marks optimize_streaming_latency deprecated, but the API still
# honours it: on eleven_multilingual_v2 level 3 moves first byte from 3.76 s to 0.876 s
# on a 313-character reply. It is sent to the multilingual family only, because
# eleven_v3_conversational answers it with HTTP 400 unsupported_model, and on the fast
# models the gain does not pay for a deprecated parameter (turbo 0.390 s to 0.296 s,
# flash 0.192 s to 0.183 s). The prefix also covers eleven_multilingual_v1 and
# eleven_multilingual_sts_v2, neither of which was measured. Both constants go away
# together when the vendor withdraws the parameter.
MULTILINGUAL_MODEL_PREFIX = "eleven_multilingual"
MULTILINGUAL_STREAMING_LATENCY = 3

_shared_clients: dict[tuple[str, str | None, Any], "AsyncElevenLabs"] = {}


def _shared_async_client(api_key: str, base_url: str | None) -> "AsyncElevenLabs":
    """Cached at module level rather than per provider because callers build a provider
    per utterance, and keyed on the running loop as well as the credential because an
    httpx pool belongs to the loop that opened it and must not outlive an asyncio.run()."""
    from elevenlabs import AsyncElevenLabs

    cache_key = (api_key, base_url, asyncio.get_running_loop())
    # Without this, keying on the loop would leak one client per asyncio.run().
    for closed_key in [key for key in _shared_clients if key[2].is_closed()]:
        del _shared_clients[closed_key]
    if cache_key not in _shared_clients:
        _shared_clients[cache_key] = AsyncElevenLabs(api_key=api_key, base_url=base_url)
    return _shared_clients[cache_key]


@dataclass(frozen=True)
class ElevenLabsTtsConfig:
    api_key: str
    voice_id: str = "MpbYQvoTmXjHkaxtLiSh"
    model: str = "eleven_turbo_v2_5"
    stability: float = 0.4
    speed: float = 0.9
    similarity_boost: float = 0.75
    output_format: str = f"pcm_{AUDIO_SAMPLE_RATE}"
    base_url: str | None = None


class ElevenLabsTtsProvider:
    def __init__(self, config: ElevenLabsTtsConfig) -> None:
        self._config = config

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        from elevenlabs import VoiceSettings

        latency_argument = (
            {"optimize_streaming_latency": MULTILINGUAL_STREAMING_LATENCY}
            if self._config.model.startswith(MULTILINGUAL_MODEL_PREFIX)
            else {}
        )
        client = _shared_async_client(self._config.api_key, self._config.base_url)
        audio_stream = client.text_to_speech.stream(
            text=text,
            voice_id=self._config.voice_id,
            model_id=self._config.model,
            output_format=self._config.output_format,
            language_code=language,
            voice_settings=VoiceSettings(
                stability=self._config.stability,
                speed=self._config.speed,
                similarity_boost=self._config.similarity_boost,
            ),
            **latency_argument,
        )
        async for chunk in audio_stream:
            if chunk:
                yield chunk
