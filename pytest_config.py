from pathlib import Path

from universal_realtime_stt_tts import config


# ---------------------------------------------------------------------------
# File Configuration
# ---------------------------------------------------------------------------

BASE_PATH = Path(__file__).parent

# Path for test reports
OUT_PATH = BASE_PATH / "out"
OUT_PATH.mkdir(exist_ok=True)

# Path to look for test assets
ASSETS_DIR = Path(BASE_PATH / "assets")
assert ASSETS_DIR.exists()

# Path to save library logs
LOG_PATH = BASE_PATH / "log"
LOG_PATH.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Streaming / Test Suite Configuration
# ---------------------------------------------------------------------------

CHUNK_MS = 200

# ---------------------------------------------------------------------------
# Test Suite Configuration
# ---------------------------------------------------------------------------

# Stream factor: 0.0 = stream as fast as possible (no pacing), 1.0 = stream at natural pace.
TEST_REALTIME_FACTOR = 1.0

# Silence padding at the beginning and end.
FINAL_SILENCE_S = 2.0
assert FINAL_SILENCE_S > config.STT_VAD_SILENCE_THRESHOLD_S, "Final silence must be longer than VAD silence threshold."
