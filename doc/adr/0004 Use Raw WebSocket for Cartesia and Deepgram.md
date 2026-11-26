---
status: Accepted
date: 2026-05-08
---

# Use Raw WebSocket for Cartesia and Deepgram

## Context and Problem Statement

[0001](0001%20Use%20Official%20SDKs%20for%20ElevenLabs%20and%20Speechmatics%20STT.md) moves ElevenLabs and Speechmatics to SDKs. The other providers need the same choice.

## Decision Outcome

- Cartesia and Deepgram use raw `websockets`. Their protocols are simple JSON over WebSocket and gain nothing from an SDK layer.
- Google uses the `google-cloud-speech` SDK, whose synchronous streaming call runs in a thread.
- Gemini Live uses the `google-genai` SDK for the bidirectional Live API.

## Consequences

- Cartesia and Deepgram add no dependencies.
- `websockets` is a core dependency. `SttEventQueue.recv_guard()` in `_event_queue.py` holds the shared WebSocket close and cancellation handling.
