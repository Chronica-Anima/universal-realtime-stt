---
status: Accepted
date: 2026-05-08
---

# Add Speaker Field to TranscriptEvent

## Context and Problem Statement

Speechmatics labels each word with a speaker (`S1`, `S2`, ..., `UU` for unidentified), and `TranscriptEvent` needs a way to carry it.

## Decision Outcome

`TranscriptEvent` has an optional `speaker: str | None = None`, chosen over a separate diarization event type. Providers without diarization leave it `None`.

Speechmatics picks the most frequent known label among an event's words. When every label is `UU` the speaker is `"??"`, and when no word carries a label it is `None`. This logic lives in the provider, not the protocol.

## Consequences

- Existing `TranscriptEvent(text=..., is_final=...)` calls keep working.
- Consumers that ignore diarization ignore the field.
