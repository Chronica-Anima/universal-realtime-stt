---
status: Accepted
date: 2026-05-08
---

# Add Speaker Field to TranscriptEvent

## Context and Problem Statement

Speechmatics provides per-word speaker labels (`S1`, `S2`, ..., `UU`) in its transcription results. There was no way to surface this information through the existing `TranscriptEvent(text, is_final)` dataclass.

## Considered Options

- Separate diarization event type alongside `TranscriptEvent`
- Add optional `speaker` field to the existing `TranscriptEvent`

## Decision Outcome

Add `speaker: str | None = None` to `TranscriptEvent`. Providers without diarization return `None`.

Speechmatics extracts the dominant speaker per event via majority vote over word-level `alternatives[].speaker` labels, filtering out `UU` (unidentified). The result populates `TranscriptEvent.speaker`.

## Consequences

- Backward-compatible — existing positional `TranscriptEvent(text=..., is_final=...)` calls still work
- Consumers that don't care about diarization can ignore the field
- Speaker extraction logic (`_extract_speaker`) lives in the Speechmatics provider, not in the protocol layer
