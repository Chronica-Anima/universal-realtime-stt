"""
Unit tests for universal_realtime_stt_tts and the transcription helpers, with fake
providers and SDK clients: no network, no API keys, no assets.

    pytest tests/test_unit.py -v
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import threading
import unittest
import unittest.mock
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

from websockets import ConnectionClosed, ConnectionClosedOK
from websockets.frames import Close

from helpers.transcribe import transcribe_and_diff
from helpers.transcript_ingest import transcript_ingest_task
from universal_realtime_stt_tts import stt as stt_module
from universal_realtime_stt_tts._event_queue import SttEventQueue
from universal_realtime_stt_tts.stt import stt_session_task
from universal_realtime_stt_tts.stt_provider import RealtimeSttProvider, TranscriptEvent
from universal_realtime_stt_tts.stt_provider_cartesia import CartesiaInkProvider, CartesiaSttConfig
from universal_realtime_stt_tts.stt_provider_deepgram import DeepgramRealtimeProvider, DeepgramSttConfig
from universal_realtime_stt_tts.stt_provider_elevenlabs import ElevenLabsSttConfig, ElevenLabsSttProvider
from universal_realtime_stt_tts.stt_provider_gemini_live import GeminiLiveProvider, GeminiLiveSttConfig
from universal_realtime_stt_tts.stt_provider_google import GoogleRealtimeProvider
from universal_realtime_stt_tts.stt_provider_speechmatics import SpeechmaticsSttConfig, SpeechmaticsSttProvider
from universal_realtime_stt_tts.tts_provider import RealtimeTtsProvider
from universal_realtime_stt_tts.tts_provider_elevenlabs import ElevenLabsTtsConfig, ElevenLabsTtsProvider


class FakeSttProvider:
    """Records the audio it is sent and replays preset events once the audio has ended."""

    def __init__(self, events: list[TranscriptEvent] | None = None) -> None:
        self._preset = events or []
        self.sent_chunks: list[bytes] = []
        self.audio_ended = asyncio.Event()
        self.exited = False

    async def __aenter__(self) -> "FakeSttProvider":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited = True

    async def send_audio(self, pcm_chunk: bytes) -> None:
        self.sent_chunks.append(pcm_chunk)

    async def end_audio(self) -> None:
        self.audio_ended.set()

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        await self.audio_ended.wait()
        for event in self._preset:
            yield event


def queued(*items) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)
    return queue


async def run_session(provider: FakeSttProvider, audio_queue: asyncio.Queue) -> list[TranscriptEvent | None]:
    """Run one stt_session_task and return everything it put on the transcript queue."""
    transcript_queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
    running = asyncio.Event()
    running.set()
    await stt_session_task(provider, audio_queue, transcript_queue, running)
    return [transcript_queue.get_nowait() for _ in range(transcript_queue.qsize())]


async def collect_texts(event_queue: SttEventQueue) -> list[str]:
    return [event.text async for event in event_queue.events()]


def make_queue() -> SttEventQueue:
    return SttEventQueue(logging.getLogger("test"))


# ---------------------------------------------------------------------------
# stt_session_task orchestration
# ---------------------------------------------------------------------------

class TestSttSessionTask(unittest.IsolatedAsyncioTestCase):
    async def test_audio_reaches_provider_and_events_reach_transcript_queue(self) -> None:
        chunk = b"\xAB\xCD" * 50
        provider = FakeSttProvider([
            TranscriptEvent(text="partial", is_final=False),
            TranscriptEvent(text="final", is_final=True),
        ])
        received = await run_session(provider, queued(chunk, None))

        self.assertEqual(provider.sent_chunks, [chunk])
        self.assertTrue(provider.audio_ended.is_set())
        self.assertEqual(received, [
            TranscriptEvent(text="partial", is_final=False),
            TranscriptEvent(text="final", is_final=True),
            None,
        ])

    async def test_blank_events_are_filtered(self) -> None:
        provider = FakeSttProvider([
            TranscriptEvent(text="   ", is_final=False),
            TranscriptEvent(text="real", is_final=True),
            TranscriptEvent(text="", is_final=True),
        ])
        received = await run_session(provider, queued(None))
        self.assertEqual(received, [TranscriptEvent(text="real", is_final=True), None])

    async def test_idle_audio_queue_sends_silence_keepalive(self) -> None:
        provider = FakeSttProvider()
        audio_queue = queued()
        asyncio.get_running_loop().call_later(0.35, audio_queue.put_nowait, None)
        # The shipped backstop is 5 s, deliberately long so pacing jitter never
        # interleaves silence with real audio, and too long to wait out in a unit test.
        with unittest.mock.patch.object(stt_module, "_AUDIO_IDLE_KEEPALIVE_S", 0.1):
            await run_session(provider, audio_queue)

        self.assertTrue(provider.sent_chunks, "Expected at least one silence keepalive")
        self.assertEqual(set(provider.sent_chunks), {stt_module._SILENCE_CHUNK})


# ---------------------------------------------------------------------------
# transcript_ingest_task
# ---------------------------------------------------------------------------

class TestTranscriptIngest(unittest.IsolatedAsyncioTestCase):
    async def test_collects_only_final_events(self) -> None:
        result = await transcript_ingest_task(queued(
            TranscriptEvent(text="partial1", is_final=False),
            TranscriptEvent(text="final1", is_final=True),
            TranscriptEvent(text="partial2", is_final=False),
            TranscriptEvent(text="final2", is_final=True),
            None,
        ))
        self.assertEqual(result, ["final1", "final2"])

    async def test_none_sentinel_stops_loop(self) -> None:
        result = await transcript_ingest_task(queued(
            TranscriptEvent(text="before", is_final=True),
            None,
            TranscriptEvent(text="after", is_final=True),
        ))
        self.assertEqual(result, ["before"])

    async def test_blank_text_ignored_and_text_stripped(self) -> None:
        result = await transcript_ingest_task(queued(
            TranscriptEvent(text="  ", is_final=True),
            TranscriptEvent(text=" real ", is_final=True),
            None,
        ))
        self.assertEqual(result, ["real"])


# ---------------------------------------------------------------------------
# transcribe_and_diff cancellation
# ---------------------------------------------------------------------------

class TestTranscribeCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_mid_stream_leaves_no_child_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / "speech.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x00\x00" * 16000 * 10)
            txt_path = Path(directory) / "speech.txt"
            txt_path.write_text("expected text", encoding="utf-8")

            # Ten seconds of audio paced in real time, so the provider is still streaming at the timeout.
            provider = FakeSttProvider()
            tasks_before = asyncio.all_tasks()
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(
                    transcribe_and_diff(provider, wav_path, txt_path, Path(directory) / "report.html",
                                        chunk_ms=100, sample_rate=16000, realtime_factor=1.0, silence_s=0.2),
                    timeout=0.5,
                )

            surviving_tasks = [task for task in asyncio.all_tasks() - tasks_before if not task.done()]
            self.assertEqual(surviving_tasks, [])
            self.assertTrue(provider.exited, "provider session was not closed")


# ---------------------------------------------------------------------------
# Protocol conformance of the shipped providers
# ---------------------------------------------------------------------------

class TestProtocolConformance(unittest.TestCase):
    def test_stt_providers_satisfy_protocol(self) -> None:
        providers = [
            CartesiaInkProvider(CartesiaSttConfig(api_key="fake-key")),
            DeepgramRealtimeProvider(DeepgramSttConfig(api_key="fake-key")),
            ElevenLabsSttProvider(ElevenLabsSttConfig(api_key="fake-key")),
            GeminiLiveProvider(GeminiLiveSttConfig(api_key="fake-key")),
            GoogleRealtimeProvider(),
            SpeechmaticsSttProvider(SpeechmaticsSttConfig(api_key="fake-key")),
        ]
        for provider in providers:
            with self.subTest(provider=type(provider).__name__):
                self.assertIsInstance(provider, RealtimeSttProvider)

    def test_tts_provider_satisfies_protocol(self) -> None:
        self.assertIsInstance(ElevenLabsTtsProvider(ElevenLabsTtsConfig(api_key="fake-key")), RealtimeTtsProvider)


# ---------------------------------------------------------------------------
# ElevenLabs TTS provider (fake async SDK client)
# ---------------------------------------------------------------------------

class FakeTextToSpeechClient:
    """Stands in for AsyncElevenLabs().text_to_speech, recording the stream() arguments."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.stream_calls: list[dict] = []

    async def _replay(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    def stream(self, **arguments) -> AsyncIterator[bytes]:
        self.stream_calls.append(arguments)
        return self._replay()


class TestElevenLabsTtsStreaming(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from universal_realtime_stt_tts import tts_provider_elevenlabs

        tts_provider_elevenlabs._shared_clients.clear()
        self.addCleanup(tts_provider_elevenlabs._shared_clients.clear)

        self.text_to_speech = FakeTextToSpeechClient([b"\x01\x02", b"", b"\x03\x04"])
        self.constructed_clients = 0

        def make_client(**_arguments):
            self.constructed_clients += 1
            return SimpleNamespace(text_to_speech=self.text_to_speech)

        patcher = unittest.mock.patch("elevenlabs.AsyncElevenLabs", side_effect=make_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _collect(self, model: str) -> list[bytes]:
        provider = ElevenLabsTtsProvider(ElevenLabsTtsConfig(api_key="fake-key", model=model))
        return [chunk async for chunk in provider.synthesize("Dobrý den.", language="cs")]

    async def test_yields_chunks_and_skips_empty(self) -> None:
        self.assertEqual(await self._collect("eleven_v3_conversational"), [b"\x01\x02", b"\x03\x04"])

    async def test_stream_arguments_reach_the_sdk(self) -> None:
        await self._collect("eleven_v3_conversational")
        arguments = self.text_to_speech.stream_calls[0]
        self.assertEqual(arguments["text"], "Dobrý den.")
        self.assertEqual(arguments["language_code"], "cs")
        self.assertEqual(arguments["model_id"], "eleven_v3_conversational")
        self.assertEqual(arguments["voice_id"], "MpbYQvoTmXjHkaxtLiSh")

    async def test_latency_level_sent_only_for_multilingual(self) -> None:
        await self._collect("eleven_multilingual_v2")
        await self._collect("eleven_v3_conversational")
        multilingual, conversational = self.text_to_speech.stream_calls
        self.assertEqual(multilingual["optimize_streaming_latency"], 3)
        self.assertNotIn("optimize_streaming_latency", conversational)

    async def test_client_reused_across_calls(self) -> None:
        await self._collect("eleven_v3_conversational")
        await self._collect("eleven_v3_conversational")
        self.assertEqual(len(self.text_to_speech.stream_calls), 2)
        self.assertEqual(self.constructed_clients, 1)


# ---------------------------------------------------------------------------
# ElevenLabs STT provider callbacks
# ---------------------------------------------------------------------------

class TestElevenLabsSttCallbacks(unittest.IsolatedAsyncioTestCase):
    async def test_transcripts_keep_finality_and_close_ends_stream(self) -> None:
        provider = ElevenLabsSttProvider(ElevenLabsSttConfig(api_key="fake-key"))
        provider._on_transcript({"text": " hello "}, is_final=False)
        provider._on_transcript({"text": "   "}, is_final=False)
        provider._on_transcript({"text": "world"}, is_final=True)
        provider._on_transcript({"text": ""}, is_final=True)
        provider._on_close()

        self.assertEqual([event async for event in provider.events()], [
            TranscriptEvent(text="hello", is_final=False),
            TranscriptEvent(text="world", is_final=True),
        ])

    async def test_error_ends_stream_with_runtime_error(self) -> None:
        provider = ElevenLabsSttProvider(ElevenLabsSttConfig(api_key="fake-key"))
        provider._on_error("connection reset")

        with self.assertRaises(RuntimeError) as raised:
            async for _event in provider.events():
                pass
        self.assertEqual(str(raised.exception), "ElevenLabs STT error: connection reset")


# ---------------------------------------------------------------------------
# SttEventQueue
# ---------------------------------------------------------------------------

class TestSttEventQueue(unittest.IsolatedAsyncioTestCase):
    def test_put_sentinel_idempotent(self) -> None:
        event_queue = make_queue()
        event_queue.put_sentinel()
        event_queue.put_sentinel()
        self.assertEqual(event_queue._q.qsize(), 1)

    async def test_events_iterator(self) -> None:
        event_queue = make_queue()
        event_queue.put_nowait(TranscriptEvent(text="a", is_final=False))
        event_queue.put_nowait(TranscriptEvent(text="b", is_final=True))
        event_queue.put_sentinel()
        self.assertEqual(await collect_texts(event_queue), ["a", "b"])

    async def test_events_raise_error_after_queued_events(self) -> None:
        event_queue = make_queue()
        event_queue.put_nowait(TranscriptEvent(text="ok", is_final=True))
        failure = RuntimeError("fail")
        event_queue.set_error(failure)

        received = []
        with self.assertRaises(RuntimeError) as raised:
            async for event in event_queue.events():
                received.append(event.text)
        self.assertIs(raised.exception, failure)
        self.assertEqual(received, ["ok"])

    async def test_sentinel_on_full_queue_waits_instead_of_dropping(self) -> None:
        event_queue = make_queue()
        while not event_queue._q.full():
            event_queue.put_nowait(TranscriptEvent(text=str(event_queue._q.qsize()), is_final=True))
        event_queue.put_sentinel()
        event_queue.put_sentinel()

        # A dropped sentinel blocks events() forever, so fail instead of hanging the suite.
        texts = await asyncio.wait_for(collect_texts(event_queue), timeout=5)
        self.assertEqual(texts, [str(index) for index in range(event_queue._q.maxsize)])
        self.assertTrue(event_queue._q.empty())

    async def test_finish_from_thread_ends_stream_with_error(self) -> None:
        event_queue = make_queue()
        event_queue.put_nowait(TranscriptEvent(text="before", is_final=True))
        failure = RuntimeError("stream broke")
        await asyncio.to_thread(event_queue.finish_from_thread, asyncio.get_running_loop(), failure)

        received = []
        with self.assertRaises(RuntimeError) as raised:
            async for event in event_queue.events():
                received.append(event.text)
        self.assertIs(raised.exception, failure)
        self.assertEqual(received, ["before"])


# ---------------------------------------------------------------------------
# Deepgram STT provider (fake WebSocket)
# ---------------------------------------------------------------------------

class FakeDeepgramWebSocket:
    """Replays preset messages, then closes cleanly."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)

    async def recv(self) -> str:
        if not self._messages:
            raise ConnectionClosedOK(None, None)
        return self._messages.pop(0)


def deepgram_results(transcript: str, is_final: bool) -> str:
    return json.dumps({
        "type": "Results",
        "is_final": is_final,
        "channel": {"alternatives": [{"transcript": transcript}]},
    })


class TestDeepgramResults(unittest.IsolatedAsyncioTestCase):
    async def test_interim_and_final_results_keep_their_finality(self) -> None:
        provider = DeepgramRealtimeProvider(DeepgramSttConfig(api_key="fake-key"))
        provider._ws = FakeDeepgramWebSocket([
            deepgram_results("dobry", False),
            deepgram_results("dobry den", True),
            deepgram_results("  ", False),
        ])
        await provider._recv_loop()

        received = [event async for event in provider.events()]
        self.assertEqual(received, [
            TranscriptEvent(text="dobry", is_final=False),
            TranscriptEvent(text="dobry den", is_final=True),
        ])


# ---------------------------------------------------------------------------
# Google STT provider (fake google.cloud.speech)
# ---------------------------------------------------------------------------

class ThreadRecordingEvent(asyncio.Event):
    """Remembers which thread called set()."""

    def set(self) -> None:
        self.setter_thread = threading.get_ident()
        super().set()


def fake_google_cloud(transcript: str):
    def streaming_recognize(_streaming_config, requests):
        for _request in requests:
            pass
        alternative = SimpleNamespace(transcript=transcript)
        result = SimpleNamespace(alternatives=[alternative], is_final=True)
        return [SimpleNamespace(results=[result])]

    speech = unittest.mock.MagicMock()
    speech.SpeechClient.return_value.streaming_recognize.side_effect = streaming_recognize
    return SimpleNamespace(speech=speech)


class TestGoogleStreamEnd(unittest.IsolatedAsyncioTestCase):
    async def test_stream_end_sets_closed_on_the_loop_thread(self) -> None:
        provider = GoogleRealtimeProvider()
        provider._closed = ThreadRecordingEvent()
        with unittest.mock.patch.dict(sys.modules, {"google.cloud": fake_google_cloud(" ahoj ")}):
            async with provider:
                await provider.send_audio(b"\x00\x00")
                await provider.end_audio()
                received = [event async for event in provider.events()]
                self.assertTrue(provider._closed.is_set())

        self.assertEqual(received, [TranscriptEvent(text="ahoj", is_final=True)])
        self.assertEqual(provider._closed.setter_thread, threading.get_ident())


# ---------------------------------------------------------------------------
# Speechmatics STT provider (fake SDK client)
# ---------------------------------------------------------------------------

class TestSpeechmaticsSession(unittest.IsolatedAsyncioTestCase):
    """Config values must reach the SDK client and transcription config; unset ones defer to the SDK."""

    async def _enter(self, **config_arguments):
        client = unittest.mock.MagicMock()
        client.__aenter__ = unittest.mock.AsyncMock(return_value=client)
        client.on = unittest.mock.MagicMock(return_value=lambda handler: handler)
        client.start_session = unittest.mock.AsyncMock()
        with unittest.mock.patch("speechmatics.rt.AsyncClient", return_value=client) as client_class:
            await SpeechmaticsSttProvider(
                SpeechmaticsSttConfig(api_key="fake-key", **config_arguments),
            ).__aenter__()
        return client_class, client

    async def _sdk_url(self, **config_arguments):
        client_class, _client = await self._enter(**config_arguments)
        return client_class.call_args.kwargs["url"]

    async def _transcription_config(self, **config_arguments):
        _client_class, client = await self._enter(**config_arguments)
        return client.start_session.call_args.kwargs["transcription_config"]

    async def test_defaults(self) -> None:
        config = await self._transcription_config()
        self.assertEqual(config.to_dict()["model"], "enhanced")
        self.assertIsNone(config.max_delay_mode)
        self.assertIsNone(config.audio_filtering_config)
        self.assertNotIn("audio_filtering_config", config.to_dict())
        self.assertIsNone(await self._sdk_url())

    async def test_max_delay_settings_reach_the_transcription_config(self) -> None:
        """Both are deliberately off the defaults, so a swapped mapping fails here."""
        config = await self._transcription_config(max_delay_s=1.4, max_delay_mode="fixed")
        self.assertEqual(config.max_delay, 1.4)
        self.assertEqual(config.max_delay_mode, "fixed")

    async def test_model_reaches_the_transcription_config(self) -> None:
        config = await self._transcription_config(model="standard")
        self.assertEqual(config.to_dict()["model"], "standard")
        self.assertNotIn("operating_point", config.to_dict())

    async def test_input_volume_threshold_becomes_audio_filtering_config(self) -> None:
        """0 sends the gate with nothing filtered, so it must not read as unset."""
        for threshold in (3.4, 0):
            with self.subTest(threshold=threshold):
                config = await self._transcription_config(input_volume_threshold=threshold)
                self.assertEqual(config.audio_filtering_config, {"volume_threshold": threshold})
                self.assertEqual(config.to_dict()["audio_filtering_config"], {"volume_threshold": threshold})

    async def test_speaker_diarization_config_reaches_the_transcription_config(self) -> None:
        config = await self._transcription_config(speaker_diarization_config={"max_speakers": 3})
        self.assertEqual(config.diarization, "speaker")
        self.assertEqual(config.to_dict()["speaker_diarization_config"], {"max_speakers": 3})

    async def test_explicit_base_url_is_passed_to_sdk_and_logged(self) -> None:
        with self.assertLogs("universal_realtime_stt_tts.stt_provider_speechmatics", level="INFO") as logs:
            url = await self._sdk_url(base_url="wss://us.rt.speechmatics.com/v2")
        self.assertEqual(url, "wss://us.rt.speechmatics.com/v2")
        self.assertTrue(
            any("started against wss://us.rt.speechmatics.com/v2" in line for line in logs.output),
            logs.output,
        )


class TestSpeechmaticsUtterances(unittest.IsolatedAsyncioTestCase):
    def test_extract_speaker(self) -> None:
        cases = [
            ("majority vote", [["S1"], ["S2"], ["S1"]], "S1"),
            ("UU is ignored", [["UU"], ["S1"]], "S1"),
            ("only UU", [["UU"]], "??"),
            ("no results", [], None),
            ("empty alternatives", [[]], None),
        ]
        for name, speakers_per_result, expected in cases:
            with self.subTest(name):
                message = {"results": [
                    {"alternatives": [{"speaker": speaker} for speaker in speakers]}
                    for speakers in speakers_per_result
                ]}
                self.assertEqual(SpeechmaticsSttProvider._extract_speaker(message), expected)
        self.assertIsNone(SpeechmaticsSttProvider._extract_speaker({}))

    async def test_flush_utterance_concatenates_segments_with_speaker(self) -> None:
        provider = SpeechmaticsSttProvider(SpeechmaticsSttConfig(api_key="fake-key"))
        # A Speechmatics segment carries its own leading space, so the buffer is
        # concatenated and stripped rather than joined on a separator.
        provider._utterance_buf.extend(["hello", " world"])
        provider._utterance_speaker = "S1"
        provider._flush_utterance()
        provider._flush_utterance()
        provider._eq.put_sentinel()

        self.assertEqual([event async for event in provider.events()], [
            TranscriptEvent(text="hello world", is_final=True, speaker="S1"),
        ])


# ---------------------------------------------------------------------------
# recv_guard context manager
# ---------------------------------------------------------------------------

class TestRecvGuard(unittest.IsolatedAsyncioTestCase):
    async def test_clean_exit_sets_closed_and_ends_stream(self) -> None:
        event_queue = make_queue()
        closed = asyncio.Event()
        async with event_queue.recv_guard("Test", closed):
            pass
        self.assertTrue(closed.is_set())
        self.assertIsNone(event_queue.error)
        self.assertEqual(await collect_texts(event_queue), [])

    async def test_websocket_closed_ok(self) -> None:
        event_queue = make_queue()
        closed = asyncio.Event()
        async with event_queue.recv_guard("Test", closed):
            raise ConnectionClosedOK(None, None)
        self.assertIsNone(event_queue.error)
        self.assertTrue(closed.is_set())

    async def test_unexpected_websocket_close_sets_error(self) -> None:
        event_queue = make_queue()
        async with event_queue.recv_guard("Test", asyncio.Event()):
            raise ConnectionClosed(Close(1006, "abnormal"), None)
        self.assertIsInstance(event_queue.error, RuntimeError)
        self.assertEqual(
            str(event_queue.error),
            "Test connection closed unexpectedly: received 1006 (abnormal closure [internal]) abnormal; "
            "no close frame sent",
        )

    async def test_cancelled_error_reraised(self) -> None:
        event_queue = make_queue()
        closed = asyncio.Event()
        with self.assertRaises(asyncio.CancelledError):
            async with event_queue.recv_guard("Test", closed):
                raise asyncio.CancelledError()
        self.assertTrue(closed.is_set())

    async def test_generic_exception_becomes_the_error(self) -> None:
        event_queue = make_queue()
        failure = ValueError("boom")
        async with event_queue.recv_guard("Test", asyncio.Event()):
            raise failure
        self.assertIs(event_queue.error, failure)

    async def test_exception_after_close_is_not_an_error(self) -> None:
        event_queue = make_queue()
        closed = asyncio.Event()
        closed.set()
        async with event_queue.recv_guard("Test", closed):
            raise Exception("expected after close")
        self.assertIsNone(event_queue.error)

    async def test_on_close_runs_after_error_is_set(self) -> None:
        event_queue = make_queue()
        failure = ValueError("fail")
        observed_errors = []

        async def on_close() -> None:
            observed_errors.append(event_queue.error)

        async with event_queue.recv_guard("Test", asyncio.Event(), on_close=on_close):
            raise failure
        self.assertEqual(observed_errors, [failure])


# ---------------------------------------------------------------------------
# Gemini clients target the API version that serves preview models
# ---------------------------------------------------------------------------

class ClientConstructed(Exception):
    """Stops the provider right after it builds its client, so no network call happens."""


class TestGeminiApiVersion(unittest.IsolatedAsyncioTestCase):
    async def test_live_provider_api_version(self) -> None:
        for config, expected_version in [
            (GeminiLiveSttConfig(api_key="fake-key"), "v1beta"),
            (GeminiLiveSttConfig(api_key="fake-key", api_version="v1"), "v1"),
        ]:
            with self.subTest(expected_version=expected_version):
                with unittest.mock.patch("google.genai.Client", side_effect=ClientConstructed) as client_class:
                    with self.assertRaises(ClientConstructed):
                        await GeminiLiveProvider(config).__aenter__()
                self.assertEqual(client_class.call_args.kwargs["http_options"].api_version, expected_version)
