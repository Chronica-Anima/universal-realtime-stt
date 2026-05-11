"""
Unit tests for universal_realtime_stt_tts — no real API calls, no network.

Exercises the core abstractions (TranscriptEvent, stt_session_task,
transcript_ingest_task, TTS protocol) using mock providers.

    pytest tests/test_unit.py -v
"""
from __future__ import annotations

import asyncio
import unittest
from typing import AsyncIterator

from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt_provider import TranscriptEvent, RealtimeSttProvider
from universal_realtime_stt_tts.tts_provider import RealtimeTtsProvider


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------

class MockSttProvider:
    """Plays back a preset sequence of TranscriptEvents."""

    def __init__(self, events: list[TranscriptEvent]) -> None:
        self._preset = events
        self.sent_chunks: list[bytes] = []
        self.end_audio_called = False

    async def __aenter__(self) -> "MockSttProvider":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass

    async def send_audio(self, pcm_chunk: bytes) -> None:
        self.sent_chunks.append(pcm_chunk)

    async def end_audio(self) -> None:
        self.end_audio_called = True

    def events(self) -> AsyncIterator[TranscriptEvent]:
        async def _aiter():
            for ev in self._preset:
                yield ev
        return _aiter()


class MockTtsProvider:
    """Returns fixed PCM chunks for any input text."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


# ---------------------------------------------------------------------------
# TranscriptEvent
# ---------------------------------------------------------------------------

class TestTranscriptEvent(unittest.TestCase):
    def test_construction_minimal(self) -> None:
        ev = TranscriptEvent(text="hello", is_final=True)
        self.assertEqual(ev.text, "hello")
        self.assertTrue(ev.is_final)
        self.assertIsNone(ev.speaker)

    def test_construction_with_speaker(self) -> None:
        ev = TranscriptEvent(text="world", is_final=False, speaker="S1")
        self.assertEqual(ev.speaker, "S1")

    def test_frozen(self) -> None:
        ev = TranscriptEvent(text="x", is_final=True)
        with self.assertRaises(AttributeError):
            ev.text = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# stt_session_task orchestration
# ---------------------------------------------------------------------------

class TestSttSessionTask(unittest.IsolatedAsyncioTestCase):
    async def test_events_forwarded_to_transcript_queue(self) -> None:
        """Provider events appear in transcript_queue; None sentinel at the end."""
        from universal_realtime_stt_tts.stt import stt_session_task

        preset = [
            TranscriptEvent(text="partial", is_final=False),
            TranscriptEvent(text="final", is_final=True),
        ]
        provider = MockSttProvider(preset)
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        transcript_q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        audio_q.put_nowait(b"\x00\x01" * 100)
        audio_q.put_nowait(None)

        await stt_session_task(provider, audio_q, transcript_q, running)

        received: list[TranscriptEvent | None] = []
        while not transcript_q.empty():
            received.append(transcript_q.get_nowait())

        texts = [ev.text for ev in received if ev is not None]
        self.assertIn("partial", texts)
        self.assertIn("final", texts)
        self.assertIs(received[-1], None)

    async def test_end_audio_called_after_none_chunk(self) -> None:
        from universal_realtime_stt_tts.stt import stt_session_task

        provider = MockSttProvider([TranscriptEvent(text="ok", is_final=True)])
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        transcript_q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        audio_q.put_nowait(None)
        await stt_session_task(provider, audio_q, transcript_q, running)
        self.assertTrue(provider.end_audio_called)

    async def test_audio_chunks_forwarded_to_provider(self) -> None:
        from universal_realtime_stt_tts.stt import stt_session_task

        provider = MockSttProvider([TranscriptEvent(text="ok", is_final=True)])
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        transcript_q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        chunk = b"\xAB\xCD" * 50
        audio_q.put_nowait(chunk)
        audio_q.put_nowait(None)

        await stt_session_task(provider, audio_q, transcript_q, running)
        self.assertIn(chunk, provider.sent_chunks)

    async def test_empty_text_events_filtered(self) -> None:
        from universal_realtime_stt_tts.stt import stt_session_task

        preset = [
            TranscriptEvent(text="   ", is_final=False),
            TranscriptEvent(text="real", is_final=True),
            TranscriptEvent(text="", is_final=True),
        ]
        provider = MockSttProvider(preset)
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        transcript_q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        audio_q.put_nowait(None)
        await stt_session_task(provider, audio_q, transcript_q, running)

        received = []
        while not transcript_q.empty():
            received.append(transcript_q.get_nowait())

        texts = [ev.text for ev in received if ev is not None]
        self.assertEqual(texts, ["real"])

    async def test_silence_keepalive_sent_on_timeout(self) -> None:
        """When audio_queue is idle, silence bytes are sent to the provider."""
        from universal_realtime_stt_tts.stt import stt_session_task

        delay_event = asyncio.Event()

        class SlowMockProvider(MockSttProvider):
            def events(self) -> AsyncIterator[TranscriptEvent]:
                async def _aiter():
                    await delay_event.wait()
                    return
                    yield  # makes this an async generator
                return _aiter()

        provider = SlowMockProvider([])
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        transcript_q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        async def stop_after_delay():
            await asyncio.sleep(0.5)
            audio_q.put_nowait(None)
            await asyncio.sleep(0.1)
            delay_event.set()

        asyncio.create_task(stop_after_delay())
        await stt_session_task(provider, audio_q, transcript_q, running)

        silence = b"\x00\x00" * 1600
        silence_chunks = [c for c in provider.sent_chunks if c == silence]
        self.assertGreater(len(silence_chunks), 0, "Expected at least one silence keepalive")


# ---------------------------------------------------------------------------
# transcript_ingest_task
# ---------------------------------------------------------------------------

class TestTranscriptIngest(unittest.IsolatedAsyncioTestCase):
    async def test_collects_only_final_events(self) -> None:
        from helpers.transcript_ingest import transcript_ingest_task

        q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        q.put_nowait(TranscriptEvent(text="partial1", is_final=False))
        q.put_nowait(TranscriptEvent(text="final1", is_final=True))
        q.put_nowait(TranscriptEvent(text="partial2", is_final=False))
        q.put_nowait(TranscriptEvent(text="final2", is_final=True))
        q.put_nowait(None)

        result = await transcript_ingest_task(running, q)
        self.assertEqual(result, ["final1", "final2"])

    async def test_none_sentinel_stops_loop(self) -> None:
        from helpers.transcript_ingest import transcript_ingest_task

        q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        q.put_nowait(TranscriptEvent(text="before", is_final=True))
        q.put_nowait(None)
        q.put_nowait(TranscriptEvent(text="after", is_final=True))

        result = await transcript_ingest_task(running, q)
        self.assertEqual(result, ["before"])

    async def test_empty_text_ignored(self) -> None:
        from helpers.transcript_ingest import transcript_ingest_task

        q: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        running = asyncio.Event()
        running.set()

        q.put_nowait(TranscriptEvent(text="  ", is_final=True))
        q.put_nowait(TranscriptEvent(text="real", is_final=True))
        q.put_nowait(None)

        result = await transcript_ingest_task(running, q)
        self.assertEqual(result, ["real"])


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance(unittest.TestCase):
    def test_mock_stt_satisfies_protocol(self) -> None:
        provider = MockSttProvider([])
        self.assertIsInstance(provider, RealtimeSttProvider)

    def test_mock_tts_satisfies_protocol(self) -> None:
        provider = MockTtsProvider([b"\x00"])
        self.assertIsInstance(provider, RealtimeTtsProvider)


# ---------------------------------------------------------------------------
# ElevenLabs STT provider (mock SDK)
# ---------------------------------------------------------------------------

class TestElevenLabsSttCallbacks(unittest.TestCase):
    def _make_provider(self):
        from universal_realtime_stt_tts.stt_provider_elevenlabs import (
            ElevenLabsSttProvider, ElevenLabsSttConfig,
        )
        cfg = ElevenLabsSttConfig(api_key="fake-key")
        return ElevenLabsSttProvider(cfg)

    def test_on_transcript_partial(self) -> None:
        p = self._make_provider()
        p._on_transcript({"text": "hello"}, is_final=False)
        ev = p._eq.get_nowait()
        self.assertEqual(ev.text, "hello")
        self.assertFalse(ev.is_final)

    def test_on_transcript_final(self) -> None:
        p = self._make_provider()
        p._on_transcript({"text": "world"}, is_final=True)
        ev = p._eq.get_nowait()
        self.assertEqual(ev.text, "world")
        self.assertTrue(ev.is_final)

    def test_on_transcript_ignores_empty_text(self) -> None:
        p = self._make_provider()
        p._on_transcript({"text": "   "}, is_final=False)
        self.assertTrue(p._eq.empty())
        p._on_transcript({"text": ""}, is_final=True)
        self.assertTrue(p._eq.empty())

    def test_on_error_sets_error_and_terminates(self) -> None:
        p = self._make_provider()
        p._on_error("connection reset")
        self.assertIsNotNone(p._eq.error)
        sentinel = p._eq.get_nowait()
        self.assertIsNone(sentinel)

    def test_on_close_sends_sentinel(self) -> None:
        p = self._make_provider()
        p._on_close()
        sentinel = p._eq.get_nowait()
        self.assertIsNone(sentinel)


# ---------------------------------------------------------------------------
# Speechmatics STT provider (mock — extract_speaker logic)
# ---------------------------------------------------------------------------

class TestSttEventQueue(unittest.IsolatedAsyncioTestCase):
    def _make_queue(self) -> SttEventQueue:
        import logging
        return SttEventQueue(logging.getLogger("test"))

    def test_put_nowait_and_get(self) -> None:
        eq = self._make_queue()
        ev = TranscriptEvent(text="hello", is_final=True)
        eq.put_nowait(ev)
        self.assertEqual(eq.get_nowait(), ev)

    def test_put_sentinel_idempotent(self) -> None:
        eq = self._make_queue()
        eq.put_sentinel()
        eq.put_sentinel()
        self.assertIsNone(eq.get_nowait())
        self.assertTrue(eq.empty())

    def test_set_error_puts_sentinel(self) -> None:
        eq = self._make_queue()
        eq.set_error(RuntimeError("boom"))
        self.assertIsNotNone(eq.error)
        self.assertIsNone(eq.get_nowait())

    async def test_events_iterator(self) -> None:
        eq = self._make_queue()
        eq.put_nowait(TranscriptEvent(text="a", is_final=False))
        eq.put_nowait(TranscriptEvent(text="b", is_final=True))
        eq.put_sentinel()

        texts = [ev.text async for ev in eq.events()]
        self.assertEqual(texts, ["a", "b"])

    async def test_events_raises_on_error(self) -> None:
        eq = self._make_queue()
        eq.put_nowait(TranscriptEvent(text="ok", is_final=True))
        eq.set_error(RuntimeError("fail"))

        with self.assertRaises(RuntimeError):
            async for _ in eq.events():
                pass


class TestSpeechmaticsExtractSpeaker(unittest.TestCase):
    def _make_provider(self):
        from universal_realtime_stt_tts.stt_provider_speechmatics import (
            SpeechmaticsSttProvider, SpeechmaticsSttConfig,
        )
        cfg = SpeechmaticsSttConfig(api_key="fake-key")
        return SpeechmaticsSttProvider(cfg)

    def test_majority_vote(self) -> None:
        p = self._make_provider()
        msg = {
            "results": [
                {"alternatives": [{"speaker": "S1"}]},
                {"alternatives": [{"speaker": "S2"}]},
                {"alternatives": [{"speaker": "S1"}]},
            ]
        }
        self.assertEqual(p._extract_speaker(msg), "S1")

    def test_filters_uu(self) -> None:
        p = self._make_provider()
        msg = {
            "results": [
                {"alternatives": [{"speaker": "UU"}]},
                {"alternatives": [{"speaker": "S1"}]},
            ]
        }
        self.assertEqual(p._extract_speaker(msg), "S1")

    def test_all_uu_returns_none(self) -> None:
        p = self._make_provider()
        msg = {
            "results": [
                {"alternatives": [{"speaker": "UU"}]},
            ]
        }
        self.assertIsNone(p._extract_speaker(msg))

    def test_no_results_returns_none(self) -> None:
        p = self._make_provider()
        self.assertIsNone(p._extract_speaker({"results": []}))
        self.assertIsNone(p._extract_speaker({}))

    def test_empty_alternatives_returns_none(self) -> None:
        p = self._make_provider()
        msg = {"results": [{"alternatives": []}]}
        self.assertIsNone(p._extract_speaker(msg))

    def test_flush_utterance_with_speaker(self) -> None:
        p = self._make_provider()
        p._utterance_buf.append("hello world")
        p._utterance_speaker = "S1"
        p._flush_utterance()
        ev = p._eq.get_nowait()
        self.assertEqual(ev.text, "hello world")
        self.assertTrue(ev.is_final)
        self.assertEqual(ev.speaker, "S1")

    def test_flush_utterance_joins_segments(self) -> None:
        p = self._make_provider()
        p._utterance_buf.extend(["hello", "world"])
        p._flush_utterance()
        ev = p._eq.get_nowait()
        self.assertEqual(ev.text, "hello world")
        self.assertTrue(ev.is_final)

    def test_flush_utterance_ignores_empty_buffer(self) -> None:
        p = self._make_provider()
        p._flush_utterance()
        self.assertTrue(p._eq.empty())


# ---------------------------------------------------------------------------
# recv_guard context manager
# ---------------------------------------------------------------------------

class TestRecvGuard(unittest.IsolatedAsyncioTestCase):
    def _make_queue(self) -> SttEventQueue:
        import logging
        return SttEventQueue(logging.getLogger("test"))

    async def test_clean_exit_sets_closed_and_sentinel(self) -> None:
        eq = self._make_queue()
        closed = asyncio.Event()
        async with eq.recv_guard("Test", closed):
            pass
        self.assertTrue(closed.is_set())
        self.assertIsNone(eq.get_nowait())
        self.assertIsNone(eq.error)

    async def test_websocket_closed_ok(self) -> None:
        from websockets import ConnectionClosedOK
        eq = self._make_queue()
        closed = asyncio.Event()
        async with eq.recv_guard("Test", closed):
            raise ConnectionClosedOK(None, None)
        self.assertIsNone(eq.error)
        self.assertTrue(closed.is_set())

    async def test_unexpected_websocket_close_sets_error(self) -> None:
        from websockets import ConnectionClosed
        from websockets.frames import Close
        eq = self._make_queue()
        closed = asyncio.Event()
        async with eq.recv_guard("Test", closed):
            raise ConnectionClosed(Close(1006, "abnormal"), None)
        self.assertIsNotNone(eq.error)

    async def test_cancelled_error_reraised(self) -> None:
        eq = self._make_queue()
        closed = asyncio.Event()
        with self.assertRaises(asyncio.CancelledError):
            async with eq.recv_guard("Test", closed):
                raise asyncio.CancelledError()
        self.assertTrue(closed.is_set())

    async def test_generic_exception_sets_error(self) -> None:
        eq = self._make_queue()
        closed = asyncio.Event()
        async with eq.recv_guard("Test", closed):
            raise ValueError("boom")
        self.assertIsNotNone(eq.error)

    async def test_normal_close_when_already_closed(self) -> None:
        eq = self._make_queue()
        closed = asyncio.Event()
        closed.set()
        async with eq.recv_guard("Test", closed):
            raise Exception("expected after close")
        self.assertIsNone(eq.error)

    async def test_on_close_callback_runs(self) -> None:
        eq = self._make_queue()
        closed = asyncio.Event()
        called = False
        async def on_close():
            nonlocal called
            called = True
        async with eq.recv_guard("Test", closed, on_close=on_close):
            pass
        self.assertTrue(called)

    async def test_on_close_sees_error_state(self) -> None:
        """on_close runs after exception handling, so it sees set_error."""
        eq = self._make_queue()
        closed = asyncio.Event()
        observed_error = None
        async def on_close():
            nonlocal observed_error
            observed_error = eq.error
        async with eq.recv_guard("Test", closed, on_close=on_close):
            raise ValueError("fail")
        self.assertIsNotNone(observed_error)
