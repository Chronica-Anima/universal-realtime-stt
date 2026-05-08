# universal-realtime-audio — Library Specification

## Executive Summary

- **What**: Rename `universal-realtime-stt` → `universal-realtime-audio`. Switch ElevenLabs and Speechmatics STT providers from raw WebSocket to official SDKs. Add TTS abstraction with ElevenLabs provider. Add diarization support.
- **Why**: Official SDKs are maintained, handle protocol changes, and expose features (diarization, end-of-utterance) that are painful to implement manually. Adding TTS makes this a complete audio provider library.
- **Scope**: Library changes only. App integration is specified separately in `chronica_anima_app/docs/specs/spec_audio_provider_abstraction.md`.

## Current State

All STT providers use raw `websockets` with manual JSON serialization:
- **ElevenLabs** (`ElevenLabsRealtimeProvider`): base64-encodes PCM in JSON envelopes, manual WebSocket lifecycle, parses `partial_transcript`/`committed_transcript` message types
- **Speechmatics** (`SpeechmaticsRealtimeProvider`): binary PCM frames + JSON control messages (`StartRecognition`, `EndOfStream`), manual `_recv_loop`, swallows `AddPartialTranscript` events

Both depend only on the `websockets` library. No TTS abstraction exists.

## Design Decisions

### D1: Switch to official SDKs where available

**Decision**: Replace raw WebSocket implementations with official Python SDKs for ElevenLabs (`elevenlabs` package) and Speechmatics (`speechmatics-rt` package).

**Why**: The current implementations manually handle WebSocket protocol details — connection URLs, message serialization, authentication headers, error codes. Official SDKs handle this and also expose features like diarization that would require significant manual protocol work.

**What changes**:
- ElevenLabs STT: `websockets` → `elevenlabs` SDK (via `client.speech_to_text.realtime.connect()`)
- Speechmatics STT: `websockets` → `speechmatics-rt` SDK (`AsyncClient` with `send_audio()`)
- ElevenLabs TTS: `elevenlabs` SDK (`text_to_speech.convert()`)

**What stays the same**: The `RealtimeSttProvider` and `RealtimeTtsProvider` protocols, `TranscriptEvent` dataclass, `stt_session_task()` orchestration, and the queue-based architecture.

### D2: Extend TranscriptEvent with speaker info for diarization

**Decision**: Add an optional `speaker` field to `TranscriptEvent`.

**Why**: Speechmatics provides speaker labels (`S1`, `S2`, ...) on word-level results in `results[].alternatives[].speaker` (verified from `RecognitionAlternative` dataclass in SDK source). Providers without diarization return `speaker=None`.

### D3: Add TTS protocol and ElevenLabs provider

**Decision**: Add `RealtimeTtsProvider` protocol alongside existing STT protocol. Implement ElevenLabs TTS provider.

Speechmatics TTS is English-only (no Czech voices) — not implemented until they add Czech support.

### D4: Keep raw WebSocket for other providers

**Decision**: Cartesia, Deepgram, Google, Gemini Live keep their current raw `websockets` implementations.

**Why**: No official SDKs needed for these. `websockets` remains a core dependency for them.

### D5: SttEventQueue — shared event queue helper

**Decision**: Extract the queue + error + sentinel pattern into a reusable `SttEventQueue` helper class. All SDK-based providers compose it instead of managing their own queue boilerplate.

**Why**: Every provider duplicated the same pattern: an `asyncio.Queue[TranscriptEvent | None]`, error storage, sentinel deduplication, and a `put_nowait` wrapper with queue-full handling. `SttEventQueue` encapsulates this in one place.

## SDK API Reference (Verified)

### ElevenLabs SDK (`elevenlabs` package)

**Module**: `elevenlabs.realtime.scribe` — contains `ScribeRealtime` class.

**Entry point** (verified from `speech_to_text_custom.py`):
```python
from elevenlabs import ElevenLabs, RealtimeAudioOptions, AudioFormat, CommitStrategy, RealtimeEvents

client = ElevenLabs(api_key="...")
connection = await client.speech_to_text.realtime.connect(
    RealtimeAudioOptions(
        model_id="scribe_v2_realtime",
        audio_format=AudioFormat.PCM_16000,
        sample_rate=16000,
        commit_strategy=CommitStrategy.VAD,
        vad_silence_threshold_secs=1.5,
        vad_threshold=0.4,
        min_speech_duration_ms=100,
        min_silence_duration_ms=100,
        language_code="cs",
    )
)
```

**Events** (callback-based, registered via `connection.on()`):
```python
connection.on(RealtimeEvents.PARTIAL_TRANSCRIPT, lambda data: ...)
connection.on(RealtimeEvents.COMMITTED_TRANSCRIPT, lambda data: ...)
connection.on(RealtimeEvents.ERROR, lambda err: ...)
connection.on(RealtimeEvents.CLOSE, lambda: ...)
```

**Sending audio** (base64-encoded PCM):
```python
import base64
await connection.send({"audio_base_64": base64.b64encode(pcm).decode(), "sample_rate": 16000})
```

**Closing**: `await connection.close()`

### Speechmatics RT SDK (`speechmatics-rt` v1.0.0)

**Package**: `speechmatics-rt` on PyPI (released March 2026). Imports from `speechmatics.rt`.

**Entry point** (verified from SDK source and examples):
```python
from speechmatics.rt import (
    AsyncClient, ServerMessageType, TranscriptionConfig,
    AudioFormat, AudioEncoding, ConversationConfig,
    SpeakerDiarizationConfig, StaticKeyAuth,
)

async with AsyncClient(auth=StaticKeyAuth(api_key="...")) as client:
    await client.start_session(
        transcription_config=TranscriptionConfig(
            language="cs",
            enable_partials=True,
            max_delay=1.0,
            operating_point="enhanced",
            diarization="speaker",
            speaker_diarization_config=SpeakerDiarizationConfig(
                max_speakers=2,
                speaker_sensitivity=0.5,
            ),
            conversation_config=ConversationConfig(
                end_of_utterance_silence_trigger=1.0,
            ),
        ),
        audio_format=AudioFormat(
            encoding=AudioEncoding.PCM_S16LE,
            sample_rate=16000,
        ),
    )
    # Send audio chunks directly — no IOBase stream needed
    await client.send_audio(pcm_chunk)  # bytes only, raises ValueError on bytearray
```

**Events** (decorator-based):
```python
@client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
def handle_partial(msg): ...

@client.on(ServerMessageType.ADD_TRANSCRIPT)
def handle_final(msg): ...

@client.on(ServerMessageType.END_OF_UTTERANCE)
def handle_eou(msg): ...
```

Handlers can be sync or async. The client is fully async — no threads, no `IOBase` stream bridging needed.

**Key constraint**: `send_audio()` accepts only `bytes`. Passing `bytearray` raises `ValueError`.

**Diarization message format** (verified from `RecognitionAlternative` dataclass):
```json
{
    "message": "AddTranscript",
    "metadata": {
        "start_time": 1.2,
        "end_time": 3.5,
        "transcript": "Dobrý den, jak se máte"
    },
    "results": [
        {
            "type": "word",
            "start_time": 1.2,
            "end_time": 1.8,
            "alternatives": [
                {
                    "content": "Dobrý",
                    "confidence": 0.99,
                    "speaker": "S1"
                }
            ]
        }
    ]
}
```

Speaker labels: `"S1"`, `"S2"`, ..., `"UU"` (unidentified).


## STT Protocol

### TranscriptEvent

```python
@dataclass(frozen=True, init=True)
class TranscriptEvent:
    text: str
    is_final: bool
    speaker: str | None = None  # "S1", "S2", ... or None
```

Backward-compatible — existing `TranscriptEvent(text=..., is_final=...)` calls still work.

### RealtimeSttProvider

```python
@runtime_checkable
class RealtimeSttProvider(Protocol):
    async def __aenter__(self) -> "RealtimeSttProvider": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def send_audio(self, pcm_chunk: bytes) -> None: ...
    async def end_audio(self) -> None: ...
    def events(self) -> AsyncIterator[TranscriptEvent]: ...
```

File: `universal_realtime_audio/stt_provider.py`

### SttEventQueue — shared event helper

All SDK-based providers compose `SttEventQueue` instead of managing their own queue + error + sentinel boilerplate.

```python
class SttEventQueue:
    def __init__(self, log: Logger, maxsize: int = 200) -> None: ...

    @property
    def error(self) -> Exception | None: ...
    def set_error(self, err: Exception) -> None: ...

    def put_nowait(self, ev: TranscriptEvent) -> None: ...
    async def put(self, ev: TranscriptEvent) -> None: ...

    def put_sentinel(self) -> None: ...
    async def put_sentinel_async(self) -> None: ...

    def events(self) -> AsyncIterator[TranscriptEvent]: ...
```

Key behaviors:
- `put_nowait` drops events with a warning when queue is full (never blocks the callback thread)
- `put_sentinel` is idempotent — only the first call enqueues `None`
- `set_error` stores the exception and sends a sentinel; `events()` raises it when the sentinel is consumed
- `put_sentinel_async` is an awaitable variant for use from coroutines scheduled cross-thread

File: `universal_realtime_audio/_event_queue.py`

### stt_session_task()

All events (partials and finals) flow through `transcript_queue` as `TranscriptEvent` objects. The consumer decides how to handle each based on `is_final`. The session runs two top-level async functions and manages their lifecycle explicitly: it awaits the receiver, then cancels the sender.

```python
_SILENCE_CHUNK = b"\x00\x00" * 1600  # 100ms silence at 16kHz mono 16-bit
_AUDIO_TIMEOUT_S = 0.2


async def _audio_sender(
    provider: RealtimeSttProvider,
    audio_queue: asyncio.Queue[bytes | None],
    conversation_running: asyncio.Event,
) -> None:
    try:
        while conversation_running.is_set():
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=_AUDIO_TIMEOUT_S)
            except asyncio.TimeoutError:
                chunk = _SILENCE_CHUNK
            if chunk is None:
                break
            await provider.send_audio(chunk)
    finally:
        await provider.end_audio()


async def _event_receiver(
    provider: RealtimeSttProvider,
    transcript_queue: asyncio.Queue[TranscriptEvent | None],
    conversation_running: asyncio.Event,
) -> None:
    async for ev in provider.events():
        if not conversation_running.is_set():
            break
        if ev.text.strip():
            await transcript_queue.put(ev)
    await transcript_queue.put(None)


async def stt_session_task(
    provider: RealtimeSttProvider,
    audio_queue: asyncio.Queue[bytes | None],
    transcript_queue: asyncio.Queue[TranscriptEvent | None],
    conversation_running: asyncio.Event,
) -> None:
    async with provider:
        sender = asyncio.create_task(
            _audio_sender(provider, audio_queue, conversation_running)
        )
        receiver = asyncio.create_task(
            _event_receiver(provider, transcript_queue, conversation_running)
        )

        try:
            await receiver
        finally:
            if not sender.done():
                sender.cancel()
            try:
                await sender
            except (asyncio.CancelledError, Exception):
                pass
```

Lifecycle rationale: awaiting `receiver` first ensures all transcript events are consumed before teardown. If `receiver` finishes (provider closed or `conversation_running` cleared), sender is cancelled — this triggers `end_audio()` in sender's `finally` block, giving the provider a clean shutdown signal.

File: `universal_realtime_audio/stt.py`


## ElevenLabs STT Provider — SDK Rewrite

Uses `SttEventQueue` for event handling. A unified `_on_transcript` callback handles both partials and finals. A `_closed` flag prevents double-close on the connection.

```python
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
```

### ElevenLabsSttConfig

```python
@dataclass(frozen=True)
class ElevenLabsSttConfig:
    api_key: str
    model: str = "scribe_v2_realtime"
    language: str = "cs"
    sample_rate: int = 16000
    vad_silence_threshold_s: float = 1.0
    vad_threshold: float = 0.6
    min_silence_duration_ms: int = 300
    min_speech_duration_ms: int = 1000
```

File: `universal_realtime_audio/stt_provider_elevenlabs.py`


## Speechmatics STT Provider — SDK Rewrite

Uses `SttEventQueue` for event handling. A shared `_emit_transcript` helper handles both partial and final events. Speaker extraction uses majority vote across word-level results.

```python
class SpeechmaticsSttProvider:
    def __init__(self, cfg: SpeechmaticsSttConfig) -> None:
        self._cfg = cfg
        self._eq = SttEventQueue(logger)
        self._client = None

    async def __aenter__(self) -> "SpeechmaticsSttProvider":
        from speechmatics.rt import (
            AsyncClient, ServerMessageType, TranscriptionConfig,
            AudioFormat, AudioEncoding, ConversationConfig,
            SpeakerDiarizationConfig, StaticKeyAuth,
        )

        self._client = AsyncClient(auth=StaticKeyAuth(api_key=self._cfg.api_key))
        await self._client.__aenter__()

        @self._client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
        def on_partial(msg):
            self._emit_transcript(msg, is_final=False)

        @self._client.on(ServerMessageType.ADD_TRANSCRIPT)
        def on_final(msg):
            self._emit_transcript(msg, is_final=True)

        @self._client.on(ServerMessageType.END_OF_TRANSCRIPT)
        def on_end(msg):
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
                operating_point=self._cfg.operating_point,
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

    def _emit_transcript(self, msg: dict, is_final: bool) -> None:
        text = msg.get("metadata", {}).get("transcript", "").strip()
        if text:
            speaker = self._extract_speaker(msg)
            self._eq.put_nowait(TranscriptEvent(text=text, is_final=is_final, speaker=speaker))

    def _extract_speaker(self, msg: dict) -> str | None:
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
```

### SpeechmaticsSttConfig

```python
@dataclass(frozen=True)
class SpeechmaticsSttConfig:
    api_key: str
    base_url: str = "wss://eu.rt.speechmatics.com/v2/"
    language: str = "cs"
    operating_point: str = "enhanced"  # "enhanced" or "standard"
    max_delay_s: float = 1.0
    sample_rate: int = 16000

    # Diarization
    diarization: str = "speaker"  # "speaker" | "channel" | "none"
    speaker_diarization_config: dict | None = None
    # e.g. {"max_speakers": 2, "speaker_sensitivity": 0.5}

    # End-of-utterance
    end_of_utterance_silence_trigger: float = 1.0  # seconds; 0 = disabled
```

File: `universal_realtime_audio/stt_provider_speechmatics.py`


## TTS Protocol

### RealtimeTtsProvider

```python
@runtime_checkable
class RealtimeTtsProvider(Protocol):
    async def synthesize(
        self, text: str, language: str,
    ) -> AsyncIterator[bytes]: ...
```

Contract: yields raw PCM 16-bit LE, 16 kHz mono chunks. Provider manages its own connection lifecycle per call.

File: `universal_realtime_audio/tts_provider.py`

### ElevenLabs TTS Provider

```python
@dataclass(frozen=True)
class ElevenLabsTtsConfig:
    api_key: str
    voice_id: str = "MpbYQvoTmXjHkaxtLiSh"
    model: str = "eleven_turbo_v2_5"
    stability: float = 0.4
    speed: float = 0.9

class ElevenLabsTtsProvider:
    def __init__(self, config: ElevenLabsTtsConfig) -> None:
        self._config = config

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        from elevenlabs import ElevenLabs, VoiceSettings

        client = ElevenLabs(api_key=self._config.api_key)
        audio_stream = client.text_to_speech.convert(
            text=text,
            voice_id=self._config.voice_id,
            model_id=self._config.model,
            output_format="pcm_16000",
            language_code=language,
            voice_settings=VoiceSettings(
                stability=self._config.stability,
                speed=self._config.speed,
            ),
        )
        for chunk in audio_stream:
            if chunk:
                yield chunk
```

File: `universal_realtime_audio/tts_provider_elevenlabs.py`


## Package Restructure

### Directory rename

```
lib/                              →  universal_realtime_audio/
lib/stt_provider.py               →  universal_realtime_audio/stt_provider.py
lib/stt.py                        →  universal_realtime_audio/stt.py
lib/stt_provider_elevenlabs.py    →  universal_realtime_audio/stt_provider_elevenlabs.py
lib/stt_provider_speechmatics.py  →  universal_realtime_audio/stt_provider_speechmatics.py
lib/stt_provider_*.py             →  universal_realtime_audio/stt_provider_*.py
lib/utils.py                      →  universal_realtime_audio/utils.py
(new)                             →  universal_realtime_audio/_event_queue.py
(new)                             →  universal_realtime_audio/tts_provider.py
(new)                             →  universal_realtime_audio/tts_provider_elevenlabs.py
```

### pyproject.toml

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "universal-realtime-audio"
version = "0.2.0"
description = "Multiprovider realtime speech-to-text and text-to-speech library with unified async interface"
requires-python = ">=3.10"
dependencies = [
    "websockets>=16.0.0",
    "python-dotenv>=1.2.1",
]

[project.optional-dependencies]
elevenlabs = ["elevenlabs>=2.0.0"]
speechmatics = ["speechmatics-rt>=1.0.0"]
google = ["google-cloud-speech>=2.36.0"]
gemini = ["google-genai>=1.64.0"]
benchmark = ["diff-match-patch>=20241021"]
semantic = ["google-genai>=1.64.0"]
dev = ["pytest"]
all = [
    "universal-realtime-audio[elevenlabs,speechmatics,google,gemini,benchmark]",
]

[tool.setuptools.packages.find]
include = ["universal_realtime_audio*"]
```

`websockets` stays as a core dependency — Cartesia, Deepgram, and other providers still use it.

### Config decoupling

All provider config dataclasses stop importing from `config.py`:

```python
# Before
from config import AUDIO_SAMPLE_RATE, STT_LANGUAGE_ISO_639_1

@dataclass(frozen=True)
class SpeechmaticsSttConfig:
    language: str = STT_LANGUAGE_ISO_639_1
    sample_rate: int = AUDIO_SAMPLE_RATE

# After
@dataclass(frozen=True)
class SpeechmaticsSttConfig:
    language: str = "cs"
    sample_rate: int = 16000
```

### Class renames

| Before                         | After                     |
|--------------------------------|---------------------------|
| `ElevenLabsRealtimeProvider`   | `ElevenLabsSttProvider`   |
| `SpeechmaticsRealtimeProvider` | `SpeechmaticsSttProvider` |

## Diarization

### Speechmatics diarization modes

| Mode                    | Description                        | Use case            |
|-------------------------|------------------------------------|---------------------|
| `"speaker"`             | Voice-based speaker identification | Default for our app |
| `"channel"`             | One transcript per audio channel   | Multi-mic setups    |
| `"channel_and_speaker"` | Combined (real-time only)          | Advanced scenarios  |

### Speaker labels

Labels appear at the word level in `results[].alternatives[].speaker`:
- `"S1"`, `"S2"`, ... — identified speakers
- `"UU"` — unidentified/noise

The provider extracts the dominant speaker per event via `_extract_speaker()` (majority vote across word results) and populates `TranscriptEvent.speaker`.

### Configuration

```python
SpeechmaticsSttConfig(
    diarization="speaker",
    speaker_diarization_config={
        "max_speakers": 2,
        "speaker_sensitivity": 0.5,
    },
)
```

### ElevenLabs — no diarization

`TranscriptEvent.speaker` is always `None` for ElevenLabs.

## Change Summary

| Change                                       | File(s)                                                       | Breaking? |
|----------------------------------------------|---------------------------------------------------------------|-----------|
| Rename repo/package                          | all files, `pyproject.toml`                                   | yes       |
| Package restructure `lib/` →                 | all files                                                     | yes       |
| Decouple config defaults                     | all provider configs                                          | no        |
| Class renames                                | `stt_provider_elevenlabs.py`, `stt_provider_speechmatics.py`  | yes       |
| TranscriptEvent + speaker field              | `stt_provider.py`                                             | no        |
| SttEventQueue helper                         | `_event_queue.py` (new)                                       | no        |
| ElevenLabs STT → SDK                         | `stt_provider_elevenlabs.py`                                  | no        |
| Speechmatics STT → SDK + diarization         | `stt_provider_speechmatics.py`                                | no        |
| Emit Speechmatics partials                   | `stt_provider_speechmatics.py`                                | no        |
| All events through queue (drop `on_partial`) | `stt.py`                                                      | yes       |
| Silence keepalive                            | `stt.py`                                                      | no        |
| Optional deps per provider                   | `pyproject.toml`                                              | yes       |
| TTS protocol                                 | `tts_provider.py` (new)                                       | no        |
| ElevenLabs TTS provider                      | `tts_provider_elevenlabs.py` (new)                            | no        |

## Implementation Phases

### Phase 1: Structural

1. Rename repo `universal-realtime-stt` → `universal-realtime-audio`
2. Rename `lib/` → `universal_realtime_audio/`
3. Update all internal imports
4. Update `pyproject.toml` (name, package find, deps structure)
5. Rename `ElevenLabsRealtimeProvider` → `ElevenLabsSttProvider`
6. Rename `SpeechmaticsRealtimeProvider` → `SpeechmaticsSttProvider`
7. Decouple all provider configs from `config.py`
8. Update helpers, tests, benchmarks
9. Verify `pip install -e .` works

### Phase 2: STT improvements

1. Add `speaker` field to `TranscriptEvent`
2. Create `_event_queue.py` with `SttEventQueue` helper
3. Route all events (partials + finals) through `transcript_queue`
4. Refactor `stt_session_task` to top-level `_audio_sender`/`_event_receiver` functions with explicit lifecycle
5. Add silence keepalive to `_audio_sender`
6. Rewrite ElevenLabs STT to use `elevenlabs` SDK + `SttEventQueue`
7. Rewrite Speechmatics STT to use `speechmatics-rt` SDK + `SttEventQueue`
8. Enable diarization in Speechmatics provider
9. Emit partial transcripts from Speechmatics provider
10. Add `end_of_utterance_silence_trigger` via `ConversationConfig`
11. Test both providers with Czech audio

### Phase 3: TTS

1. Create `tts_provider.py` (Protocol)
2. Create `tts_provider_elevenlabs.py`
3. Test ElevenLabs TTS with Czech text
