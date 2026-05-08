"""
STT Provider Protocol — the interface every real-time provider must implement.

All provider implementations (ElevenLabs, Deepgram, Google, …) conform to the
RealtimeSttProvider protocol defined here. The protocol uses structural typing
(typing.Protocol), so providers do not need to inherit from it — they just need
to implement the required methods.

Lifecycle
---------
A provider instance goes through three phases:

1. **Construction** — instantiate with a provider-specific frozen dataclass
   config (API key, model, language, etc.). No network calls happen here.

2. **Session** (async context manager) — entering the context opens the
   connection (typically a WebSocket). From this point on you can send audio
   and iterate events. Exiting the context tears down the connection.

3. **Streaming** — within the session, two concurrent operations run:

   - ``send_audio(chunk)`` — feed raw PCM bytes (16 kHz, mono, 16-bit).
     Call ``end_audio()`` once when all audio has been sent.
   - ``events()`` — async iterator yielding ``TranscriptEvent`` objects.
     Partial results have ``is_final=False``; committed segments have
     ``is_final=True``. The iterator ends when the provider closes.

Implementing a new provider
----------------------------
1. Create ``universal_realtime_audio/stt_provider_<name>.py``.

2. Define a frozen ``@dataclass`` config with at least the API key and any
   provider-specific settings (model, URL overrides, VAD params). Universal
   audio settings (sample rate, language) should use literal defaults
   (e.g. ``sample_rate: int = 16000``).

3. Implement a class satisfying this protocol::

       class MyProvider:
           def __init__(self, config: MyConfig) -> None: ...

           async def __aenter__(self) -> "MyProvider":
               # Open WebSocket / gRPC channel.
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

           async def events(self) -> AsyncIterator[TranscriptEvent]:
               # Yield TranscriptEvent for each provider message.
               # Partial transcripts: is_final=False
               # Committed transcripts: is_final=True
               ...

   Most providers use an internal ``asyncio.Queue[TranscriptEvent]`` fed by
   a background WebSocket listener task, with ``events()`` draining it.

4. Add a test method in ``tests/test_stt.py`` and a benchmark entry in
   ``benchmark.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass(frozen=True, init=True)
class TranscriptEvent:
    text: str
    is_final: bool
    speaker: str | None = None


@runtime_checkable
class RealtimeSttProvider(Protocol):
    """
    Structural protocol for real-time STT providers.

    Any class implementing these methods is a valid provider — no
    inheritance required. See the module docstring for lifecycle details
    and implementation guidance.
    """
    async def __aenter__(self) -> "RealtimeSttProvider": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def send_audio(self, pcm_chunk: bytes) -> None: ...
    async def end_audio(self) -> None: ...

    def events(self) -> AsyncIterator[TranscriptEvent]: ...
