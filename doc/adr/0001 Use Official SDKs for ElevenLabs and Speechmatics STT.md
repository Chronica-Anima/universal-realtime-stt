---
status: Accepted
date: 2026-05-08
---

# Use Official SDKs for ElevenLabs and Speechmatics STT

## Context and Problem Statement

ElevenLabs and Speechmatics STT providers were implemented with raw `websockets` — manual URL construction, JSON serialization, authentication headers, and message parsing. This worked but meant maintaining protocol-level code that the vendor can change without notice. Adding features like diarization (Speechmatics) required implementing non-trivial protocol extensions manually.

## Considered Options

- Keep raw WebSocket implementations and add features manually
- Switch to official Python SDKs (`elevenlabs`, `speechmatics-rt`)

## Decision Outcome

Switch to official SDKs.

- ElevenLabs STT: `websockets` replaced by `elevenlabs` SDK (`client.speech_to_text.realtime.connect()` with callback-based events)
- Speechmatics STT: `websockets` replaced by `speechmatics-rt` SDK (`AsyncClient` with decorator-based events and direct `send_audio(bytes)`)

The `RealtimeSttProvider` protocol, `TranscriptEvent` dataclass, and queue-based architecture stay the same — only the internal wiring changes.

## Consequences

- Diarization support in Speechmatics becomes straightforward (SDK exposes speaker labels on word-level results)
- ElevenLabs and Speechmatics become optional dependencies (`elevenlabs`, `speechmatics-rt` extras in `pyproject.toml`)
- Protocol changes are handled by SDK maintainers
- Speechmatics SDK requires `bytes` (not `bytearray`) for `send_audio()` — provider casts explicitly
