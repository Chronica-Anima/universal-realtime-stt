---
status: Accepted
date: 2026-05-08
---

# Keep Raw WebSocket for Cartesia, Deepgram, Google, and Gemini

## Context and Problem Statement

With ElevenLabs and Speechmatics moving to official SDKs (see [0001](0001%20Use%20Official%20SDKs%20for%20ElevenLabs%20and%20Speechmatics%20STT.md)), the question was whether the remaining providers should also switch.

## Considered Options

- Switch all providers to official SDKs
- Keep raw `websockets` for Cartesia and Deepgram; keep existing Google and Gemini SDK usage

## Decision Outcome

Keep current implementations.

- Cartesia and Deepgram have straightforward WebSocket protocols that don't benefit from an SDK layer. The `websockets` library handles the transport; the provider code handles the JSON message format.
- Google uses `google-cloud-speech` (synchronous streaming in a thread). This is already SDK-based.
- Gemini Live uses `google-genai` SDK for its bidirectional Live API. Also already SDK-based.

`websockets` remains a core dependency since Cartesia and Deepgram still use it.

## Consequences

- No new dependencies for these providers
- `websockets` stays in `[project.dependencies]`, not optional
- Shared recv loop error handling is factored into `SttEventQueue.recv_guard()` (see [0005](0005%20Extract%20Recv%20Loop%20Error%20Handling%20into%20recv_guard.md))
