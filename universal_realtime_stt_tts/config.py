# Universal STT Configuration (provider-independent)
#
# Central defaults for all providers. Each provider config dataclass imports
# from here so that changing a value propagates everywhere.  The app can
# override any value at instantiation time via the factory.

# Language codes
STT_LANGUAGE_ISO_639_1 = "cs"
STT_LANGUAGE_BCP_47 = "cs-CZ"

# VAD / endpointing
STT_VAD_SILENCE_THRESHOLD_S = 0.7  # seconds
STT_VAD_THRESHOLD = 0.6
STT_MIN_SILENCE_DURATION_MS = 300  # milliseconds
STT_MIN_SPEECH_DURATION_MS = 1000  # milliseconds

# Audio format
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM
AUDIO_ENCODING = "pcm_s16le"
