"""Animated geometric overlay effects.

Effects:
- grid_overlay: Animated grid lines
- circle_pulse: Expanding/pulsing circle overlay
- line_wipe: Animated diagonal line sweep
- border_frame: Decorative border overlay
"""

import math
import numpy as np
from PIL import Image, ImageDraw
from .registry import register_effect


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Convert hex color to RGBA tuple."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b, alpha)


@register_effect("grid_overlay")
def grid_overlay(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Animated grid lines overlay.

    params:
        spacing: int (pixels between grid lines, default 50)
        color: str (hex color, default "#FFFFFF")
        opacity: float (0.0–1.0, default 0.15)
        line_width: int (default 1)
        animate: bool (if True, grid scrolls with progress)
    """
    spacing = params.get("spacing", 50)
    color = params.get("color", "#FFFFFF")
    opacity = params.get("opacity", 0.15)
    line_width = params.get("line_width", 1)

    w, h = frame.size
    alpha = int(255 * opacity)
    rgba = _hex_to_rgba(color, alpha)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Offset for animation
    offset = 0
    if params.get("animate", False):
        offset = int(progress * spacing) % spacing

    # Vertical lines
    for x in range(-spacing + offset, w + spacing, spacing):
        draw.line([(x, 0), (x, h)], fill=rgba, width=line_width)

    # Horizontal lines
    for y in range(-spacing + offset, h + spacing, spacing):
        draw.line([(0, y), (w, y)], fill=rgba, width=line_width)

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")


@register_effect("circle_pulse")
def circle_pulse(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Expanding/pulsing circle overlay.

    params:
        color: str (hex color, default "#FFFFFF")
        opacity: float (0.0–1.0, default 0.3)
        line_width: int (circle stroke width, default 3)
        count: int (number of concentric circles, default 3)
        center_x: float (0.0–1.0, default 0.5)
        center_y: float (0.0–1.0, default 0.5)
    """
    color = params.get("color", "#FFFFFF")
    opacity = params.get("opacity", 0.3)
    line_width = params.get("line_width", 3)
    count = params.get("count", 3)
    center_x = params.get("center_x", 0.5)
    center_y = params.get("center_y", 0.5)

    w, h = frame.size
    cx = int(center_x * w)
    cy = int(center_y * h)
    max_radius = int(math.sqrt(w ** 2 + h ** 2) / 2)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for i in range(count):
        # Stagger circles at different phases
        phase = (progress + i / count) % 1.0
        radius = int(phase * max_radius)
        # Fade out as circle expands
        circle_opacity = int(255 * opacity * (1 - phase))
        if circle_opacity <= 0 or radius <= 0:
            continue

        rgba = _hex_to_rgba(color, circle_opacity)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=rgba, width=line_width,
        )

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")


@register_effect("line_wipe")
def line_wipe(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Animated diagonal line sweep.

    params:
        color: str (hex color, default "#FFFFFF")
        opacity: float (0.0–1.0, default 0.4)
        line_width: int (default 4)
        count: int (number of parallel lines, default 5)
        angle: float (degrees, default 45)
    """
    color = params.get("color", "#FFFFFF")
    opacity = params.get("opacity", 0.4)
    line_width = params.get("line_width", 4)
    count = params.get("count", 5)
    angle = params.get("angle", 45.0)

    w, h = frame.size
    alpha = int(255 * opacity)
    rgba = _hex_to_rgba(color, alpha)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    diag = math.sqrt(w ** 2 + h ** 2)
    angle_rad = math.radians(angle)
    spacing = diag / max(count, 1)

    # Sweep position based on progress
    sweep_offset = progress * diag * 1.5 - diag * 0.25

    for i in range(count):
        offset = sweep_offset + i * spacing
        # Line perpendicular to the angle
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        # Center point along the sweep direction
        cx = w / 2 + offset * cos_a
        cy = h / 2 + offset * sin_a
        # Line endpoints (perpendicular to angle, long enough to cross frame)
        perp_cos = math.cos(angle_rad + math.pi / 2)
        perp_sin = math.sin(angle_rad + math.pi / 2)
        x1 = int(cx + perp_cos * diag)
        y1 = int(cy + perp_sin * diag)
        x2 = int(cx - perp_cos * diag)
        y2 = int(cy - perp_sin * diag)
        draw.line([(x1, y1), (x2, y2)], fill=rgba, width=line_width)

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")


@register_effect("border_frame")
def border_frame(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Decorative border overlay.

    params:
        width: int (border width in pixels, default 20)
        color: str (hex color, default "#FFFFFF")
        opacity: float (0.0–1.0, default 0.6)
        rounded: bool (rounded corners, default False)
        corner_radius: int (default 30, only if rounded=True)
        animate: bool (if True, border fades in with progress)
    """
    border_width = params.get("width", 20)
    color = params.get("color", "#FFFFFF")
    opacity = params.get("opacity", 0.6)
    rounded = params.get("rounded", False)
    corner_radius = params.get("corner_radius", 30)

    if params.get("animate", False):
        opacity *= min(progress * 4, 1.0)

    w, h = frame.size
    alpha = int(255 * opacity)
    rgba = _hex_to_rgba(color, alpha)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if rounded:
        draw.rounded_rectangle(
            [border_width // 2, border_width // 2,
             w - border_width // 2, h - border_width // 2],
            radius=corner_radius, outline=rgba, width=border_width,
        )
    else:
        draw.rectangle(
            [border_width // 2, border_width // 2,
             w - border_width // 2, h - border_width // 2],
            outline=rgba, width=border_width,
        )

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")
