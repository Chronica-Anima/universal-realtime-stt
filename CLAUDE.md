# universal-realtime-stt-tts

A multiprovider realtime speech-to-text and text-to-speech library behind one async interface, plus a benchmark that streams Czech WAV recordings to each STT provider and scores the transcripts against ground truth.

## Setup

```bash
git submodule update --init        # assets/ is a separate repository
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

Provider credentials go in `.env`:

| Variable                         | Used by                                                                               |
| -------------------------------- | ------------------------------------------------------------------------------------- |
| `ELEVENLABS_API_KEY`             | ElevenLabs STT and TTS                                                                |
| `DEEPGRAM_API_KEY`               | Deepgram                                                                              |
| `SPEECHMATICS_API_KEY`           | Speechmatics                                                                          |
| `CARTESIA_API_KEY`               | Cartesia                                                                              |
| `GOOGLE_APPLICATION_CREDENTIALS` | Google, as a path to a service-account JSON (ADC)                                     |
| `GEMINI_API_KEY`                 | Gemini Live STT and the semantic understanding metric; needs `google-genai` installed |

## Commands

```bash
pytest tests/test_unit.py -v                          # no keys, no assets
pytest tests/test_diff.py -v -k "not llm"             # diff and metrics; the llm test needs Gemini
pytest tests/test_stt.py -v                           # every STT provider, live
pytest tests/test_stt.py::TestStt::test_deepgram -v   # one provider
pytest tests/test_tts.py -v                           # ElevenLabs TTS, live
python benchmark.py                                   # all configured providers in parallel
python benchmark.py --providers Speechmatics,Deepgram
python -m helpers.collect_reports                     # TSV summary of the HTML diffs in out/
```

The provider tests are `test_cartesia`, `test_deepgram`, `test_eleven_labs`, `test_google`, `test_speechmatics`, `test_gemini_live` and `test_speechmatics_semantics`. A live test with a missing key or package fails rather than skips. The benchmark skips a provider whose key is missing; its names are `Cartesia`, `Deepgram`, `ElevenLabs`, `Google`, `Speechmatics` and `GeminiLive`.

Output: HTML diffs and `<timestamp>_benchmark.tsv` in `out/`, logs in `log/` (DEBUG for `universal_realtime_stt_tts.*`, INFO for third-party libraries).

## Architecture

Everything is async, and components talk through queues that end with a `None` sentinel.

### `universal_realtime_stt_tts/` — the library

- `stt_provider.py` — the `RealtimeSttProvider` protocol and the frozen `TranscriptEvent` (`text`, `is_final`, optional `speaker` for diarization). Providers satisfy the protocol structurally, without inheriting.
- `tts_provider.py` — the `RealtimeTtsProvider` protocol: `synthesize(text, language)` yields PCM chunks.
- `stt_provider_<name>.py` — one module per provider, each with a frozen config dataclass and a provider class.
- `_event_queue.py` — `SttEventQueue`, which every STT provider composes: event queue, error state, a single sentinel, and `recv_guard()` for WebSocket close and cancellation handling.
- `stt.py` — `stt_session_task()` runs a sender (audio queue to provider, with silence keepalive) and a receiver (provider events, partial and final, to the transcript queue) concurrently.
- `tts_provider_elevenlabs.py` — ElevenLabs TTS through `AsyncElevenLabs` and the `/stream` endpoint; the client is cached per API key, base URL and running loop.
- `config.py` — central defaults: language `cs` / `cs-CZ` (BCP-47, for Google), 16 kHz mono PCM16LE audio, VAD thresholds.

Provider config dataclasses take their defaults from `config.py`, and each provider maps them to its own parameter names and units, so a change there reaches every provider. API keys are injected at instantiation.

| Provider     | Transport                                         |
| ------------ | ------------------------------------------------- |
| ElevenLabs   | `elevenlabs` SDK, callback-based                  |
| Speechmatics | `speechmatics-rt` SDK, speaker by majority vote   |
| Google       | `google-cloud-speech` SDK                         |
| Gemini Live  | `google-genai` SDK, preview model                 |
| Deepgram     | raw WebSocket                                     |
| Cartesia     | raw WebSocket                                     |

The core depends only on `websockets` and `python-dotenv`; each provider SDK is an optional extra in `pyproject.toml`.

### `helpers/` — tests and benchmark

- `transcribe.py` — `transcribe_and_diff()` streams a file, collects final transcripts, diffs them against ground truth and writes the HTML report; `custom_metric_fn` adds a metric such as semantic understanding.
- `stream_wav.py` — paces PCM chunks in real time and appends silence so the VAD commits the last utterance.
- `diff_report.py` — `DiffReport`: HTML report and Levenshtein-based CER and WER.
- `transcript_ingest.py` — collects the `is_final` events from the transcript queue.
- `semantic_understanding.py`, `llm_api.py` — the Gemini-based semantic error rate metric, described in `doc/semantic_understanding_metric.md`. Without `google-genai` the benchmark warns and runs without it.

`pytest_config.py` holds the test and benchmark paths and pacing (`OUT_PATH`, `ASSETS_DIR`, `LOG_PATH`, `CHUNK_MS`, `TEST_REALTIME_FACTOR`, `FINAL_SILENCE_S`). Library code never imports it.

Assets in `assets/` are WAV/TXT pairs, the TXT holding the expected transcript. Audio is PCM 16 kHz, mono, 16-bit:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

## Adding a provider

An STT provider is `universal_realtime_stt_tts/stt_provider_<name>.py` with a frozen config dataclass (API key plus provider settings, defaults from `config.py`) and a class implementing `async __aenter__`/`__aexit__`, `send_audio(bytes)`, `end_audio()` and `events() -> AsyncIterator[TranscriptEvent]`, backed by `SttEventQueue`. ADRs 0001 and 0004 in `doc/adr/` record when a provider uses its SDK and when a raw WebSocket. Then add a test in `tests/test_stt.py`, an entry in `build_provider_specs()` in `benchmark.py`, and its key to the README's credential list.

A TTS provider is `tts_provider_<name>.py` with a frozen config dataclass and a class implementing `RealtimeTtsProvider`.

## Conventions

- User-visible changes go under `[Unreleased]` in `CHANGELOG.md` (Keep a Changelog)
- Architecture decisions are MADR records in `doc/adr/`
- Conventional Commits: `type: description`, lowercase
