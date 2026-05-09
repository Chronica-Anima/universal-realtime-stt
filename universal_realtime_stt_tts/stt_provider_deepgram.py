from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from websockets import connect, ConnectionClosed

from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import RealtimeSttProvider, TranscriptEvent

logger = getLogger(__name__)


@dataclass(frozen=True)
class DeepgramSttConfig:
    api_key: str
    base_url: str = "wss://api.deepgram.com/v1/listen"
    model: str = "nova-3"
    language: str = "cs"
    punctuate: bool = True
    smart_format: bool = True
    interim_results: bool = True
    encoding: str = "linear16"
    sample_rate: int = 16000
    channels: int = 1
    endpointing_ms: int = 700


class DeepgramRealtimeProvider(RealtimeSttProvider):
    def __init__(self, cfg: DeepgramSttConfig) -> None:
        self._cfg = cfg
        self._ws = None
        self._eq = SttEventQueue(logger)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def __aenter__(self) -> "DeepgramRealtimeProvider":
        if not self._cfg.api_key:
            raise ValueError("Deepgram API key is required")

        qs = urlencode(
            {
                "model": self._cfg.model,
                "language": self._cfg.language,
                "encoding": self._cfg.encoding,
                "sample_rate": str(self._cfg.sample_rate),
                "channels": str(self._cfg.channels),
                "punctuate": str(self._cfg.punctuate).lower(),
                "smart_format": str(self._cfg.smart_format).lower(),
                "interim_results": str(self._cfg.interim_results).lower(),
                "endpointing": str(self._cfg.endpointing_ms),
            }
        )
        url = f"{self._cfg.base_url}?{qs}"
        logger.debug("[STT] Deepgram: connecting to %s", url)

        try:
            self._ws = await asyncio.wait_for(
                connect(
                    url,
                    additional_headers={"Authorization": f"token {self._cfg.api_key}"},
                    open_timeout=10,
                    ping_interval=10,
                    ping_timeout=10,
                    close_timeout=5,
                    max_queue=32,
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Deepgram WebSocket connection timed out after 15s")

        logger.info("[STT] Deepgram: WebSocket connected.")
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
            logger.warning("[STT] Deepgram: cannot send audio, connection closed")
            return
        try:
            await self._ws.send(pcm_chunk)
        except ConnectionClosed:
            logger.warning("[STT] Deepgram: connection closed while sending audio")
            self._closed.set()

    async def end_audio(self) -> None:
        if self._ws is None or self._closed.is_set():
            return
        try:
            await self._ws.send(json.dumps({"type": "Finalize"}))
            await asyncio.sleep(0.25)
        except Exception:
            pass
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass

    def events(self) -> AsyncIterator[TranscriptEvent]:
        return self._eq.events()

    async def _recv_loop(self) -> None:
        logger.debug("[STT] Deepgram: _recv_loop started.")
        async with self._eq.recv_guard("Deepgram", self._closed):
            while not self._closed.is_set():
                msg = await self._ws.recv()

                if isinstance(msg, (bytes, bytearray)):
                    logger.warning("[STT] Deepgram: received unexpected binary message")
                    continue

                data = json.loads(msg)
                typ = data.get("type")

                if typ == "Results":
                    if not data.get("is_final", False):
                        continue
                    channel = data.get("channel") or {}
                    alts = channel.get("alternatives") or []
                    if not alts:
                        continue
                    text = (alts[0].get("transcript") or "").strip()
                    if text:
                        logger.debug("[STT] Deepgram: final transcript: %s", text[:50])
                        await self._eq.put(TranscriptEvent(text=text, is_final=True))
                    continue

                if typ in ("Metadata", "UtteranceEnd", "SpeechStarted"):
                    logger.debug("[STT] Deepgram: received %s", typ)
                    continue

                if typ == "Error" or "error" in data:
                    error_msg = data.get("message", str(data))
                    raise RuntimeError(f"Deepgram STT error: {error_msg}")
