# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- **ElevenLabs Scribe v3 Live in the benchmark**: `ElevenLabsV3` runs `ElevenLabsSttConfig(model="scribe_v3_live")` with the same VAD settings as `ElevenLabs` (`scribe_v2_realtime`), and `tests/test_stt.py` has `test_eleven_labs_v3`.
- **`helpers/report_summary.py`**: averages benchmark TSVs per provider over the meaningful assets, those on which at least one of the given runs reached 40% WER or better (`--cutoff`).
- **`transcribe_and_diff(provider_name=...)`**: labels the HTML report. The benchmark passes its provider name, so runs of one provider class with different models stay distinguishable.
- **`input_volume_threshold`** on every STT config, default `None` (off): Speechmatics sends it as `audio_filtering_config.volume_threshold` on its 0-100 scale. The other providers accept it but do not map it yet; whether they support an equivalent is untested.
- **`base_url`** on `ElevenLabsSttConfig` and `ElevenLabsTtsConfig`, for a non-default API host.
- **`ElevenLabsTtsConfig.similarity_boost`**, default 0.75.
- **`benchmark.py --providers`** runs only the named providers, for example `--providers Speechmatics,Deepgram`.
- **`py.typed`** marks the package as typed.

### Changed

- **Provider SDK minimums**: `elevenlabs>=2.80.0`, `speechmatics-rt>=1.3.0`, `google-cloud-speech>=2.41.0` and `google-genai>=3.6.1`.
- **Gemini Live uses the `v1beta` API**: `GeminiLiveSttConfig.api_version` defaults to `v1beta`, where `gemini-3.1-flash-live-preview` lives; google-genai 3.x would otherwise call `v1` and get HTTP 404.
- **Semantic error rate uses `gemini-4-hydrogen`**: on a 25-row sample it tracks the previous `gemini-3.1-pro-preview` scores closely (correlation 0.97, about 1.4 points higher on average) at roughly a third of the cost.
- **`SpeechmaticsSttConfig.model`** replaces `operating_point` and reaches `TranscriptionConfig.model`, since `speechmatics-rt` deprecates `operating_point`. The default stays `"enhanced"`; `"standard"` selects the basic model.
- **Silence keepalive**: `stt_session_task` sends 100 ms of silence only after 5 s without audio, so pacing jitter never interleaves silence with speech.
- **Speechmatics unidentified speaker**: an event whose words are all labelled `UU` carries `speaker="??"`.
- **Deepgram partials**: interim results arrive as `TranscriptEvent(is_final=False)`.
- **Google SDK imported lazily**: `stt_provider_google` loads without the `google` extra, so the benchmark runs without it.
- **Core dependencies**: only `websockets`; `python-dotenv` comes with the `benchmark` and `dev` extras.
- **Benchmark TSV**: rewritten atomically after every finished file, with metric columns taken from all reports.
- **Semantic error rate retries**: the Gemini call is retried on HTTP 503 only.
- **`transcribe_and_diff()` pacing**: `chunk_ms`, `sample_rate`, `realtime_factor` and `silence_s` are required keyword arguments.

### Removed

- **`semantic` extra** and `requirements.txt`: install with extras, for example `pip install -e ".[gemini]"`.
- **`GoogleSttConfig.encoding`**: the provider always sends LINEAR16.
- **`config.AUDIO_SAMPLE_WIDTH_BYTES`**.
- **`SttEventQueue.get_nowait()`, `empty()`, `put_sentinel_async()` and the `maxsize` argument**; `finish_from_thread()` ends a stream from a worker thread.
- **`helpers/collect_reports.py`**.

### Fixed

- **SER column in the benchmark TSV** is kept when the first report has no semantic metric.
- **Benchmark timeouts** cancel the STT session instead of leaving it streaming.
- **Failed transcript ingest** raises instead of scoring a partial transcript.
- **End-of-stream sentinel** waits for room on a full event queue instead of being dropped.

## [0.3.0] - 2026-09-19

### Added

- **`optimize_streaming_latency=3` on `eleven_multilingual_*`**: measured through the provider on a 313-character Czech reply, first byte lands at 0.876 s with the level against 3.76 s without. The parameter is omitted for every other model, since `eleven_v3_conversational` rejects it with HTTP 400 `unsupported_model` and on the fast models the gain is too small to spend a deprecated parameter on (turbo 0.390 s to 0.296 s, flash 0.192 s to 0.183 s).
- **`SpeechmaticsSttConfig.max_delay_mode`**: reaches `TranscriptionConfig.max_delay_mode`. Its default is `None`, which leaves the server on `flexible`, where `enable_entities` may push a final transcript past `max_delay`. `"fixed"` holds the delay instead.
- **`tests/test_tts.py`**: one live synthesis per ElevenLabs model, asserting a plausible duration, audio rather than silence, and that a 50 ms ticker alongside it never misses a beat by more than 100 ms.

### Changed

- **ElevenLabs TTS is fully async**: `synthesize()` uses `AsyncElevenLabs` and the `/stream` endpoint consumed with `async for`, in place of the synchronous client and the `convert` endpoint. The event loop is no longer held for the duration of a synthesis, and first byte arrives earlier on long replies.
- **One client per credential and loop**: the `AsyncElevenLabs` instance is cached at module level by `(api_key, base_url, running loop)`, so a provider built per utterance no longer pays a TLS handshake each time, and no pool outlives the loop that opened it.

### Fixed

- **Speechmatics regional endpoint**: `SpeechmaticsSttConfig.base_url` is passed to the SDK client, so it selects the realtime region. Its default is `None`, which defers to the SDK (`SPEECHMATICS_RT_URL`, then the SDK's EU host).

## [0.2.0] - 2026-05-08

### Added

- **TTS abstraction**: `RealtimeTtsProvider` protocol (`tts_provider.py`) and ElevenLabs TTS implementation (`tts_provider_elevenlabs.py`)
- **Diarization support**: `TranscriptEvent.speaker` field (optional, `str | None`)
- **Speechmatics diarization**: majority-vote speaker extraction from word-level results, `UU` label filtering
- **Silence keepalive**: `stt_session_task` sends 100ms silence when no audio arrives within 200ms, preventing provider timeouts
- **Unit test suite**: `tests/test_unit.py`, mock-based tests covering TranscriptEvent, stt_session_task orchestration, transcript_ingest_task, protocol compliance, ElevenLabs/Speechmatics callback logic
- **Optional dependencies**: per-provider extras in `pyproject.toml` (`elevenlabs`, `speechmatics`, `google`, `gemini`, `benchmark`, `all`)

### Changed

- **Package rename**: `lib/` -> `universal_realtime_stt_tts/`
- **Project rename**: `universal-realtime-stt` -> `universal-realtime-stt-tts`
- **ElevenLabs STT**: rewritten from raw WebSocket to official `elevenlabs` SDK with callback-based events
- **Speechmatics STT**: rewritten from raw WebSocket to official `speechmatics-rt` SDK with decorator-based events
- **Class renames**: `ElevenLabsRealtimeProvider` -> `ElevenLabsSttProvider`, `SpeechmaticsRealtimeProvider` -> `SpeechmaticsSttProvider`
- **Provider configs import from central `config.py`**: defaults for sample rate, language, VAD thresholds come from `config.py` (matching the original design)
- **`utils.py` decoupled**: `setup_logging()` accepts `log_dir` parameter instead of importing `LOG_PATH`
- **`transcript_queue` type**: `Queue[str | None]` -> `Queue[TranscriptEvent | None]`; both partial and final events are now routed through the queue
- **Core dependencies trimmed**: only `websockets` and `python-dotenv` are required; provider SDKs are optional extras

### Fixed

- **Google provider hang**: `__aexit__` now has a 30s timeout on the streaming thread to prevent indefinite blocking
- **Speechmatics provider hang**: `__aexit__` now has a 10s timeout on SDK cleanup; `end_session()` errors are caught gracefully

## [0.1.0]

Initial release with ElevenLabs, Deepgram, Google, Speechmatics, Cartesia, and Gemini Live STT providers.
