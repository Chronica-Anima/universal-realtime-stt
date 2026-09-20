# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
- **Unit test suite**: `tests/test_unit.py` — mock-based tests covering TranscriptEvent, stt_session_task orchestration, transcript_ingest_task, protocol compliance, ElevenLabs/Speechmatics callback logic
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
