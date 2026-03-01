"""Pydantic models for the video pipeline.

These are the ONLY data structures that flow between video modules.
No raw dicts allowed. Every model is validated at construction time.
"""

from pydantic import BaseModel, Field, field_validator
from enum import Enum
from typing import Optional
from pathlib import Path


class AspectRatio(str, Enum):
    """Supported output aspect ratios."""
    SQUARE = "1:1"
    PORTRAIT_4_5 = "4:5"
    PORTRAIT_9_16 = "9:16"
    LANDSCAPE_16_9 = "16:9"
    LANDSCAPE_1_91 = "1.91:1"


class Resolution(BaseModel):
    """Pixel dimensions. Always calculated from aspect ratio + short edge."""
    width: int = Field(gt=0, le=3840)
    height: int = Field(gt=0, le=3840)

    @classmethod
    def from_aspect_ratio(cls, ratio: AspectRatio, short_edge: int = 1080) -> "Resolution":
        """Calculate resolution from aspect ratio.

        Args:
            ratio: Target aspect ratio
            short_edge: The shorter dimension in pixels (default 1080)

        Returns:
            Resolution with correct dimensions

        Examples:
            9:16 with short_edge=1080 -> 1080x1920
            16:9 with short_edge=1080 -> 1920x1080
            1:1 with short_edge=1080 -> 1080x1080
        """
        ratio_map = {
            AspectRatio.SQUARE: (1, 1),
            AspectRatio.PORTRAIT_4_5: (4, 5),
            AspectRatio.PORTRAIT_9_16: (9, 16),
            AspectRatio.LANDSCAPE_16_9: (16, 9),
            AspectRatio.LANDSCAPE_1_91: (191, 100),
        }
        w_ratio, h_ratio = ratio_map[ratio]
        if w_ratio <= h_ratio:
            width = short_edge
            height = int(short_edge * h_ratio / w_ratio)
        else:
            height = short_edge
            width = int(short_edge * w_ratio / h_ratio)
        # Ensure even dimensions (required by most video codecs)
        width = width if width % 2 == 0 else width + 1
        height = height if height % 2 == 0 else height + 1
        return cls(width=width, height=height)


class CodecPreset(str, Enum):
    """FFmpeg encoding presets. Slower = smaller file + better quality."""
    DRAFT = "ultrafast"
    STANDARD = "medium"
    HIGH_QUALITY = "slow"
    MAX_QUALITY = "veryslow"


class VideoFormat(str, Enum):
    MP4 = "mp4"
    WEBM = "webm"
    GIF = "gif"
    FRAMES = "frames"


class TransitionType(str, Enum):
    NONE = "none"
    FADE = "fade"
    DISSOLVE = "dissolve"
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"
    WIPE_UP = "wipe_up"
    WIPE_DOWN = "wipe_down"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"


class TextPosition(str, Enum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER = "center"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class EasingFunction(str, Enum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    SPRING = "spring"


class TextOverlay(BaseModel):
    """A text element rendered on top of video frames."""
    text: str = Field(min_length=1, max_length=500)
    font_path: Optional[str] = None
    font_size: int = Field(default=48, gt=8, le=200)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_color: Optional[str] = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_width: int = Field(default=2, ge=0, le=10)
    position: TextPosition = TextPosition.BOTTOM_CENTER
    padding_x: int = Field(default=40, ge=0)
    padding_y: int = Field(default=40, ge=0)
    start_time_ms: int = Field(ge=0)
    end_time_ms: int = Field(gt=0)
    fade_in_ms: int = Field(default=200, ge=0)
    fade_out_ms: int = Field(default=200, ge=0)
    animation: EasingFunction = EasingFunction.EASE_OUT

    @field_validator("end_time_ms")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_time_ms")
        if start is not None and v <= start:
            raise ValueError("end_time_ms must be greater than start_time_ms")
        return v


class AudioTrack(BaseModel):
    """An audio element in the timeline."""
    source_path: str
    start_time_ms: int = Field(ge=0)
    trim_start_ms: int = Field(default=0, ge=0)
    trim_end_ms: Optional[int] = Field(default=None)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)


class Clip(BaseModel):
    """A single visual segment in the timeline.

    A clip is either:
    - A static image (displayed for duration_ms)
    - A video file (trimmed and placed at a timeline position)
    - An AI-generated image (generated on-the-fly by the image agent)
    """
    clip_id: str = Field(description="Unique identifier, e.g. 'clip_001'")
    source_type: str = Field(description="'image', 'video', or 'ai_generated'")
    source_path: Optional[str] = Field(
        default=None,
        description="Path to source file. None if ai_generated."
    )
    ai_prompt: Optional[str] = Field(
        default=None,
        description="Prompt for AI generation. Only if source_type='ai_generated'."
    )
    character_id: Optional[str] = Field(
        default=None,
        description="Character ID for face verification. Only for ai_generated clips with faces."
    )
    duration_ms: int = Field(gt=0, le=300_000)
    transition_in: TransitionType = TransitionType.NONE
    transition_in_duration_ms: int = Field(default=500, ge=0, le=5000)
    transition_out: TransitionType = TransitionType.NONE
    transition_out_duration_ms: int = Field(default=500, ge=0, le=5000)
    zoom_start: float = Field(default=1.0, ge=0.5, le=3.0)
    zoom_end: float = Field(default=1.0, ge=0.5, le=3.0)
    pan_x_start: float = Field(default=0.0, ge=-1.0, le=1.0)
    pan_x_end: float = Field(default=0.0, ge=-1.0, le=1.0)
    pan_y_start: float = Field(default=0.0, ge=-1.0, le=1.0)
    pan_y_end: float = Field(default=0.0, ge=-1.0, le=1.0)
    text_overlays: list[TextOverlay] = Field(default_factory=list)
    effects: list[tuple[str, dict]] = Field(
        default_factory=list,
        description="Effect chain to apply to this clip. List of (effect_name, params) tuples."
    )

    @field_validator("ai_prompt")
    @classmethod
    def ai_prompt_requires_source_type(cls, v, info):
        if v is not None and info.data.get("source_type") != "ai_generated":
            raise ValueError("ai_prompt only valid when source_type='ai_generated'")
        return v

    @field_validator("source_path")
    @classmethod
    def source_path_required_for_non_ai(cls, v, info):
        if v is None and info.data.get("source_type") in ("image", "video"):
            raise ValueError("source_path required for image/video clips")
        return v


class Timeline(BaseModel):
    """The complete video specification.

    This is the central data structure. The LLM generates a storyboard, which is
    converted into a Timeline, which is then rendered by the compositor + encoder.
    """
    timeline_id: str
    title: str
    description: Optional[str] = None
    resolution: Resolution
    fps: int = Field(default=30, ge=15, le=60)
    clips: list[Clip] = Field(min_length=1)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    global_text_overlays: list[TextOverlay] = Field(
        default_factory=list,
        description="Text that appears across all clips"
    )
    global_effects: list[tuple[str, dict]] = Field(
        default_factory=list,
        description="Effect chain applied to every frame after clip compositing."
    )
    background_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    output_format: VideoFormat = VideoFormat.MP4
    codec_preset: CodecPreset = CodecPreset.STANDARD

    @property
    def total_duration_ms(self) -> int:
        """Total timeline duration = sum of clip durations minus transition overlaps."""
        if not self.clips:
            return 0
        total = sum(c.duration_ms for c in self.clips)
        for i in range(len(self.clips) - 1):
            overlap = min(
                self.clips[i].transition_out_duration_ms,
                self.clips[i + 1].transition_in_duration_ms,
            )
            total -= overlap
        return total

    @property
    def total_frames(self) -> int:
        return int(self.total_duration_ms / 1000 * self.fps)


class Storyboard(BaseModel):
    """LLM-generated storyboard before conversion to Timeline.

    This is what the content agent produces. It's a higher-level description
    that gets compiled into a precise Timeline.
    """
    concept: str = Field(description="One-sentence video concept")
    target_platform: str = Field(
        description="'instagram_reel', 'tiktok', 'youtube_short', 'instagram_story'"
    )
    target_duration_seconds: int = Field(ge=3, le=180)
    scenes: list[dict] = Field(description="List of scene descriptions from LLM")
    music_mood: Optional[str] = Field(
        default=None,
        description="e.g. 'upbeat electronic', 'calm ambient'"
    )
    voiceover_text: Optional[str] = None
    character_ids: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    caption: Optional[str] = None
