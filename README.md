# universal-realtime-stt-tts

Provider-agnostic realtime speech-to-text (STT) and text-to-speech (TTS) for Python, built around a uniform `async`/`await` interface.

Wire up one async task, push PCM chunks into an input queue, and consume `TranscriptEvent`s from an output queue. Swap providers without changing the surrounding code.

## Features

- **Realtime STT** across Cartesia (Ink-Whisper), Deepgram (Nova-3), ElevenLabs (Scribe v2 Realtime and Scribe v3 Live), Google Cloud Speech-to-Text, Speechmatics and Gemini Live.
- **Realtime TTS** via ElevenLabs, streamed as PCM.

## Installation

Requires Python 3.10+.

```bash
pip install universal-realtime-stt-tts                          # core: Cartesia and Deepgram
pip install "universal-realtime-stt-tts[elevenlabs]"            # ElevenLabs SDK
pip install "universal-realtime-stt-tts[speechmatics]"          # Speechmatics SDK
pip install "universal-realtime-stt-tts[google]"                # google-cloud-speech
pip install "universal-realtime-stt-tts[gemini]"                # google-genai (Gemini Live and the SER metric)
pip install "universal-realtime-stt-tts[all]"                   # every provider SDK plus benchmark helpers
```

The core depends only on `websockets`. Cartesia and Deepgram speak raw WebSocket and work from the core install; the other providers need their SDK extra.

### Provider credentials

API keys are passed to each provider's config dataclass at instantiation. The library does not read environment variables itself, so load them however you prefer (`python-dotenv`, OS env, a secret manager).

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

Switching providers changes the import and the config:

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
    speaker: str | None = None
```

Partial events (`is_final=False`) carry the recognizer's current hypothesis; Deepgram, ElevenLabs, Google and Speechmatics emit them, Cartesia and Gemini Live emit finals only. Final events (`is_final=True`) are committed segments. `speaker` is the raw diarization label, filled by Speechmatics (`"??"` when the speaker is unidentified) and `None` elsewhere.

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

`synthesize()` streams through the async ElevenLabs client, so it never blocks the event loop.

## How it works

`stt_session_task()` runs two concurrent tasks for the duration of a session:

- a sender that forwards PCM chunks from `audio_queue` to the provider and calls `end_audio()` on a `None` chunk. If no audio arrives for 5 s, it sends 100 ms of silence so the provider does not close an idle session;
- a receiver that drains the provider's events into `transcript_queue`, skipping empty text, and pushes `None` when the provider closes.

Audio is 16 kHz, mono, 16-bit signed little-endian PCM. To convert other formats:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## Overriding defaults

Defaults live in `universal_realtime_stt_tts.config` (language, VAD thresholds, sample rate, input volume threshold). Each provider's config dataclass takes them as defaults and maps them to the provider's own parameter names; override any of them per instance:

```python
from universal_realtime_stt_tts.stt_provider_speechmatics import SpeechmaticsSttConfig

config = SpeechmaticsSttConfig(
    api_key="...",
    language="en",
    model="enhanced",  # or "standard"
    diarization="speaker",
    base_url="wss://us.rt.speechmatics.com/v2",  # regional endpoint; omit for the SDK default
    input_volume_threshold=0.2,  # silence input below this level, 0-100 scale
)
```

`base_url` pins the Speechmatics realtime region (`eu`, `us`, `au`, or `global`). When it is `None`, the `speechmatics-rt` SDK picks the endpoint itself: `SPEECHMATICS_RT_URL` if set, otherwise its EU default.

`input_volume_threshold` is in every STT config and is off by default. Only Speechmatics maps it so far, as `audio_filtering_config.volume_threshold`; the other providers accept and ignore it.

## Implementing a new provider

An STT provider satisfies the `RealtimeSttProvider` protocol structurally, without inheriting:

```python
class RealtimeSttProvider(Protocol):
    async def __aenter__(self) -> "RealtimeSttProvider": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def send_audio(self, pcm_chunk: bytes) -> None: ...
    async def end_audio(self) -> None: ...
    def events(self) -> AsyncIterator[TranscriptEvent]: ...
```

The module docstring of [`universal_realtime_stt_tts/stt_provider.py`](universal_realtime_stt_tts/stt_provider.py) is the template: lifecycle, config dataclass, class skeleton and the files to update. Import a provider SDK inside the provider so the module loads without its extra, and add the SDK as an optional extra in `pyproject.toml`.

A TTS provider is `universal_realtime_stt_tts/tts_provider_<name>.py` with a frozen config dataclass and a class implementing `RealtimeTtsProvider`: `synthesize(text, language)` yields PCM chunks.

## Optional: Semantic Error Rate (SER)

Besides WER and CER, the benchmark and its `transcribe_and_diff()` helper accept a custom metric callback. The repository ships one that uses Gemini to extract subject/predicate/object facts from the expected and produced transcripts and scores how much of the expected meaning survived:

```
SER = facts_missing / (facts_both + facts_missing) * 100
```

Lower is better, as with WER and CER. Enable it by setting `GEMINI_API_KEY` and installing the `[gemini]` extra. See [`doc/semantic_understanding_metric.md`](doc/semantic_understanding_metric.md) for the details and how to plug in your own metric.

## Repository extras

The repository, not the published wheel, ships tooling for evaluating providers:

- `benchmark.py` runs every configured provider in parallel against a directory of WAV/TXT pairs and writes a TSV report plus per-file HTML diffs.
- `tests/test_unit.py` covers the core protocol and orchestration with mocks, with no API keys needed. `tests/test_stt.py` holds a live test per STT provider, `tests/test_tts.py` a live synthesis per ElevenLabs model, and `tests/test_diff.py` the diff report and LLM metric.

```bash
pytest tests/test_unit.py -v                      # offline
pytest tests/test_stt.py::TestStt::test_deepgram  # one provider, live
python benchmark.py --providers Deepgram          # one provider, billed
```

## License

MIT. See [LICENSE](LICENSE).

## Links

- Source and issues: <https://github.com/Chronica-Anima/universal-realtime-stt-tts>
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
