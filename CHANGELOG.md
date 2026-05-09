# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
- **Provider configs decoupled from `config.py`**: each config dataclass uses literal defaults instead of importing shared constants
- **`utils.py` decoupled**: `setup_logging()` accepts `log_dir` parameter instead of importing `LOG_PATH`
- **`transcript_queue` type**: `Queue[str | None]` -> `Queue[TranscriptEvent | None]`; both partial and final events are now routed through the queue
- **Core dependencies trimmed**: only `websockets` and `python-dotenv` are required; provider SDKs are optional extras

## [0.1.0]

Initial release with ElevenLabs, Deepgram, Google, Speechmatics, Cartesia, and Gemini Live STT providers.
