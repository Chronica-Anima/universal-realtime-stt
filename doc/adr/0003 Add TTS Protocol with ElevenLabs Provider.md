---
status: Accepted
date: 2026-05-08
---

# Add TTS Protocol with ElevenLabs Provider

## Context and Problem Statement

The library handled only STT. The consuming application needed TTS and was going to implement it separately. Having both audio directions in one library avoids duplicating provider management, config patterns, and dependency wiring.

## Considered Options

- Keep TTS in the application layer
- Add `RealtimeTtsProvider` protocol to this library with an ElevenLabs implementation

## Decision Outcome

Add TTS protocol and ElevenLabs provider.

`RealtimeTtsProvider` defines a single method: `async def synthesize(text, language) -> AsyncIterator[bytes]`, yielding raw PCM 16-bit LE, 16 kHz mono chunks. The provider manages its own connection lifecycle per call.

ElevenLabs TTS uses the `elevenlabs` SDK (`client.text_to_speech.convert()` with `output_format="pcm_16000"`).

Speechmatics TTS was considered but is English-only — not implemented until Czech support is available.

## Consequences

- Library becomes `universal-realtime-stt-tts` (renamed from `universal-realtime-stt`)
- TTS providers follow the same pattern as STT: frozen config dataclass + protocol implementation
- `elevenlabs` SDK serves both STT and TTS
