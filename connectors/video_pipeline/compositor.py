"""Frame-by-frame compositing engine.

Renders individual frames from a Timeline specification. Each frame is a
PIL Image at the Timeline's resolution.

The compositor is STATELESS: given a timeline and a frame number, it produces
exactly one frame. This enables parallel rendering.
"""

import math
import logging
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from .schemas import (
    Timeline, Clip, TextOverlay, TextPosition,
    TransitionType, EasingFunction
)

logger = logging.getLogger(__name__)


# --- Easing Functions ---

def _ease(t: float, easing: EasingFunction) -> float:
    """Map t in [0,1] through an easing function."""
    t = max(0.0, min(1.0, t))
    if easing == EasingFunction.LINEAR:
        return t
    elif easing == EasingFunction.EASE_IN:
        return t * t
    elif easing == EasingFunction.EASE_OUT:
        return 1 - (1 - t) * (1 - t)
    elif easing == EasingFunction.EASE_IN_OUT:
        return 3 * t * t - 2 * t * t * t
    elif easing == EasingFunction.SPRING:
        return 1 - math.exp(-6 * t) * math.cos(t * math.pi * 2)
    return t


# --- Image Scaling ---

def _fit_image_to_frame(
    img: Image.Image,
    target_width: int,
    target_height: int,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Image.Image:
    """Scale and crop an image to fill the target dimensions exactly.

    Uses "cover" strategy (like CSS background-size: cover).
    """
    src_w, src_h = img.size
    target_aspect = target_width / target_height
    src_aspect = src_w / src_h

    if src_aspect > target_aspect:
        scale = target_height / src_h
    else:
        scale = target_width / src_w

    scale *= zoom

    # Use round() for deterministic cross-platform rounding (int() truncates,
    # which can differ by ±1 across platforms due to floating-point)
    new_w = round(src_w * scale)
    new_h = round(src_h * scale)

    resized = img.resize((new_w, new_h), Image.LANCZOS)

    max_offset_x = max(0, new_w - target_width)
    max_offset_y = max(0, new_h - target_height)

    offset_x = round((1 + pan_x) / 2 * max_offset_x)
    offset_y = round((1 + pan_y) / 2 * max_offset_y)

    cropped = resized.crop((
        offset_x,
        offset_y,
        offset_x + target_width,
        offset_y + target_height,
    ))

    return cropped


# --- Transition Blending ---

def _blend_transition(
    frame_a: Image.Image,
    frame_b: Image.Image,
    progress: float,
    transition_type: TransitionType,
) -> Image.Image:
    """Blend two frames together using a transition effect."""
    w, h = frame_a.size
    progress = max(0.0, min(1.0, progress))

    if transition_type == TransitionType.NONE:
        return frame_b if progress >= 0.5 else frame_a

    elif transition_type in (TransitionType.FADE, TransitionType.DISSOLVE):
        return Image.blend(frame_a, frame_b, progress)

    elif transition_type == TransitionType.WIPE_LEFT:
        split_x = round(w * progress)
        result = frame_a.copy()
        result.paste(frame_b.crop((w - split_x, 0, w, h)), (w - split_x, 0))
        return result

    elif transition_type == TransitionType.WIPE_RIGHT:
        split_x = round(w * progress)
        result = frame_a.copy()
        result.paste(frame_b.crop((0, 0, split_x, h)), (0, 0))
        return result

    elif transition_type == TransitionType.WIPE_UP:
        split_y = round(h * progress)
        result = frame_a.copy()
        result.paste(frame_b.crop((0, h - split_y, w, h)), (0, h - split_y))
        return result

    elif transition_type == TransitionType.WIPE_DOWN:
        split_y = round(h * progress)
        result = frame_a.copy()
        result.paste(frame_b.crop((0, 0, w, split_y)), (0, 0))
        return result

    elif transition_type == TransitionType.SLIDE_LEFT:
        offset = round(w * (1 - progress))
        result = Image.new("RGB", (w, h))
        result.paste(frame_a, (offset - w, 0))
        result.paste(frame_b, (offset, 0))
        return result

    elif transition_type == TransitionType.SLIDE_RIGHT:
        offset = round(w * progress)
        result = Image.new("RGB", (w, h))
        result.paste(frame_a, (offset, 0))
        result.paste(frame_b, (offset - w, 0))
        return result

    elif transition_type == TransitionType.ZOOM_IN:
        zoom = 1.0 + progress * 0.3
        zoomed_a = _fit_image_to_frame(frame_a, w, h, zoom=zoom)
        return Image.blend(zoomed_a, frame_b, progress)

    elif transition_type == TransitionType.ZOOM_OUT:
        zoom = 1.3 - progress * 0.3
        zoomed_b = _fit_image_to_frame(frame_b, w, h, zoom=zoom)
        return Image.blend(frame_a, zoomed_b, progress)

    return Image.blend(frame_a, frame_b, progress)


# --- Text Rendering ---

def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Convert hex color string to RGBA tuple."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b, alpha)


def _load_font(font_path: Optional[str], font_size: int) -> ImageFont.FreeTypeFont:
    """Load a font, falling back to defaults if unavailable."""
    if font_path:
        try:
            return ImageFont.truetype(font_path, font_size)
        except (IOError, OSError):
            pass

    # Try common system font paths
    system_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/System/Library/Fonts/SFNSText.ttf",  # macOS newer
        "C:\\Windows\\Fonts\\arial.ttf",  # Windows
    ]
    for path in system_fonts:
        try:
            return ImageFont.truetype(path, font_size)
        except (IOError, OSError):
            continue

    return ImageFont.load_default()


def _render_text_overlay(
    frame: Image.Image,
    overlay: TextOverlay,
    frame_time_ms: int,
) -> Image.Image:
    """Render a text overlay onto a frame with fade animation."""
    if frame_time_ms < overlay.start_time_ms or frame_time_ms > overlay.end_time_ms:
        return frame

    elapsed = frame_time_ms - overlay.start_time_ms
    remaining = overlay.end_time_ms - frame_time_ms

    opacity = 1.0
    if overlay.fade_in_ms > 0 and elapsed < overlay.fade_in_ms:
        opacity = _ease(elapsed / overlay.fade_in_ms, overlay.animation)
    if overlay.fade_out_ms > 0 and remaining < overlay.fade_out_ms:
        opacity = min(opacity, _ease(remaining / overlay.fade_out_ms, EasingFunction.EASE_IN))

    if opacity <= 0.01:
        return frame

    w, h = frame.size
    txt_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(txt_layer)

    font = _load_font(overlay.font_path, overlay.font_size)

    bbox = draw.textbbox((0, 0), overlay.text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    position_map = {
        TextPosition.TOP_LEFT: (overlay.padding_x, overlay.padding_y),
        TextPosition.TOP_CENTER: ((w - text_w) // 2, overlay.padding_y),
        TextPosition.TOP_RIGHT: (w - text_w - overlay.padding_x, overlay.padding_y),
        TextPosition.CENTER: ((w - text_w) // 2, (h - text_h) // 2),
        TextPosition.BOTTOM_LEFT: (overlay.padding_x, h - text_h - overlay.padding_y),
        TextPosition.BOTTOM_CENTER: ((w - text_w) // 2, h - text_h - overlay.padding_y),
        TextPosition.BOTTOM_RIGHT: (w - text_w - overlay.padding_x, h - text_h - overlay.padding_y),
    }

    x, y = position_map[overlay.position]
    # Explicit rounding for deterministic pixel positioning
    x = round(x)
    y = round(y)
    alpha = round(255 * opacity)

    # Draw stroke first
    if overlay.stroke_color and overlay.stroke_width > 0:
        stroke_rgba = _hex_to_rgba(overlay.stroke_color, alpha)
        draw.text(
            (x, y), overlay.text, font=font, fill=stroke_rgba,
            stroke_width=overlay.stroke_width, stroke_fill=stroke_rgba
        )

    # Draw main text
    text_rgba = _hex_to_rgba(overlay.color, alpha)
    draw.text((x, y), overlay.text, font=font, fill=text_rgba)

    # Composite
    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, txt_layer)
    return result.convert("RGB")


# --- Main Compositor ---

class FrameCompositor:
    """Renders individual frames from a Timeline.

    Usage:
        compositor = FrameCompositor(timeline, image_cache)
        for frame_num in range(timeline.total_frames):
            frame = compositor.render_frame(frame_num)
    """

    def __init__(self, timeline: Timeline, image_cache: dict[str, Image.Image]):
        """
        Args:
            timeline: The complete Timeline specification
            image_cache: Pre-loaded images keyed by clip_id.
                         Every clip with source_type 'image' or 'ai_generated'
                         MUST have its resolved image in this dict before rendering.
        """
        self.timeline = timeline
        self.image_cache = image_cache
        self._build_frame_index()

    def _build_frame_index(self):
        """Pre-compute which clip(s) are active at each millisecond."""
        self.clip_ranges = []
        current_ms = 0

        for i, clip in enumerate(self.timeline.clips):
            start = current_ms
            end = current_ms + clip.duration_ms
            self.clip_ranges.append((start, end, i))

            if i < len(self.timeline.clips) - 1:
                next_clip = self.timeline.clips[i + 1]
                overlap = min(
                    clip.transition_out_duration_ms,
                    next_clip.transition_in_duration_ms,
                )
                current_ms = end - overlap
            else:
                current_ms = end

    def _get_active_clips_at(self, time_ms: int) -> list:
        """Find which clips are active at a given time."""
        active = []
        for start, end, idx in self.clip_ranges:
            if start <= time_ms < end:
                local_time = time_ms - start
                progress = local_time / (end - start) if end > start else 0
                active.append((idx, local_time, progress))
        return active

    def _render_clip_frame(
        self,
        clip: Clip,
        local_time_ms: int,
        progress: float,
    ) -> Image.Image:
        """Render a single clip at a given local time position."""
        w = self.timeline.resolution.width
        h = self.timeline.resolution.height

        img = self.image_cache.get(clip.clip_id)
        if img is None:
            img = Image.new("RGB", (w, h), self.timeline.background_color)
            draw = ImageDraw.Draw(img)
            draw.text((w // 4, h // 2), f"MISSING: {clip.clip_id}", fill="#FF0000")
            return img

        # Ken Burns
        zoom = clip.zoom_start + (clip.zoom_end - clip.zoom_start) * progress
        pan_x = clip.pan_x_start + (clip.pan_x_end - clip.pan_x_start) * progress
        pan_y = clip.pan_y_start + (clip.pan_y_end - clip.pan_y_start) * progress

        frame = _fit_image_to_frame(img, w, h, zoom=zoom, pan_x=pan_x, pan_y=pan_y)

        for overlay in clip.text_overlays:
            frame = _render_text_overlay(frame, overlay, local_time_ms)

        # Apply clip-level effects
        if clip.effects:
            from .effects.pipeline import EffectChain
            chain = EffectChain(clip.effects)
            frame = chain.apply(frame, progress)

        return frame

    def render_frame(self, frame_number: int) -> Image.Image:
        """Render a single frame by its index."""
        w = self.timeline.resolution.width
        h = self.timeline.resolution.height
        # Use round() for deterministic frame-to-time mapping
        time_ms = round(frame_number / self.timeline.fps * 1000)

        active = self._get_active_clips_at(time_ms)

        if len(active) == 0:
            return Image.new("RGB", (w, h), self.timeline.background_color)

        elif len(active) == 1:
            idx, local_time, progress = active[0]
            clip = self.timeline.clips[idx]
            frame = self._render_clip_frame(clip, local_time, progress)

        else:
            # Transition between two clips
            idx_a, local_a, progress_a = active[0]
            idx_b, local_b, progress_b = active[1]
            clip_a = self.timeline.clips[idx_a]
            clip_b = self.timeline.clips[idx_b]

            frame_a = self._render_clip_frame(clip_a, local_a, progress_a)
            frame_b = self._render_clip_frame(clip_b, local_b, progress_b)

            transition_duration = min(
                clip_a.transition_out_duration_ms,
                clip_b.transition_in_duration_ms,
            )
            t_progress = local_b / transition_duration if transition_duration > 0 else 1.0

            frame = _blend_transition(frame_a, frame_b, t_progress, clip_b.transition_in)

        # Global text overlays
        for overlay in self.timeline.global_text_overlays:
            frame = _render_text_overlay(frame, overlay, time_ms)

        # Global effects (applied to every frame after compositing)
        if self.timeline.global_effects:
            from .effects.pipeline import EffectChain
            global_progress = time_ms / max(self.timeline.total_duration_ms, 1)
            chain = EffectChain(self.timeline.global_effects)
            frame = chain.apply(frame, global_progress)

        return frame
