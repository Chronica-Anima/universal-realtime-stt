# Implementation Plan: universal-realtime-audio

## Context

The `universal-realtime-stt` library needs to evolve into `universal-realtime-audio` — adding TTS, switching ElevenLabs/Speechmatics STT to official SDKs, adding diarization, and restructuring the package for proper distribution. The spec lives at `doc/spec_universal_realtime_audio.md`. This plan also adds a unit test suite with no real API calls.

---

## Phase 1: Package Rename (`lib/` -> `universal_realtime_audio/`)

**Files changed**: every file in the repo

1. `git mv lib/ universal_realtime_audio/`
2. Update all internal imports (8 files inside the package): `from lib.` -> `from universal_realtime_audio.`
3. Update external consumers:
    - `helpers/transcribe.py` (lines 10-11)
    - `tests/test_stt.py` (lines 29-35)
    - `benchmark.py` (lines 52-58)
4. Update `utils.py` line 21: `PROJECT_PREFIXES = ("universal_realtime_audio.", "__main__")`
5. Add `[tool.setuptools.packages.find]` to `pyproject.toml`: `include = ["universal_realtime_audio*"]`
6. Update `stt_provider.py` module docstring: `lib/stt_provider_<name>.py` -> `universal_realtime_audio/stt_provider_<name>.py`

**Verify**: `pytest tests/test_diff.py -v` passes; `python -c "from universal_realtime_audio.stt_provider import TranscriptEvent"`

---

## Phase 2: Config Decoupling + Class Renames

**Files changed**: all 6 `stt_provider_*.py`, `utils.py`, `tests/test_stt.py`, `benchmark.py`

### 2a. Decouple provider configs from `config.py`

Each provider config stops importing from `config.py` and uses literal defaults:

| Provider             | Remove imports                                               | Replace with literals                         |
|----------------------|--------------------------------------------------------------|-----------------------------------------------|
| ElevenLabsSttConfig  | `AUDIO_SAMPLE_RATE`, `STT_LANGUAGE_ISO_639_1`, 4 VAD params | `16000`, `"cs"`, `0.7`, `0.6`, `300`, `1000` |
| SpeechmaticsSttConfig| `AUDIO_SAMPLE_RATE`, `STT_LANGUAGE_ISO_639_1`, VAD threshold | `16000`, `"cs"`, `0.7`                        |
| DeepgramSttConfig    | `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, VAD, language         | `16000`, `1`, `0.7`, `"cs"`                  |
| CartesiaSttConfig    | `AUDIO_SAMPLE_RATE`, `AUDIO_ENCODING`, language, VAD         | `16000`, `"pcm_s16le"`, `"cs"`, `0.7`        |
| GoogleSttConfig      | `AUDIO_SAMPLE_RATE`, `STT_LANGUAGE_BCP_47`                   | `16000`, `"cs-CZ"`                            |
| GeminiLiveSttConfig  | `AUDIO_SAMPLE_RATE`                                          | `16000`                                       |

### 2b. Decouple `utils.py`

Change `setup_logging()` to accept `log_dir: Path | None = None` parameter instead of importing `LOG_PATH`. Callers pass `LOG_PATH` from their own `config.py` import.

### 2c. Rename classes

| File                           | Old                          | New                    |
|--------------------------------|------------------------------|------------------------|
| `stt_provider_elevenlabs.py`   | `ElevenLabsRealtimeProvider` | `ElevenLabsSttProvider`|
| `stt_provider_speechmatics.py` | `SpeechmaticsRealtimeProvider`| `SpeechmaticsSttProvider`|

Update references in `tests/test_stt.py` and `benchmark.py`.

**Verify**: `pytest tests/test_diff.py -v`; `python -c "from universal_realtime_audio.stt_provider_elevenlabs import ElevenLabsSttProvider"`

---

## Phase 3: TranscriptEvent + Orchestration Changes

**Files changed**: `stt_provider.py`, `stt.py`, `transcript_ingest.py`, `transcribe.py`

### 3a. Add `speaker` field to `TranscriptEvent`

```python
@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    speaker: str | None = None
```

Backward-compatible — existing positional calls still work.

### 3b. Change `transcript_queue` type

`stt.py`: signature changes from `Queue[Optional[str]]` to `Queue[TranscriptEvent | None]`

### 3c. Route all events through queue

`_receiver` in `stt.py`: put full `TranscriptEvent` objects (partials + finals) instead of only final text strings. Filter empty text.

### 3d. Add silence keepalive to `_sender`

```python
silence = b"\x00\x00" * 1600  # 100ms at 16kHz
try:
    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.2)
except asyncio.TimeoutError:
    chunk = silence
```

### 3e. Update `transcript_ingest.py`

- Accept `Queue[TranscriptEvent | None]` instead of `Queue[str | None]`
- Import `TranscriptEvent`
- Filter: collect only `is_final` events' text
- Return type stays `List[str]`

### 3f. Update `transcribe.py`

- Queue type annotation: `Queue[TranscriptEvent | None]`
- Import `TranscriptEvent`

### 3g. Update `stt.py` module docstring

Queue contract: `transcript_queue (TranscriptEvent | None)` instead of `(str | None)`.

**Verify**: `pytest tests/test_diff.py -v`

---

## Phase 4: SDK Rewrites (ElevenLabs + Speechmatics STT)

**Files changed**: `stt_provider_elevenlabs.py`, `stt_provider_speechmatics.py`

### 4a. ElevenLabs STT -> `elevenlabs` SDK

Full rewrite per spec. Key changes:
- Remove `websockets` imports, manual URL building, `_recv_loop`
- Use `client.speech_to_text.realtime.connect(RealtimeAudioOptions(...))`
- Callback-based events: `connection.on(RealtimeEvents.PARTIAL_TRANSCRIPT, ...)`
- Both partials and finals emit `TranscriptEvent` into `_events_q`
- Config: remove `base_url`, `commit_strategy`; update defaults (`vad_silence_threshold_s=1.0`, `vad_threshold=0.6`)

### 4b. Speechmatics STT -> `speechmatics-rt` SDK + diarization

Full rewrite per spec. Key changes:
- Remove `websockets` imports, manual `StartRecognition`/`EndOfStream`, `_recv_loop`, `_seq_no`
- Use `AsyncClient(auth=StaticKeyAuth(...))` + `client.start_session()`
- Decorator-based events: `@client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)`
- Emit partials with `is_final=False`
- Add `_extract_speaker()` for diarization (majority-vote from word-level results)
- `send_audio()`: cast to `bytes()` (SDK rejects `bytearray`)
- Config: add `diarization`, `speaker_diarization_config`, `end_of_utterance_silence_trigger`; remove `enable_partials`, `encoding`

**Verify**: Integration tests (real API calls, separate from unit tests):
- `pytest tests/test_stt.py::TestStt::test_eleven_labs -v`
- `pytest tests/test_stt.py::TestStt::test_speechmatics -v`

---

## Phase 5: TTS Protocol + ElevenLabs TTS

**New files**: `tts_provider.py`, `tts_provider_elevenlabs.py`

### 5a. `universal_realtime_audio/tts_provider.py`

```python
class RealtimeTtsProvider(Protocol):
    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]: ...
```

Contract: yields raw PCM 16-bit LE, 16 kHz mono chunks.

### 5b. `universal_realtime_audio/tts_provider_elevenlabs.py`

- `ElevenLabsTtsConfig`: `api_key`, `voice_id`, `model`, `stability`, `speed`
- `ElevenLabsTtsProvider.synthesize()`: uses `client.text_to_speech.convert()` with `output_format="pcm_16000"`

**Verify**: `python -c "from universal_realtime_audio.tts_provider import RealtimeTtsProvider"`

---

## Phase 6: Packaging, Tests, Documentation

### 6a. Update `pyproject.toml`

```toml
name = "universal-realtime-audio"
version = "0.2.0"
dependencies = ["websockets>=16.0.0", "python-dotenv>=1.2.1"]

[project.optional-dependencies]
elevenlabs = ["elevenlabs>=2.0.0"]
speechmatics = ["speechmatics-rt>=1.0.0"]
google = ["google-cloud-speech>=2.36.0"]
gemini = ["google-genai>=1.64.0"]
benchmark = ["diff-match-patch>=20241021"]
all = ["universal-realtime-audio[elevenlabs,speechmatics,google,gemini,benchmark]"]
```

### 6b. Update `requirements.txt`

Add `elevenlabs>=2.0.0`, `speechmatics-rt>=1.0.0` as new dependencies.

### 6c. Unit tests: `tests/test_unit.py` (no API calls)

Tests using mock providers — no network, no API keys:

**TranscriptEvent**:
- Construction with/without `speaker`, frozen enforcement

**stt_session_task orchestration** (mock provider that yields preset events):
- Audio chunks forwarded to `provider.send_audio()`
- TranscriptEvents appear in `transcript_queue`
- `None` sentinel on clean close
- `end_audio()` called after `None` audio chunk
- Silence keepalive: verify silence bytes sent when audio queue is idle

**_receiver routing**:
- Both partial and final events arrive in `transcript_queue`
- Empty-text events filtered

**transcript_ingest_task**:
- Only `is_final` texts collected, partials ignored
- `None` sentinel stops loop

**TTS protocol compliance**:
- Mock provider satisfies `RealtimeTtsProvider`

**ElevenLabs STT provider** (mock SDK connection):
- Verify `_on_partial` creates `TranscriptEvent(is_final=False)`
- Verify `_on_committed` creates `TranscriptEvent(is_final=True)`
- Verify `_on_error` sets error and terminates events
- Verify `_on_close` terminates events

**Speechmatics STT provider** (mock SDK client):
- Verify `_extract_speaker()` majority-vote logic
- Verify `_extract_speaker()` filters out `"UU"`
- Verify `send_audio()` casts to `bytes`

### 6d. Update `CLAUDE.md`

- `lib/` -> `universal_realtime_audio/` throughout
- Class name updates
- Add TTS section
- Add `pytest tests/test_unit.py -v` to commands
- Update "Adding a New Provider" section

### 6e. Create `CHANGELOG.md`

Version 0.2.0 entry covering: package rename, SDK migrations, diarization, TTS, optional deps.

**Verify**: `pip install -e ".[all]"` succeeds; `pytest tests/test_unit.py -v` passes; `pytest tests/test_diff.py -v` passes

---

## Phase Dependencies

```
Phase 1 (rename) -> Phase 2 (decouple + renames) -> Phase 3 (events + orchestration) -> Phase 4 (SDK rewrites)
                                                                                      -> Phase 5 (TTS, parallel to 4)
                                                                                                -> Phase 6 (packaging + tests)
```

---

## Files Summary

| Action  | File                                               |
|---------|----------------------------------------------------|
| Rename  | `lib/` -> `universal_realtime_audio/`              |
| Rewrite | `stt_provider_elevenlabs.py` (SDK)                 |
| Rewrite | `stt_provider_speechmatics.py` (SDK + diarization) |
| Modify  | `stt_provider.py` (speaker field)                  |
| Modify  | `stt.py` (all events, keepalive, queue type)       |
| Modify  | `stt_provider_cartesia.py` (imports, config)       |
| Modify  | `stt_provider_deepgram.py` (imports, config)       |
| Modify  | `stt_provider_google.py` (imports, config)         |
| Modify  | `stt_provider_gemini_live.py` (imports, config)    |
| Modify  | `utils.py` (imports, log_dir param)                |
| Modify  | `helpers/transcribe.py` (imports, queue type)      |
| Modify  | `helpers/transcript_ingest.py` (TranscriptEvent)   |
| Modify  | `tests/test_stt.py` (imports, class names)         |
| Modify  | `benchmark.py` (imports, class names)              |
| Modify  | `pyproject.toml` (name, version, deps)             |
| Modify  | `requirements.txt`                                 |
| Modify  | `CLAUDE.md`                                        |
| Create  | `tts_provider.py`                                  |
| Create  | `tts_provider_elevenlabs.py`                       |
| Create  | `tests/test_unit.py`                               |
| Create  | `CHANGELOG.md`                                     |
