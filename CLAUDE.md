# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multiprovider realtime speech-to-text and text-to-speech library with unified async interface. Includes a benchmark/testing framework that validates STT provider accuracy by streaming WAV audio files and comparing transcribed output against ground-truth transcripts. Test audio is in Czech.

## Commands

```bash
# Setup
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"

# Unit tests (no API keys needed)
pytest tests/test_unit.py -v

# Run all provider integration tests (requires API keys)
pytest tests/test_stt.py -v

# Run a single provider test
pytest tests/test_stt.py::TestStt::test_eleven_labs -v
pytest tests/test_stt.py::TestStt::test_google -v
pytest tests/test_stt.py::TestStt::test_deepgram -v
pytest tests/test_stt.py::TestStt::test_speechmatics -v
pytest tests/test_stt.py::TestStt::test_cartesia -v

# Speechmatics with LLM semantic understanding metric (requires GEMINI_API_KEY + google-genai)
pytest tests/test_stt.py::TestStt::test_speechmatics_semantics -v

# Diff report and LLM metric unit tests
pytest tests/test_diff.py -v

# Run benchmark (all providers in parallel, TSV report)
python benchmark.py
```

## Environment Variables

Provider API keys in `.env`:
- `ELEVENLABS_API_KEY` — ElevenLabs (STT + TTS)
- `DEEPGRAM_API_KEY` — Deepgram
- `SPEECHMATICS_API_KEY` — Speechmatics
- `CARTESIA_API_KEY` — Cartesia
- `GOOGLE_APPLICATION_CREDENTIALS` — Path to Google service account JSON (uses ADC)
- `GEMINI_API_KEY` — Optional. Enables the Gemini Live STT provider, the semantic understanding metric in `benchmark.py`, and `test_speechmatics_semantics`. Requires `google-genai` to be installed.

## Architecture

The system uses async/await throughout with queue-based communication between components.

### `universal_realtime_stt_tts/` — Core library

**STT provider protocol** (`stt_provider.py`): Defines `RealtimeSttProvider` protocol and `TranscriptEvent` dataclass (with `text`, `is_final`, and optional `speaker` field for diarization). New providers implement this protocol via structural typing (no inheritance needed).

**TTS provider protocol** (`tts_provider.py`): Defines `RealtimeTtsProvider` protocol with `synthesize(text, language) -> AsyncIterator[bytes]` yielding PCM chunks.

**STT provider implementations** (`stt_provider_*.py`): Each provider has its own module with a frozen config dataclass and a class implementing the `RealtimeSttProvider` protocol.
- **ElevenLabs** — uses `elevenlabs` SDK with callback-based events
- **Speechmatics** — uses `speechmatics-rt` SDK with diarization support (speaker field via majority-vote extraction)
- **Google** — uses `google-cloud-speech` SDK
- **Gemini Live** — uses `google-genai` SDK
- **Deepgram** — direct WebSocket
- **Cartesia** — direct WebSocket

**TTS provider implementations** (`tts_provider_elevenlabs.py`): ElevenLabs TTS using `elevenlabs` SDK.

**Session orchestration** (`stt.py`): `stt_session_task()` runs two concurrent async tasks — a sender (audio queue -> provider, with silence keepalive) and a receiver (provider events -> transcript queue). Both partial and final events are routed through the queue. Queues use `None` sentinels to signal completion.

**Shared event queue** (`_event_queue.py`): `SttEventQueue` encapsulates the queue + error + sentinel + recv-guard pattern shared across providers. Provider classes compose it instead of duplicating boilerplate. Handles WebSocket close exceptions, cancellation, and ensures a single sentinel reaches the consumer.

### `helpers/` — Test and benchmark support

**Transcribe + diff pipeline** (`helpers/transcribe.py`): `transcribe_and_diff()` ties everything together — streams audio, collects transcripts, compares against ground truth, writes HTML diff report. Accepts an optional `custom_metric_fn` for plugging in additional metrics (e.g. semantic understanding).

**WAV streaming** (`helpers/stream_wav.py`): Reads WAV files, yields PCM chunks with realistic timing pacing, and appends silence padding to ensure VAD commits the final utterance.

**Diff reports** (`helpers/diff_report.py`): `DiffReport` dataclass — generates HTML diff reports and calculates Levenshtein distance-based CER and WER.

**Transcript ingest** (`helpers/transcript_ingest.py`): Collects `TranscriptEvent` objects from the transcript queue, filtering for `is_final` events only.

**Test assets** (`assets/`): WAV/TXT file pairs where the TXT contains the expected transcript. Audio must be PCM 16kHz, mono, 16-bit. Convert with:
```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## Test Output

- **HTML diffs** in `out/` — visual comparison of expected vs actual transcripts
- **Logs** in `log/` — DEBUG for project code (`universal_realtime_stt_tts.*`), INFO for third-party libraries
- **TSV reports** in `out/` — benchmark results with per-provider, per-file CER/WER metrics

## Configuration

`universal_realtime_stt_tts/config.py` defines audio parameters (16kHz, mono, PCM16LE), VAD settings, and streaming parameters (200ms chunks). Provider config dataclasses import their defaults from `config.py` so that changing a parameter (e.g. VAD silence threshold) in one place propagates to all providers.

`pytest_config.py` (project root) holds paths and pacing for the test suite and benchmark — `OUT_PATH`, `ASSETS_DIR`, `LOG_PATH`, `CHUNK_MS`, `TEST_REALTIME_FACTOR`, `FINAL_SILENCE_S`. Library code never imports it; only tests and `benchmark.py` do.

- Language: `cs` (ISO 639-1) / `cs-CZ` (BCP-47, used by Google)
- Audio: 16kHz sample rate, mono, 16-bit PCM (`pcm_s16le`)
- Streaming: 200ms chunks, 1.0x realtime factor, 2s final silence padding

## Design Principles

- **SDK-first for providers with official SDKs** — ElevenLabs and Speechmatics use their official Python SDKs. Deepgram and Cartesia use direct WebSocket. Google and Gemini use their respective Google SDKs.
- **Central config, per-provider mapping** — provider config dataclasses import defaults from `config.py` (sample rate, language, VAD thresholds). Each provider maps these to its own parameter names and units. API keys are injected at instantiation time.
- **Queue-based IPC** — audio and transcript queues decouple streaming from processing. `None` sentinels signal end-of-stream.
- **Optional dependencies** — provider SDKs are optional extras in `pyproject.toml`. The library core only requires `websockets` and `python-dotenv`.

## Adding a New STT Provider

1. Create `universal_realtime_stt_tts/stt_provider_<name>.py` with:
   - A frozen `@dataclass` config class (API key + provider-specific settings with literal defaults)
   - A class implementing the `RealtimeSttProvider` protocol from `universal_realtime_stt_tts/stt_provider.py`
   - The protocol requires: `async __aenter__`/`__aexit__`, `send_audio(bytes)`, `end_audio()`, `events() -> AsyncIterator[TranscriptEvent]`
2. Most providers use an internal `asyncio.Queue[TranscriptEvent | None]` fed by a background listener, with `events()` draining it
3. Add a test method in `tests/test_stt.py` following the pattern of existing tests
4. Add a benchmark entry in `benchmark.py` (`build_provider_specs()`)
5. Add the API key env var to `.env`

## Adding a New TTS Provider

1. Create `universal_realtime_stt_tts/tts_provider_<name>.py` with:
   - A frozen `@dataclass` config class
   - A class implementing `RealtimeTtsProvider` from `universal_realtime_stt_tts/tts_provider.py`
   - The protocol requires: `async def synthesize(text, language) -> AsyncIterator[bytes]`

## Optional: Semantic Understanding Metric

To enable the LLM-based SER metric:

1. Add `GEMINI_API_KEY=<key>` to `.env`
2. Install `google-genai`: `pip install google-genai`

If the key is set but the package is missing, `benchmark.py` logs a clear warning and runs without the metric. See `doc/semantic_understanding_metric.md` for details.
