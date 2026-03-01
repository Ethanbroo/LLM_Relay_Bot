"""Constants for the video pipeline.

Centralizes all magic numbers, default values, and codec presets.
"""

# Frame rates
DEFAULT_FPS = 30
MIN_FPS = 15
MAX_FPS = 60

# Resolution limits
MAX_DIMENSION = 3840  # 4K
MIN_DIMENSION = 240

# Duration limits (milliseconds)
MIN_CLIP_DURATION_MS = 500
MAX_CLIP_DURATION_MS = 300_000  # 5 minutes
MAX_TIMELINE_DURATION_MS = 600_000  # 10 minutes

# Transition defaults
DEFAULT_TRANSITION_DURATION_MS = 500
MAX_TRANSITION_DURATION_MS = 5000

# Text overlay defaults
DEFAULT_FONT_SIZE = 48
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 200
DEFAULT_TEXT_COLOR = "#FFFFFF"
DEFAULT_STROKE_COLOR = "#000000"
DEFAULT_STROKE_WIDTH = 2
DEFAULT_TEXT_PADDING = 40

# Audio defaults
DEFAULT_BGM_VOLUME = 0.3
DEFAULT_VOICEOVER_VOLUME = 1.0
DEFAULT_AUDIO_FADE_IN_MS = 1000
DEFAULT_AUDIO_FADE_OUT_MS = 2000

# FFmpeg encoding defaults
MP4_CRF = 18  # High quality (lower = better, 18 is visually lossless)
WEBM_CRF = 30
MP4_AUDIO_BITRATE = "192k"

# Ken Burns defaults
DEFAULT_ZOOM_START = 1.0
DEFAULT_ZOOM_END = 1.05  # Subtle 5% zoom
DEFAULT_PAN_START = 0.0
DEFAULT_PAN_END = 0.0

# Platform-specific maximum durations (seconds)
PLATFORM_MAX_DURATION = {
    "instagram_reel": 90,
    "instagram_story": 60,
    "instagram_feed": 60,
    "tiktok": 180,
    "youtube_short": 60,
    "youtube": 600,
}
