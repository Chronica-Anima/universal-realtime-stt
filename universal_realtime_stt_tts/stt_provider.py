"""
STT Provider Protocol: the interface every real-time provider must implement.

All provider implementations (ElevenLabs, Deepgram, Google, ...) conform to the
RealtimeSttProvider protocol defined here. The protocol uses structural typing
(typing.Protocol), so providers do not inherit from it; they just implement
the required methods.

Lifecycle
---------
A provider instance goes through three phases:

1. **Construction**: instantiate with a provider-specific frozen dataclass
   config (API key, model, language, etc.). No network calls happen here.

2. **Session** (async context manager): entering the context opens the
   connection (WebSocket, SDK session or gRPC stream). From this point on you
   can send audio and iterate events. Exiting the context tears it down.

3. **Streaming**: within the session, two concurrent operations run:

   - ``send_audio(chunk)``: feed raw PCM16LE mono bytes at the configured
     sample rate (``AUDIO_SAMPLE_RATE`` in ``config.py``, 16 kHz by default).
     Call ``end_audio()`` once when all audio has been sent.
   - ``events()``: async iterator yielding ``TranscriptEvent`` objects.
     Partial results have ``is_final=False``; committed segments have
     ``is_final=True``. The iterator ends when the provider closes.

Implementing a new provider
---------------------------
1. Create ``universal_realtime_stt_tts/stt_provider_<name>.py``. ADRs 0001 and
   0004 in ``doc/adr/`` record when a provider uses its SDK and when a raw
   WebSocket. An SDK is an optional extra in ``pyproject.toml`` and is
   imported inside the provider, so the module loads without it.

2. Define a frozen ``@dataclass`` config with at least the API key and any
   provider-specific settings (model, URL overrides, VAD params). Universal
   settings take their defaults from ``config.py``
   (e.g. ``sample_rate: int = AUDIO_SAMPLE_RATE``), so a change there reaches
   every provider.

3. Implement a class satisfying this protocol::

       class MyProvider:
           def __init__(self, config: MyConfig) -> None:
               self._eq = SttEventQueue(logger)

           async def __aenter__(self) -> "MyProvider":
               # Open WebSocket / SDK session / gRPC stream.
               return self

           async def __aexit__(self, exc_type, exc, tb) -> None:
               # Close connection.
               ...

           async def send_audio(self, pcm_chunk: bytes) -> None:
               # Forward chunk to the provider (binary or base64).
               ...

           async def end_audio(self) -> None:
               # Signal end-of-audio (provider-specific close message).
               ...

           def events(self) -> AsyncIterator[TranscriptEvent]:
               # Partial transcripts: is_final=False
               # Committed transcripts: is_final=True
               return self._eq.events()

   Every provider composes ``SttEventQueue`` (``_event_queue.py``) for its
   event queue, error state and end-of-stream sentinel. Raw WebSocket
   providers wrap their receive loop in ``SttEventQueue.recv_guard()``.

4. Add a test method in ``tests/test_stt.py``, an entry in the ``PROVIDERS``
   table in ``benchmark.py``, and the provider's key to the README's
   credential list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class TranscriptEvent:
    """
    A single transcript event from an STT provider.

    Attributes:
        text: The transcribed text for this event.
        is_final: True if this is a committed (final) transcript segment.
            False for partial/interim results that may still change.
        speaker: Raw diarization label from the provider (e.g. "S1", "S2", "??").
    """
    text: str
    is_final: bool
    speaker: str | None = None


@runtime_checkable
class RealtimeSttProvider(Protocol):
    """Structural protocol for real-time STT providers; see the module docstring."""
    async def __aenter__(self) -> "RealtimeSttProvider": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def send_audio(self, pcm_chunk: bytes) -> None: ...
    async def end_audio(self) -> None: ...

    def events(self) -> AsyncIterator[TranscriptEvent]: ...
