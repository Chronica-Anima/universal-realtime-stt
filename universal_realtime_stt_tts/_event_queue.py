from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from logging import Logger
from typing import AsyncIterator

from websockets import ConnectionClosed, ConnectionClosedOK

from universal_realtime_stt_tts.stt_provider import TranscriptEvent


class SttEventQueue:
    """Shared event queue with sentinel handling for STT providers.

    Encapsulates the queue + error + sentinel pattern that every provider
    implements identically. Providers compose this instead of duplicating
    the boilerplate.
    """

    def __init__(self, log: Logger) -> None:
        self._q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(maxsize=200)
        self._log = log
        self._error: Exception | None = None
        self._sentinel_sent = False
        # Strong reference, so the event loop cannot garbage-collect a pending put.
        self._pending_sentinel: asyncio.Task | None = None

    @property
    def error(self) -> Exception | None:
        return self._error

    def set_error(self, err: Exception) -> None:
        self._error = err
        self.put_sentinel()

    def put_nowait(self, ev: TranscriptEvent) -> None:
        try:
            self._q.put_nowait(ev)
        except asyncio.QueueFull:
            self._log.warning("Event queue full, dropping event")

    async def put(self, ev: TranscriptEvent) -> None:
        await self._q.put(ev)

    def put_sentinel(self) -> None:
        """Queue the end-of-stream marker once. Call it on the event loop thread."""
        if self._sentinel_sent:
            return
        try:
            self._q.put_nowait(None)
        except asyncio.QueueFull:
            # Dropping the sentinel would block events() forever, so wait for the
            # consumer to make room; every event already queued stays ahead of it.
            self._pending_sentinel = asyncio.get_running_loop().create_task(self._q.put(None))
        self._sentinel_sent = True

    def finish_from_thread(self, loop: asyncio.AbstractEventLoop, error: Exception | None = None) -> None:
        """End the stream from a worker thread, optionally with an error.

        asyncio.Queue is not thread-safe, so the work is handed to *loop*.
        """
        def finish() -> None:
            if error is not None:
                self._error = error
            self.put_sentinel()

        loop.call_soon_threadsafe(finish)

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter():
            while True:
                ev = await self._q.get()
                if ev is None:
                    if self._error:
                        raise self._error
                    break
                yield ev
        return _aiter()

    @asynccontextmanager
    async def recv_guard(
        self,
        provider_name: str,
        closed: asyncio.Event,
        *,
        on_close: Callable[[], Awaitable[None]] | None = None,
    ):
        """Wrap a provider's recv loop body with shared error handling.

        Handles WebSocket close exceptions, cancellation, and the
        ``closed.set()`` + ``put_sentinel()`` cleanup that every provider
        needs.  Optional *on_close* coroutine runs in the finally block
        after exception handling but before the sentinel; use it for
        provider-specific teardown (e.g. Gemini's pending-interim promotion).
        """
        try:
            yield
        except ConnectionClosedOK:
            self._log.debug("[STT] %s: session closed cleanly.", provider_name)
        except ConnectionClosed as e:
            is_clean = e.code == 1000 or (e.rcvd is None and e.sent is not None)
            if is_clean:
                self._log.debug("[STT] %s: session closed (code=%s).", provider_name, e.code)
            else:
                self._log.warning("[STT] %s: connection closed unexpectedly: %s", provider_name, e)
                self.set_error(RuntimeError(f"{provider_name} connection closed unexpectedly: {e}"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if closed.is_set() or getattr(e, "status_code", None) == 1000:
                self._log.debug("[STT] %s: recv loop ended after close: %s", provider_name, e)
            else:
                self._log.exception("[STT] %s receiver crashed: %r", provider_name, e)
                self.set_error(e)
        finally:
            if on_close:
                await on_close()
            closed.set()
            self.put_sentinel()
