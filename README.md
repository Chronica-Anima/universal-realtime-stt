# universal-realtime-stt-tts

Provider-agnostic realtime speech-to-text (STT) and text-to-speech (TTS) for Python, built around a uniform `async`/`await` interface.

Wire up one async task, push PCM chunks into an input queue, and consume `TranscriptEvent`s from an output queue. Swap providers without changing the surrounding code.

## Features

- **Realtime STT** across multiple providers: Cartesia (Ink-Whisper), Deepgram (Nova-3), ElevenLabs (Scribe v2 Realtime), Google Cloud Speech-to-Text, Speechmatics, and Gemini Live.
- **Realtime TTS** via ElevenLabs (PCM stream output).
- **Unified async protocol** (`RealtimeSttProvider`, `RealtimeTtsProvider`) using `typing.Protocol`. Implement a new provider by satisfying the methods, no inheritance required.
- **Queue-based streaming**: audio in, `TranscriptEvent` out. `None` sentinels signal end-of-stream.
- **Partial and final transcripts** are both delivered; the consumer decides which to act on.
- **Speaker diarization** field on `TranscriptEvent` (populated by Speechmatics today).
- **Central config** (`config.py`) keeps sample rate, language, and VAD thresholds in one place; each provider maps them to its native parameter names.
- **Optional dependencies**: the core only requires `websockets` and `python-dotenv`. Install per-provider extras as needed.

## Installation

Requires Python 3.10+.

```bash
pip install universal-realtime-stt-tts                          # core (Cartesia + Deepgram usable already)
pip install "universal-realtime-stt-tts[elevenlabs]"            # pulls the ElevenLabs SDK
pip install "universal-realtime-stt-tts[speechmatics]"          # pulls the Speechmatics SDK
pip install "universal-realtime-stt-tts[google]"                # pulls google-cloud-speech
pip install "universal-realtime-stt-tts[gemini]"                # pulls google-genai (Gemini Live + SER metric)
pip install "universal-realtime-stt-tts[all]"                   # every provider SDK + benchmark helpers
```

Cartesia and Deepgram are reached over raw WebSocket and need no provider SDK; they work from the core install. ElevenLabs, Speechmatics, Google, and Gemini Live each require their official Python SDK, pulled in by the corresponding extra.

### Provider credentials

API keys are supplied to each provider's config dataclass at instantiation. The library does not read environment variables itself; pick whichever loader you prefer (`python-dotenv`, OS env, secret manager).

| Provider     | Credential                                                  |
| ------------ | ----------------------------------------------------------- |
| Cartesia     | API key                                                     |
| Deepgram     | API key                                                     |
| ElevenLabs   | API key                                                     |
| Google       | `GOOGLE_APPLICATION_CREDENTIALS` env var (ADC service JSON) |
| Speechmatics | API key                                                     |
| Gemini Live  | API key                                                     |

## Quick start: STT

```python
import asyncio
from universal_realtime_stt_tts.stt import stt_session_task
from universal_realtime_stt_tts.stt_provider import TranscriptEvent
from universal_realtime_stt_tts.stt_provider_deepgram import (
    DeepgramRealtimeProvider, DeepgramSttConfig,
)


async def main():
    provider = DeepgramRealtimeProvider(DeepgramSttConfig(api_key="..."))

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=40)
    transcript_queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(maxsize=200)
    running = asyncio.Event()
    running.set()

    session = asyncio.create_task(
        stt_session_task(provider, audio_queue, transcript_queue, running)
    )

    async def feed_audio():
        # PCM 16-bit LE, 16 kHz, mono, 200 ms chunks (6400 bytes)
        with open("speech.pcm", "rb") as f:
            while chunk := f.read(6400):
                await audio_queue.put(chunk)
        await audio_queue.put(None)  # signal end of audio

    asyncio.create_task(feed_audio())

    while (event := await transcript_queue.get()) is not None:
        if event.is_final:
            print(event.text)

    await session


asyncio.run(main())
```

The same code switches providers by changing two imports and the config:

```python
from universal_realtime_stt_tts.stt_provider_speechmatics import (
    SpeechmaticsSttProvider, SpeechmaticsSttConfig,
)

provider = SpeechmaticsSttProvider(SpeechmaticsSttConfig(api_key="..."))
```

### `TranscriptEvent`

```python
@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    speaker: str | None = None  # populated by providers that support diarization
```

Partial events (`is_final=False`) are emitted as the recognizer updates its hypothesis. Final events (`is_final=True`) are committed segments.

## Quick start: TTS

```python
import asyncio
from universal_realtime_stt_tts.tts_provider_elevenlabs import (
    ElevenLabsTtsProvider, ElevenLabsTtsConfig,
)


async def main():
    provider = ElevenLabsTtsProvider(ElevenLabsTtsConfig(api_key="..."))
    async for pcm_chunk in provider.synthesize("Hello, world.", language="en"):
        # pcm_chunk is raw 16 kHz PCM ready to play or persist
        ...


asyncio.run(main())
```

## How it works

`stt_session_task()` runs two concurrent tasks for the duration of a session:

- a sender that pulls PCM chunks from `audio_queue` and forwards them to the provider, injecting 100 ms of silence when no audio arrives within 200 ms (keeps providers from timing out during pauses);
- a receiver that drains the provider's event stream into `transcript_queue` and pushes `None` when the provider closes.

Audio format is fixed at the streaming layer: 16 kHz, mono, 16-bit signed little-endian PCM. To convert other formats:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## Overriding defaults

Defaults live in `universal_realtime_stt_tts.config` (language, VAD thresholds, sample rate). Each provider's config dataclass reads from those values but can be overridden per-instance:

```python
from universal_realtime_stt_tts.stt_provider_speechmatics import SpeechmaticsSttConfig

config = SpeechmaticsSttConfig(
    api_key="...",
    language="en",
    operating_point="enhanced",
    diarization="speaker",
)
```

## Implementing a new provider

The protocol is structural (`typing.Protocol`):

```python
class RealtimeSttProvider(Protocol):
    async def __aenter__(self) -> "RealtimeSttProvider": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def send_audio(self, pcm_chunk: bytes) -> None: ...
    async def end_audio(self) -> None: ...
    def events(self) -> AsyncIterator[TranscriptEvent]: ...
```

See `universal_realtime_stt_tts/stt_provider.py` for the full lifecycle docstring and a code skeleton, and any of the `stt_provider_*.py` modules for a working reference.

## Optional: Semantic Error Rate (SER)

In addition to standard WER and CER, the bundled benchmark and `transcribe_and_diff()` helper accept a custom metric callback. The repository ships one implementation that uses Gemini to extract subject/predicate/object facts from both the expected and produced transcripts, then scores how much of the expected meaning survived:

```
SER = facts_missing / (facts_both + facts_missing) * 100
```

Lower is better, same convention as WER and CER. Enable it by setting `GEMINI_API_KEY` and installing the `[gemini]` (or `[semantic]`) extra. See [`doc/semantic_understanding_metric.md`](doc/semantic_understanding_metric.md) for the data model and how to plug in your own custom metric.

## Repository extras

The repository (not the published wheel) ships additional tooling for evaluating providers:

- `benchmark.py` runs every configured provider in parallel against a directory of WAV/TXT pairs and writes a TSV report plus per-run HTML diffs.
- `tests/test_stt.py` contains end-to-end smoke tests for each provider; `tests/test_unit.py` covers the core protocol and orchestration with mocks (no API keys required).
- `tests/test_diff.py` exercises the diff report and LLM metric.

```bash
pytest tests/test_unit.py -v                      # offline
pytest tests/test_stt.py::TestStt::test_deepgram  # one provider
python benchmark.py                               # all configured providers
```

## License

MIT. See [LICENSE](LICENSE).

## Links

- Source and issues: <https://github.com/Chronica-Anima/universal-realtime-stt-tts>
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)