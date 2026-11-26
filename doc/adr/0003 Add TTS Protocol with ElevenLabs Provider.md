---
status: Accepted
date: 2026-05-08
---

# Add TTS Protocol with ElevenLabs Provider

## Context and Problem Statement

The consuming application needs TTS as well as STT. Keeping both audio directions in one library shares provider management, config patterns and dependency wiring.

## Decision Outcome

`RealtimeTtsProvider` defines one method, `synthesize(text, language) -> AsyncIterator[bytes]`, yielding raw PCM 16-bit LE mono chunks at the library sample rate.

ElevenLabs TTS streams through `AsyncElevenLabs.text_to_speech.stream`, so synthesis never blocks the event loop. The client is cached at module level per API key, base URL and running loop: callers build a provider per utterance and still share one connection pool, and no pool outlives the loop that opened it.

Speechmatics TTS is not implemented because it does not support Czech.

## Consequences

- TTS providers follow the STT pattern: a frozen config dataclass plus a protocol implementation.
- The `elevenlabs` SDK serves both STT and TTS.
