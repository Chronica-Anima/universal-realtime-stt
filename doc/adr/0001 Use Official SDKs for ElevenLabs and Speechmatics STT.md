---
status: Accepted
date: 2026-05-08
---

# Use Official SDKs for ElevenLabs and Speechmatics STT

## Context and Problem Statement

Raw WebSocket code for ElevenLabs and Speechmatics means owning URL construction, authentication and message parsing for protocols the vendors change without notice, and features such as Speechmatics diarization need protocol extensions.

## Decision Outcome

ElevenLabs STT uses the `elevenlabs` SDK (`client.speech_to_text.realtime.connect()`, callback events). Speechmatics STT uses the `speechmatics-rt` SDK (`AsyncClient`, decorator events). Both sit behind the unchanged `RealtimeSttProvider` protocol.

## Consequences

- Vendors maintain the protocol code, and diarization comes from the SDK's word-level speaker labels.
- Both SDKs are optional extras in `pyproject.toml`.
