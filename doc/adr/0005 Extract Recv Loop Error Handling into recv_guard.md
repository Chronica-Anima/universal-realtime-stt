---
status: Accepted
date: 2026-05-08
---

# Extract Recv Loop Error Handling into recv_guard

## Context and Problem Statement

Cartesia, Deepgram, and Gemini Live providers each had a `_recv_loop` method with ~20 lines of identical error handling: catching `ConnectionClosedOK`, `ConnectionClosed` (clean vs unexpected), `CancelledError`, generic exceptions, plus a finally block calling `closed.set()` and `put_sentinel()`. The actual message parsing differed per provider, but the error shell was copy-pasted.

## Considered Options

- Base class with template method (`_handle_message` override) — adds inheritance to a protocol-based design
- Async context manager on `SttEventQueue` wrapping the loop body — keeps protocol-based design, providers stay independent

## Decision Outcome

Async context manager: `SttEventQueue.recv_guard(provider_name, closed, *, on_close=None)`.

Providers wrap their message loop with `async with self._eq.recv_guard(...)`. The context manager handles WebSocket exceptions, cancellation, generic errors, and the finally cleanup. An optional `on_close` callback runs after exception handling but before the sentinel — used by Gemini Live for its pending-interim promotion quirk.

## Consequences

- Each provider's `_recv_loop` shrinks by ~20 lines
- Error handling behavior is consistent across providers and tested in one place (`TestRecvGuard`)
- Gemini's `on_close` callback preserves correct ordering: exception handler sets error state before `on_close` checks it
- `_event_queue.py` gains a `websockets` import (already a core dependency)
