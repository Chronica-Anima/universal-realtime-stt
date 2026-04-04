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
  3. end_audio(): sends audio_stream_end + turn_complete, waits briefly
     for the server to flush the final transcription, then closes the session
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

from config import AUDIO_SAMPLE_RATE
from lib.stt_provider import TranscriptEvent

logger = getLogger(__name__)

_SYSTEM_INSTRUCTION = "You are a voice transcription assistant. Acknowledge input briefly."

# How long to wait in end_audio() after signalling turn_complete before
# closing the session. Gives the model time to return the final transcript.
# The 2 s trailing silence that stream_wav.py already adds means AAD should
# have triggered before end_audio() is called, so 1 s is sufficient buffer.
_END_AUDIO_DRAIN_S = 1.0


@dataclass(frozen=True)
class GeminiLiveSttConfig:
    """
    Configuration for the Gemini Live real-time STT provider.

    Provider-specific settings have defaults appropriate for Gemini Live.
    Universal STT settings (sample_rate, language) are imported from config.py
    but can be overridden here if needed.
    """
    api_key: str

    # Gemini Live model — must support the Live API (not all Gemini models do).
    model: str = "gemini-3.1-flash-live-preview"

    # Audio format — must match what stream_wav.py produces.
    sample_rate: int = AUDIO_SAMPLE_RATE

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
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._error: Optional[Exception] = None

    async def __aenter__(self) -> "GeminiLiveProvider":
        try:
            from google import genai  # noqa: PLC0415 — intentional lazy import
        except ImportError as exc:
            raise ImportError(
                "google-genai is required for GeminiLiveProvider. "
                "Install it with: pip install google-genai"
            ) from exc

        client = genai.Client(api_key=self._cfg.api_key)

        # Dict-form config is accepted by the SDK and avoids version-specific
        # type class imports (LiveConnectConfig field names shift across releases).
        #
        # IMPORTANT: response_modalities MUST be ["AUDIO"] — not ["TEXT"].
        # gemini-3.1-flash-live-preview is a native-audio model; it only supports
        # AUDIO output. Setting ["TEXT"] causes WebSocket 1011 at connection time.
        # We don't use the model's audio output — input_audio_transcription gives us
        # a text sidecar of the user's speech independently of the audio response.
        #
        # Do NOT add speech_config.language_code here. That field selects the TTS
        # output voice — it has nothing to do with STT input language detection.
        # Input language is auto-detected from the audio; no per-session input
        # language config exists in the Live API. Including speech_config with an
        # unsupported combination of fields previously caused WebSocket 1011.
        live_config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self._cfg.system_instruction,
            "input_audio_transcription": {},  # enables server_content.input_transcription events
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

        # Always terminate the events iterator so _receiver() in stt.py can exit.
        try:
            await self._events_q.put(None)
        except Exception:
            pass

    async def send_audio(self, pcm_chunk: bytes) -> None:
        """Send a raw PCM chunk to the Live API as realtime audio input."""
        if self._closed.is_set() or self._session is None:
            return
        if self._error:
            raise self._error
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
        """
        Signal end of audio stream and wait for the model to flush.

        Sends audio_stream_end + turn_complete to the Live API, then waits
        briefly for the model to return any in-flight transcript before closing
        the session. Closing the session causes _recv_loop to exit, which puts
        None in events_q and unblocks the events() iterator.
        """
        if self._session is None or self._closed.is_set():
            return

        try:
            # Notify the server that audio input is finished.
            await self._session.send_realtime_input(audio_stream_end=True)
        except Exception as e:
            logger.debug("[STT] GeminiLive: audio_stream_end signal: %s", e)

        # Note: send_client_content(turn_complete=True) is NOT supported in Gemini 3.1
        # for signalling end-of-turn — it is only for seeding initial context history.
        # audio_stream_end is the correct signal for this model.

        # Wait for the server to return the final transcript before closing.
        await asyncio.sleep(_END_AUDIO_DRAIN_S)

        # Close the session — this causes _recv_loop's async-for to complete,
        # which triggers the None sentinel in events_q.
        self._closed.set()
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass

    def events(self) -> AsyncIterator[TranscriptEvent]:
        """Async iterator yielding one final TranscriptEvent per committed utterance."""
        async def _aiter() -> AsyncIterator[TranscriptEvent]:
            while True:
                ev = await self._events_q.get()
                if ev is None:
                    if self._error:
                        raise self._error
                    break
                yield ev
        return _aiter()

    async def _recv_loop(self) -> None:
        """
        Background task: drain the Live API response stream.

        Reads server_content.input_transcription events — the native ASR path.
        Each event carries a Transcription(text, finished) object:
          - finished=False: interim/partial result
          - finished=True:  committed final result for this utterance

        The model's generated text responses (model_turn) are intentionally
        ignored here; they are not used for transcription.

        Gemini Live quirk — pending interim promotion
        ----------------------------------------------
        When audio_stream_end is sent, the server returns the final transcript
        with finished=False and then immediately closes the connection (1000 OK).
        It never sends finished=True for the last utterance before closing.
        To handle this, we hold the most recent finished=False text in
        _pending_interim. If the session closes normally without a finished=True
        for that text, we promote it to is_final=True in the finally block so it
        is not lost.
        """
        _pending_interim: Optional[str] = None
        try:
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
                            await self._events_q.put(TranscriptEvent(text=text, is_final=True))
                        else:
                            # Hold as pending — will be promoted if session closes without finished=True.
                            _pending_interim = text

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Determine whether this is a normal/expected close:
            #   _closed.is_set()        — we initiated close via end_audio() / __aexit__
            #   status_code == 1000     — server sent a normal WebSocket close frame;
            #                            the SDK converts ConnectionClosedOK →
            #                            APIError(1000) before we see it.  This can
            #                            arrive during the drain sleep in end_audio(),
            #                            before _closed is set, so we must also
            #                            check the code explicitly.
            is_normal_close = self._closed.is_set() or getattr(e, "status_code", None) == 1000
            if is_normal_close:
                logger.debug("[STT] GeminiLive: recv loop ended after close: %s", e)
            else:
                logger.exception("[STT] GeminiLive: receiver crashed: %r", e)
                if not self._error:
                    self._error = e
        finally:
            # Promote any pending interim to final before signalling end.
            # This handles Gemini's pattern of closing (1000 OK) without sending
            # finished=True for the last utterance.
            if _pending_interim and not self._error:
                logger.debug("[STT] GeminiLive: promoting pending interim to final: %s", _pending_interim[:60])
                await self._events_q.put(TranscriptEvent(text=_pending_interim, is_final=True))
            self._closed.set()
            await self._events_q.put(None)