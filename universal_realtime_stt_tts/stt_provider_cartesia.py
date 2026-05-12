from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from websockets import connect, ConnectionClosed

from universal_realtime_stt_tts.config import AUDIO_SAMPLE_RATE, AUDIO_ENCODING, STT_LANGUAGE_ISO_639_1, STT_VAD_SILENCE_THRESHOLD_S
from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class CartesiaSttConfig:
    api_key: str
    model: str = "ink-whisper"
    base_url: str = "wss://api.cartesia.ai/stt/websocket"
    language: str = STT_LANGUAGE_ISO_639_1
    encoding: str = AUDIO_ENCODING
    sample_rate: int = AUDIO_SAMPLE_RATE
    min_volume: float = 0.15
    max_silence_duration_secs: float = STT_VAD_SILENCE_THRESHOLD_S


class CartesiaInkProvider(RealtimeSttProvider):
    """
    Cartesia streaming STT over WebSocket (Ink-Whisper).

    Protocol (docs):
      - Connect to wss://api.cartesia.ai/stt/websocket with query params
      - Send binary websocket messages containing raw audio
      - Send text command 'done' to flush and close
      - Receive JSON messages: type=transcript/flush_done/done/error
    """
    def __init__(self, cfg: CartesiaSttConfig) -> None:
        self._cfg = cfg
        self._ws = None
        self._eq = SttEventQueue(logger)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def __aenter__(self) -> "CartesiaInkProvider":
        qs = urlencode({
            "model": self._cfg.model,
            "language": self._cfg.language,
            "encoding": self._cfg.encoding,
            "sample_rate": str(self._cfg.sample_rate),
            "min_volume": str(self._cfg.min_volume),
            "max_silence_duration_secs": str(self._cfg.max_silence_duration_secs),
        })
        url = f"{self._cfg.base_url}?{qs}"

        # Auth: Cartesia requires X-API-Key and Cartesia-Version headers.
        self._ws = await connect(
            url,
            additional_headers={
                "X-API-Key": self._cfg.api_key,
                "Cartesia-Version": "2025-04-16",
            },
            ping_interval=10,
            ping_timeout=10,
            close_timeout=5,
            max_queue=1024,
        )

        self._rx_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            self._closed.set()
            if self._rx_task:
                self._rx_task.cancel()
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
        finally:
            self._eq.put_sentinel()
            self._ws = None
            self._rx_task = None

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if self._eq.error:
            raise self._eq.error
        if self._closed.is_set() or self._ws is None:
            logger.warning("[STT] Cartesia: cannot send audio, connection closed")
            return
        try:
            await self._ws.send(pcm_chunk)
        except ConnectionClosed:
            logger.warning("[STT] Cartesia: connection closed while sending audio")
            self._closed.set()

    async def end_audio(self) -> None:
        if self._ws is None or self._closed.is_set():
            return
        try:
            await self._ws.send("done")
        except Exception:
            pass

    def events(self) -> AsyncIterator[TranscriptEvent]:
        return self._eq.events()

    async def _recv_loop(self) -> None:
        async with self._eq.recv_guard("Cartesia", self._closed):
            while not self._closed.is_set():
                msg = await self._ws.recv()
                logger.debug("[STT] Cartesia: received message: %r", msg)

                if isinstance(msg, bytes):
                    logger.warning("[STT] Cartesia: received unexpected message: %r", msg)
                    continue

                data = json.loads(msg)
                typ = data.get("type")

                if typ == "transcript":
                    text = (data.get("text") or "").strip()
                    if bool(data.get("is_final", False)) and text:
                        await self._eq.put(TranscriptEvent(text=text, is_final=True))
                    continue

                if typ == "flush_done":
                    logger.info("[STT] Cartesia: received flush_done")
                    continue

                if typ == "done":
                    logger.info("[STT] Cartesia: received done")
                    break

                if typ == "error":
                    error_msg = data.get("message", str(data))
                    error_code = int(data.get("code", 0))
                    raise RuntimeError(f"Cartesia STT error (code {error_code}): {error_msg}")
