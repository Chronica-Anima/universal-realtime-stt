"""
Gemini Live STT Provider — real-time transcription via Gemini Live API.

Uses the google-genai SDK's bidirectional streaming Live API over WebSocket.
Audio is sent as raw 200ms PCM chunks; the server-side Automatic Activity
Detection (AAD) handles utterance segmentation — no client-side VAD is needed.

Transcription source
--------------------
Transcription comes from the native ``input_audio_transcription`` path, not
from the model's generated audio response. This is configured by setting
``input_audio_transcription: {}`` in the session setup. The server then sends
``LiveServerContent.input_transcription`` events (type ``Transcription``) with:

  - ``text``: transcribed text for this utterance chunk
  - ``finished``: True = final (committed), False = interim/partial

These map directly to TranscriptEvent(is_final=...). Input language is
auto-detected from the audio — there is no per-session language config for
input (``speech_config.language_code`` is for voice name/output only).

Response modality MUST be AUDIO
---------------------------------
``gemini-3.1-flash-live-preview`` only supports ``response_modalities=["AUDIO"]``.
Setting ``["TEXT"]`` causes WebSocket 1011 at connection time. The model
audio output is received and discarded — we only consume the
``input_transcription`` sidecar events for STT purposes.

Architecture
------------
Unlike the other WebSocket providers which manage the raw WebSocket protocol
themselves, this provider uses the google-genai SDK's Live API client.
The SDK handles the WebSocket framing and message serialisation, which is
non-trivial for the Gemini bidirectional protocol.

Lifecycle:
  1. __aenter__: opens a Live API session via AsyncExitStack
  2. send_audio(): forwards each PCM chunk via send_realtime_input()
  3. end_audio(): sends audio_stream_end, waits briefly for the server to
     flush the final transcription, then signals close
  4. events(): async iterator yielding TranscriptEvent per input_transcription
     event (is_final mirrors Transcription.finished)
  5. __aexit__: cancels receiver, closes session (idempotent via exit_stack)

Requires: google-genai>=1.0.0  (pip install google-genai)
API key:  GEMINI_API_KEY in .env
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from logging import getLogger
from typing import AsyncIterator, Optional

from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import TranscriptEvent

logger = getLogger(__name__)

_SYSTEM_INSTRUCTION = "You are a voice transcription assistant. Acknowledge input briefly."

# How long to wait in end_audio() after signalling turn_complete before
# closing the session. Gives the model time to return the final transcript.
_END_AUDIO_DRAIN_S = 1.0


@dataclass(frozen=True)
class GeminiLiveSttConfig:
    api_key: str
    model: str = "gemini-3.1-flash-live-preview"
    sample_rate: int = 16000

    # System instruction for the model's generated responses (not for transcription).
    # Transcription comes from the native input_audio_transcription path.
    # Input language is auto-detected from the audio; there is no per-session
    # language config for input in the Live API (speech_config.language_code
    # is for TTS output only).
    system_instruction: str = _SYSTEM_INSTRUCTION


class GeminiLiveProvider:
    """
    Real-time STT via the Gemini Live API (google-genai SDK).

    Protocol:
      - Opens a Live API session (bidirectional WebSocket, managed by SDK).
      - Sends 200ms raw PCM chunks as realtime audio input.
      - Server-side AAD handles utterance segmentation; no client VAD needed.
      - Reads server_content.input_transcription events for transcription:
          finished=False → TranscriptEvent(is_final=False)  (interim)
          finished=True  → TranscriptEvent(is_final=True)   (committed)
      - Model text responses (model_turn) are not used for transcription.
      - Audio output suppressed via response_modalities=["TEXT"].

    Usage is identical to every other provider — pass an instance to
    stt_session_task() via the RealtimeSttProvider protocol.

    Requires google-genai>=1.0.0:
        pip install google-genai
    """

    def __init__(self, cfg: GeminiLiveSttConfig) -> None:
        self._cfg = cfg
        self._session = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._eq = SttEventQueue(logger)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def __aenter__(self) -> "GeminiLiveProvider":
        try:
            from google import genai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "google-genai is required for GeminiLiveProvider. "
                "Install it with: pip install google-genai"
            ) from exc

        client = genai.Client(api_key=self._cfg.api_key)

        # IMPORTANT: response_modalities MUST be ["AUDIO"] — not ["TEXT"].
        # gemini-3.1-flash-live-preview only supports AUDIO output.
        # We don't use the model's audio output — input_audio_transcription gives us
        # a text sidecar of the user's speech independently of the audio response.
        live_config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self._cfg.system_instruction,
            "input_audio_transcription": {},
        }

        self._exit_stack = AsyncExitStack()
        self._session = await self._exit_stack.enter_async_context(
            client.aio.live.connect(model=self._cfg.model, config=live_config)
        )
        logger.info("[STT] GeminiLive: session established (model=%s).", self._cfg.model)

        self._rx_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._closed.set()

        if self._rx_task:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass

        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass

        self._eq.put_sentinel()

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if self._closed.is_set() or self._session is None:
            return
        if self._eq.error:
            raise self._eq.error
        try:
            from google.genai import types  # noqa: PLC0415
            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=pcm_chunk,
                    mime_type=f"audio/pcm;rate={self._cfg.sample_rate}",
                )
            )
        except Exception as e:
            if not self._closed.is_set():
                logger.warning("[STT] GeminiLive: send_audio error: %s", e)
            self._closed.set()

    async def end_audio(self) -> None:
        if self._session is None or self._closed.is_set():
            return

        try:
            await self._session.send_realtime_input(audio_stream_end=True)
        except Exception as e:
            logger.debug("[STT] GeminiLive: audio_stream_end signal: %s", e)

        await asyncio.sleep(_END_AUDIO_DRAIN_S)
        self._closed.set()

    def events(self) -> AsyncIterator[TranscriptEvent]:
        return self._eq.events()

    async def _recv_loop(self) -> None:
        """
        Gemini Live quirk — pending interim promotion:
        When audio_stream_end is sent, the server returns the final transcript
        with finished=False and then closes (1000 OK) without sending finished=True.
        We hold the most recent interim and promote it to final if the session
        closes cleanly without a committed version.
        """
        _pending_interim: str | None = None

        async def _promote_pending() -> None:
            if _pending_interim and not self._eq.error:
                logger.debug("[STT] GeminiLive: promoting pending interim to final: %s", _pending_interim[:60])
                await self._eq.put(TranscriptEvent(text=_pending_interim, is_final=True))

        async with self._eq.recv_guard("GeminiLive", self._closed, on_close=_promote_pending):
            async for response in self._session.receive():
                if self._closed.is_set():
                    break

                server_content = response.server_content
                if server_content is None:
                    continue

                t = server_content.input_transcription
                if t and t.text:
                    text = t.text.strip()
                    if text:
                        is_final = bool(t.finished)
                        logger.debug("[STT] GeminiLive: transcript (final=%s): %s", is_final, text[:60])
                        if is_final:
                            _pending_interim = None
                            await self._eq.put(TranscriptEvent(text=text, is_final=True))
                        else:
                            _pending_interim = text
