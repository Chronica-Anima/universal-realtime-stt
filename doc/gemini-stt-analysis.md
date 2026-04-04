# Gemini as STT + Analysis Provider — Feasibility Analysis

**Date**: 2026-04-04
**Status**: Decision pending — team review required
**Question**: Can Gemini replace dedicated STT providers and simultaneously deliver emotion/energy tagging in a real-time or near-real-time pipeline?

---

## Background and Motivation

The current pipeline has two hops:

```
Dedicated STT provider (WebSocket streaming)
    → transcript text (400–750ms after utterance end)
        → Flash-Lite reasoning call (downstream, separate project)
            → response (800–2000ms)

Total end-to-end: ~1.2–2.75s after utterance ends
```

Gemini models accept raw audio natively and can perform transcription + reasoning in a single call. If the combined latency is within budget and audio quality is sufficient, this eliminates one full network round-trip and unlocks structured emotion/energy annotation that no current STT provider exposes.

This document evaluates two real-time-capable paths. A pure batch path (upload complete file → one API call → response) was considered but is incompatible with the real-time library goal and is mentioned only briefly in the appendix.

---

## Model Landscape (as of April 2026)

| Model ID | Type | Streaming | Status |
|---|---|---|---|
| `gemini-2.5-flash-lite` | Batch only | No | GA / stable |
| `gemini-3.1-flash-lite-preview` | Batch only | No | Preview (released 2026-03-03) |
| `gemini-3.1-flash-live-preview` | Live API (WebSocket) | Yes | Preview (released 2026-03-26) |

Both preview models carry API stability risk. No GA date has been announced for either. The `gemini-2.0-flash-lite-001` model was deprecated for new projects as of 2026-03-06.

**Audio input facts** (all Flash-Lite and Flash-Live models):
- Supported formats: `audio/wav`, `audio/pcm`, `audio/mp3`, `audio/flac`, `audio/ogg`, `audio/mp4`, `audio/webm`
- Your format (16kHz, mono, 16-bit PCM) is natively supported
- Internal resampling: 16 Kbps mono
- Pricing: 32 tokens per second of audio → 1,920 tokens/minute

---

## STT Accuracy Baseline

| Provider | WER (English) | WER (Czech) | Notes |
|---|---|---|---|
| Deepgram Nova-3 | ~2.9% | Not published | Best English accuracy |
| ElevenLabs Scribe | ~3.1% | ~5.5% (Common Voice) | Best documented Czech |
| Google Chirp 2 | ~3.5% | Moderate | Trails ElevenLabs on Czech |
| `gemini-3.1-flash-lite` (batch) | ~4.0% | **No data** | — |
| `gemini-3.1-flash-live` (stream) | Not published | **No data** | — |

**Critical gap**: No published WER or CER benchmark exists for any Gemini model on Czech. The existing `assets/` WAV files and `benchmark.py` infrastructure are the right tools for an empirical first measurement. This should be done before committing to either path.

**Hallucination risk**: Gemini is a generative model. Unlike Deepgram and ElevenLabs (discriminative ASR), it can produce phonetically plausible but factually wrong transcriptions. Czech diacritics (ř, š, č, ž) that carry semantic weight are highest-risk. This failure mode does not exist in dedicated STT providers.

---

## Path A — Local VAD + Flash-Lite Batch (Near-Real-Time)

### Concept

Utterance segmentation is done client-side using a lightweight VAD library (`webrtcvad`). PCM chunks accumulate in an internal buffer. When the VAD detects sustained silence (utterance boundary), the buffer is flushed to the Flash-Lite `generateContent` API as a single HTTP call. The response contains both the transcript and structured emotion/energy annotations. One `TranscriptEvent` is emitted per utterance.

```
stream_wav / microphone
    → 200ms PCM chunks → GeminiFlashProvider.send_audio()
        → internal ring buffer + WebRTC VAD
            [on silence detected]
            → google-genai generateContent(audio_bytes, system_prompt)
                → parse JSON response
                    → TranscriptEvent(text, metadata={emotion, energy, ...})
```

### Why this replaces the downstream LLM call

The Flash-Lite call that currently happens downstream (in a separate project) to reason about the transcript can be merged into this single audio call. The system prompt carries conversation context and reasoning instructions. The response carries both the transcript and the reasoning output — eliminating one full network round-trip.

### Latency Analysis

| Step | Current (STT + separate LLM) | Path A (combined) |
|---|---|---|
| VAD silence detection | Provider-side: 300–600ms | Local WebRTC VAD: <10ms + configurable threshold (~500ms) |
| STT network + inference | 200–500ms | Eliminated |
| LLM reasoning call | 800–2000ms | Merged into the Flash-Lite audio call |
| Flash-Lite audio inference | — | ~600–1500ms (32 tokens/s audio + output tokens) |
| **Total after utterance end** | **~1.2–2.75s** | **~0.6–1.5s** |

Path A is faster end-to-end than the current two-call pipeline. Latency scales with utterance length (audio tokens) and response complexity (output tokens for reasoning). Short utterances are fast; long monologues slow down proportionally.

**No incremental/partial results.** The first word of an utterance appears only after the complete utterance ends and the API call returns. This is inherent to the batch-per-utterance approach.

### Emotion and Energy Tagging

This is Path A's primary advantage over all current providers. The system prompt defines the output schema; the model reasons over raw audio acoustics + linguistic content simultaneously.

Example prompt structure:

```
You are processing Czech conversational audio.

1. Transcribe the audio exactly as spoken.
2. For each utterance, classify:
   - emotion: neutral | happy | frustrated | anxious | excited | confused | sad
   - energy: high | medium | low
   - engagement: engaged | passive | distracted

Return only valid JSON:
{
  "transcript": "...",
  "utterances": [
    {"text": "...", "emotion": "...", "energy": "...", "engagement": "..."}
  ],
  "summary": {"dominant_emotion": "...", "energy_arc": "ascending|stable|declining"}
}

Prior conversation context:
{{ conversation_history }}
```

The `conversation_history` injection means the model can resolve disfluencies and ambiguities using what was said before — something no dedicated STT provider supports.

**Limitations of emotion output**: These are model judgements over audio, not acoustic measurements. Output will vary across runs, and Czech prosody (different pitch/stress patterns from English) may not be well-calibrated. Should be treated as soft signals, not ground truth.

### VAD Implementation

`webrtcvad` is the correct library — the same VAD algorithm used inside WebRTC, CPU-only, ~40KB, no ML framework dependency.

```python
import webrtcvad

vad = webrtcvad.Vad(aggressiveness=2)  # 0=permissive, 3=aggressive

# Your 200ms chunk (3200 bytes at 16kHz 16-bit mono)
# must be split into 20ms sub-frames for webrtcvad:
FRAME_MS = 20
FRAME_BYTES = int(16000 * FRAME_MS / 1000) * 2  # 640 bytes per 20ms frame

def is_speech(chunk: bytes) -> bool:
    """Returns True if any 20ms sub-frame in the chunk contains speech."""
    for i in range(0, len(chunk), FRAME_BYTES):
        frame = chunk[i:i + FRAME_BYTES]
        if len(frame) == FRAME_BYTES:
            if vad.is_speech(frame, sample_rate=16000):
                return True
    return False
```

Utterance boundary logic: declare utterance end after N consecutive silent 200ms chunks. A threshold of 2–3 chunks (400–600ms) is typical.

```python
SILENCE_CHUNKS_THRESHOLD = 3  # 600ms of silence → flush

silent_chunks = 0
buffer = bytearray()

def on_chunk(chunk: bytes):
    global silent_chunks, buffer
    buffer.extend(chunk)
    if is_speech(chunk):
        silent_chunks = 0
    else:
        silent_chunks += 1
        if silent_chunks >= SILENCE_CHUNKS_THRESHOLD and len(buffer) > 0:
            flush_to_gemini(bytes(buffer))
            buffer.clear()
            silent_chunks = 0
```

### Provider Implementation Sketch

```python
# lib/stt_provider_gemini_flash.py

import asyncio
import base64
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional
import google.generativeai as genai

from config import AUDIO_SAMPLE_RATE, STT_LANGUAGE_ISO_639_1
from lib.stt_provider import TranscriptEvent

SILENCE_CHUNKS_THRESHOLD = 3   # 600ms silence → flush utterance
FRAME_MS = 20                  # webrtcvad frame size
FRAME_BYTES = int(16000 * FRAME_MS / 1000) * 2  # 640 bytes


@dataclass(frozen=True)
class GeminiFlashConfig:
    api_key: str
    model: str = "gemini-3.1-flash-lite-preview"
    language: str = STT_LANGUAGE_ISO_639_1
    sample_rate: int = AUDIO_SAMPLE_RATE
    silence_chunks_threshold: int = SILENCE_CHUNKS_THRESHOLD
    system_prompt: str = (
        "Transcribe the Czech audio exactly. "
        "Return JSON: {\"transcript\": \"...\", \"emotion\": \"...\", \"energy\": \"...\"}"
    )


class GeminiFlashProvider:
    """
    Near-realtime STT + analysis via Gemini Flash-Lite batch API.

    Buffers incoming 200ms PCM chunks, detects utterance boundaries using
    webrtcvad, then fires a generateContent call per utterance. Emits one
    TranscriptEvent per utterance (is_final=True).

    Conforms to the RealtimeSttProvider protocol — usable as a drop-in
    replacement for streaming providers in stt_session_task().
    """

    def __init__(self, cfg: GeminiFlashConfig) -> None:
        self._cfg = cfg
        self._vad = None  # webrtcvad.Vad, initialised in __aenter__
        self._buffer = bytearray()
        self._silent_chunks = 0
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._flush_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._flush_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def __aenter__(self) -> "GeminiFlashProvider":
        import webrtcvad
        genai.configure(api_key=self._cfg.api_key)
        self._vad = webrtcvad.Vad(aggressiveness=2)
        self._flush_task = asyncio.create_task(self._flush_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._closed.set()
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._events_q.put(None)

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if self._closed.is_set():
            return
        self._buffer.extend(pcm_chunk)
        if self._is_speech(pcm_chunk):
            self._silent_chunks = 0
        else:
            self._silent_chunks += 1
            if self._silent_chunks >= self._cfg.silence_chunks_threshold and len(self._buffer) > 0:
                await self._flush_q.put(bytes(self._buffer))
                self._buffer.clear()
                self._silent_chunks = 0

    async def end_audio(self) -> None:
        # Flush any remaining audio
        if self._buffer:
            await self._flush_q.put(bytes(self._buffer))
            self._buffer.clear()
        await self._flush_q.put(None)  # sentinel: no more flushes coming

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter():
            while True:
                ev = await self._events_q.get()
                if ev is None:
                    break
                yield ev
        return _aiter()

    def _is_speech(self, chunk: bytes) -> bool:
        for i in range(0, len(chunk), FRAME_BYTES):
            frame = chunk[i:i + FRAME_BYTES]
            if len(frame) == FRAME_BYTES and self._vad.is_speech(frame, sample_rate=16000):
                return True
        return False

    async def _flush_loop(self) -> None:
        """Background task: drain flush queue, call Gemini API, emit events."""
        try:
            while True:
                audio = await self._flush_q.get()
                if audio is None:
                    break
                ev = await self._call_gemini(audio)
                if ev:
                    await self._events_q.put(ev)
        finally:
            await self._events_q.put(None)

    async def _call_gemini(self, audio_bytes: bytes) -> Optional[TranscriptEvent]:
        """Make a single generateContent call with buffered PCM audio."""
        try:
            model = genai.GenerativeModel(self._cfg.model)
            audio_part = {
                "mime_type": "audio/pcm",
                "data": base64.b64encode(audio_bytes).decode("ascii"),
            }
            response = await asyncio.to_thread(
                model.generate_content,
                [self._cfg.system_prompt, audio_part],
            )
            text = response.text.strip()
            # TODO: parse JSON from text to extract emotion/energy metadata
            return TranscriptEvent(text=text, is_final=True)
        except Exception as exc:
            # Log and continue — do not crash the session on one bad utterance
            return None
```

**Note on the JSON response**: The sketch above returns raw text. In practice, parse the JSON to extract `transcript`, `emotion`, `energy` etc. The metadata would live in `TranscriptEvent.metadata` once that field is added (see Architecture Notes below).

### Dependencies

```
# requirements.txt additions
webrtcvad>=2.0.10
google-generativeai>=0.8.0   # or google-genai — already soft-optional in this repo
```

### Architecture Notes

**`TranscriptEvent.metadata`**: Currently `TranscriptEvent` has only `text` and `is_final`. To carry emotion/energy data end-to-end, add an optional field:

```python
# lib/stt_provider.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    metadata: dict = field(default_factory=dict)  # emotion, energy, engagement, etc.
```

This is backward-compatible: the existing `stt_session_task._receiver()` uses only `ev.is_final` and `ev.text.strip()`. The metadata is invisible to the core pipeline until you explicitly thread it through.

To carry metadata to the caller, `transcript_queue` would need to change from `Queue[Optional[str]]` to `Queue[Optional[TranscriptEvent]]` — a clean but non-trivial refactor touching `stt.py`, `transcript_ingest_task`, and `transcribe.py`. This can be deferred: start with metadata accessible only at the provider boundary, promote to the full queue type when downstream consumers need it.

---

## Path B — Flash-Live WebSocket (True Real-Time)

### Concept

`gemini-3.1-flash-live-preview` is a bidirectional audio-to-audio Live API model over WebSocket — architecturally the same as Deepgram and Speechmatics. It fits directly into the existing `RealtimeSttProvider` protocol as a new provider file. The model is a voice agent by default; audio output is suppressed via config (`response_modalities: ["TEXT"]`) so only transcript text is returned.

```
stream_wav / microphone
    → 200ms PCM chunks (unchanged from current providers)
        → GeminiLiveProvider.send_audio()
            → WebSocket: { realtime_input: { media_chunks: [...] } }
                ← serverContent.modelTurn.parts[].text
                    → TranscriptEvent(text, is_final)
```

### Differences from Path A

| | Path A | Path B |
|---|---|---|
| Results | Per-utterance (after full utterance) | Per-word partials + final |
| First word latency | ~600ms–1.5s after utterance ends | ~200–500ms while speaker is still talking |
| Emotion/energy data | First-class, structured, per-utterance | Text-level inference only; no structured API |
| Replaces downstream LLM call | Yes | No — still need separate reasoning call |
| VAD | Client-side (`webrtcvad`) | Server-side (built into Live API) |
| No custom chunking needed | No — 200ms chunks work as-is | Yes — 200ms chunks work as-is |

### When to prefer Path B

Path B is the right choice if sub-second word-level streaming matters — for example, to interrupt a speaker, begin generating a TTS response before they finish, or drive a low-latency voice UI. Path A cannot do this because it withholds all output until after the utterance is complete.

If the downstream pipeline is latency-tolerant (you process after utterance end anyway), Path A's combined call is strictly better — faster end-to-end and richer output.

### Provider Implementation Sketch

The Live API uses a Google-specific WebSocket protocol. The connection URL and message format differ substantially from Deepgram/Speechmatics, but the provider shape is identical:

```python
# lib/stt_provider_gemini_live.py

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import websockets

from config import AUDIO_SAMPLE_RATE, STT_LANGUAGE_ISO_639_1
from lib.stt_provider import TranscriptEvent

LIVE_API_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


@dataclass(frozen=True)
class GeminiLiveConfig:
    api_key: str
    model: str = "gemini-3.1-flash-live-preview"
    language: str = STT_LANGUAGE_ISO_639_1
    sample_rate: int = AUDIO_SAMPLE_RATE


class GeminiLiveProvider:
    """
    Real-time STT via Gemini Live API (WebSocket bidirectional streaming).

    Sends 200ms PCM chunks over WebSocket and yields TranscriptEvent objects
    as the model produces text output. Audio output is suppressed — only text
    transcription events are emitted.

    Conforms to the RealtimeSttProvider protocol.
    """

    def __init__(self, cfg: GeminiLiveConfig) -> None:
        self._cfg = cfg
        self._ws = None
        self._events_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._rx_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def __aenter__(self) -> "GeminiLiveProvider":
        url = f"{LIVE_API_URL}?key={self._cfg.api_key}"
        self._ws = await websockets.connect(url, ping_interval=10, ping_timeout=10)

        # Send setup message: text-only output, suppress audio generation
        setup = {
            "setup": {
                "model": f"models/{self._cfg.model}",
                "generation_config": {
                    "response_modalities": ["TEXT"],
                    "speech_config": {
                        "language_code": f"{self._cfg.language}-CZ"  # BCP-47
                    }
                }
            }
        }
        await self._ws.send(json.dumps(setup))

        # Await setup confirmation
        resp = json.loads(await self._ws.recv())
        if "setupComplete" not in resp:
            raise RuntimeError(f"Live API setup failed: {resp}")

        self._rx_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._closed.set()
        if self._rx_task:
            self._rx_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        await self._events_q.put(None)

    async def send_audio(self, pcm_chunk: bytes) -> None:
        if self._closed.is_set() or self._ws is None:
            return
        msg = {
            "realtime_input": {
                "media_chunks": [{
                    "data": base64.b64encode(pcm_chunk).decode("ascii"),
                    "mime_type": f"audio/pcm;rate={self._cfg.sample_rate}"
                }]
            }
        }
        await self._ws.send(json.dumps(msg))

    async def end_audio(self) -> None:
        if self._ws:
            # Signal end of turn
            msg = {"client_content": {"turn_complete": True}}
            try:
                await self._ws.send(json.dumps(msg))
            except Exception:
                pass

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter():
            while True:
                ev = await self._events_q.get()
                if ev is None:
                    break
                yield ev
        return _aiter()

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                if self._closed.is_set():
                    break
                data = json.loads(raw)

                # Extract text from serverContent.modelTurn.parts
                server_content = data.get("serverContent", {})
                model_turn = server_content.get("modelTurn", {})
                for part in model_turn.get("parts", []):
                    text = part.get("text", "").strip()
                    if text:
                        is_final = server_content.get("turnComplete", False)
                        await self._events_q.put(TranscriptEvent(text=text, is_final=is_final))

        except websockets.ConnectionClosedOK:
            pass
        except Exception as exc:
            pass  # log in production
        finally:
            self._closed.set()
            await self._events_q.put(None)
```

**Note**: The Live API message schema above reflects the documented protocol as of March 2026. The `response_modalities`, `speech_config`, and `serverContent` field paths should be verified against the current API reference before finalising, as the Live API was still in rapid iteration when this was written.

### Dependencies

```
# requirements.txt — no new additions beyond google-generativeai
# websockets already used by all other providers
```

---

## Decision Matrix

| Criterion | Path A (Local VAD + Flash-Lite) | Path B (Flash-Live WebSocket) |
|---|---|---|
| End-to-end latency | ~0.6–1.5s/utterance (combined STT+reasoning) | ~200–500ms first word, streaming |
| Incremental partial results | No | Yes |
| Structured emotion/energy tags | Yes — per-utterance, JSON | No — text-level inference only |
| Replaces downstream LLM call | **Yes** — single combined call | No |
| Czech accuracy | Unknown — empirical test required | Unknown — empirical test required |
| Protocol fit | Full `RealtimeSttProvider` compliance | Full `RealtimeSttProvider` compliance |
| New dependencies | `webrtcvad` + `google-generativeai` | `google-generativeai` |
| New code | ~150 lines (1 provider file) | ~200 lines (1 provider file) |
| API stability risk | Preview only | Preview only |
| Use case fit | Best for turn-based conversation analysis | Best for live voice agents needing sub-second response |

**Suggested starting point**: Path A, because it directly collapses the existing two-call pipeline into one, delivers the emotion/energy tagging goal, and produces lower end-to-end latency. Path B adds value only if sub-utterance streaming latency becomes a hard requirement.

**Both paths can coexist** — they are independent provider implementations following the same protocol.

---

## Shared Implementation Steps (both paths)

1. **Empirical Czech accuracy test** — before writing provider code, validate that Gemini transcribes your Czech `assets/*.wav` files at acceptable accuracy. Create a simple test script:

```python
# scripts/test_gemini_czech.py (rough sketch)
import google.generativeai as genai
import base64, pathlib

genai.configure(api_key="...")
model = genai.GenerativeModel("gemini-3.1-flash-lite-preview")

for wav in pathlib.Path("assets").glob("*.wav"):
    audio = base64.b64encode(wav.read_bytes()).decode()
    r = model.generate_content([
        "Transcribe this Czech audio exactly.",
        {"mime_type": "audio/wav", "data": audio}
    ])
    print(wav.name, "→", r.text)
```

Compare output against the corresponding `.txt` files manually before investing in provider implementation.

2. **Add `GEMINI_API_KEY` to `.env`** — both paths use the same key. `GOOGLE_APPLICATION_CREDENTIALS` is separate (used only by the existing Google Cloud Speech provider).

3. **Add provider to `benchmark.py`** — follow the existing `build_provider_specs()` pattern. Both paths fit identically.

4. **Add test method to `tests/test_stt.py`** — follow the `self._runner()` pattern of existing tests.

---

## Appendix: Pure Batch Path (Considered, Not Pursued)

The `generateContent` batch API (no streaming, complete file upload → single response) was considered as a third path. It is straightforward to implement and offers the cheapest cost per hour (~$0.96 for Flash-Lite preview vs. ~$3.60 for ElevenLabs).

However, it is architecturally incompatible with the real-time library goal: the entire audio file must be available before the API call, making it useless for live microphone input. It cannot implement the `RealtimeSttProvider` protocol in any meaningful way.

It remains a valid option for **post-session analysis** (e.g., as a `custom_metric_fn` in `transcribe_and_diff()` to add batch emotion analysis to the HTML report after a session), but this is outside the scope of the core library and is not a substitute for either Path A or Path B.

---

## References

- [Google AI Docs — All Gemini Models](https://ai.google.dev/gemini-api/docs/models)
- [Gemini 3.1 Flash-Lite Preview — Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-1-flash-lite)
- [Gemini API — Audio Understanding](https://ai.google.dev/gemini-api/docs/audio)
- [Gemini Live API — Vertex AI Overview](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api)
- [Gemini 3.1 Flash Live Preview](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview)
- [Gemini API — Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Artificial Analysis — STT Leaderboard](https://artificialanalysis.ai/speech-to-text)
- [ElevenLabs — Czech Speech to Text](https://elevenlabs.io/speech-to-text/czech)