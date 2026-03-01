"""Particle system effects.

Effects:
- snow: Falling snow particles
- rain: Rain streaks
- dust: Floating dust motes
- confetti: Falling confetti pieces
"""

import math
import numpy as np
from PIL import Image, ImageDraw
from .registry import register_effect


def _generate_particles(
    count: int,
    seed: int,
    w: int,
    h: int,
) -> np.ndarray:
    """Generate deterministic particle positions.

    Returns array of shape (count, 2) with (x, y) base positions in [0, 1].
    Using a fixed seed makes this reproducible for a given frame.
    """
    rng = np.random.RandomState(seed)
    return rng.uniform(0, 1, (count, 2))


@register_effect("snow")
def snow(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Falling snow particles.

    params:
        count: int (number of snowflakes, default 100)
        size_range: tuple (min, max radius, default (2, 6))
        speed: float (fall speed multiplier, default 1.0)
        wind: float (-1.0 to 1.0, horizontal drift, default 0.1)
        opacity: float (0.0–1.0, default 0.7)
    """
    count = params.get("count", 100)
    size_range = params.get("size_range", (2, 6))
    speed = params.get("speed", 1.0)
    wind = params.get("wind", 0.1)
    opacity = params.get("opacity", 0.7)

    w, h = frame.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Generate stable particle base positions
    particles = _generate_particles(count, seed=42, w=w, h=h)
    rng = np.random.RandomState(42)
    sizes = rng.uniform(size_range[0], size_range[1], count)
    phases = rng.uniform(0, 1, count)  # Stagger fall timing

    for i in range(count):
        bx, by = particles[i]
        size = sizes[i]

        # Animate: fall downward, drift with wind
        fall_progress = (progress * speed + phases[i]) % 1.0
        x = int((bx + wind * fall_progress) * w) % w
        y = int(fall_progress * (h + size * 4)) - int(size * 2)

        if y < -size or y > h + size:
            continue

        alpha = int(255 * opacity * (0.5 + 0.5 * (size - size_range[0]) / max(size_range[1] - size_range[0], 1)))
        r = int(size)
        draw.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=(255, 255, 255, alpha),
        )

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")


@register_effect("rain")
def rain(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Rain streaks.

    params:
        count: int (number of rain drops, default 150)
        length: int (streak length in pixels, default 20)
        speed: float (fall speed multiplier, default 2.0)
        angle: float (degrees from vertical, default 10)
        opacity: float (0.0–1.0, default 0.4)
        color: str (hex color, default "#C0D0E0")
    """
    count = params.get("count", 150)
    length = params.get("length", 20)
    speed = params.get("speed", 2.0)
    angle = params.get("angle", 10.0)
    opacity_val = params.get("opacity", 0.4)
    color_hex = params.get("color", "#C0D0E0")

    # Parse hex color
    ch = color_hex.lstrip("#")
    cr, cg, cb = int(ch[0:2], 16), int(ch[2:4], 16), int(ch[4:6], 16)

    w, h = frame.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    particles = _generate_particles(count, seed=99, w=w, h=h)
    rng = np.random.RandomState(99)
    phases = rng.uniform(0, 1, count)

    angle_rad = math.radians(angle)
    dx = math.sin(angle_rad) * length
    dy = math.cos(angle_rad) * length

    for i in range(count):
        bx, by = particles[i]
        fall_progress = (progress * speed + phases[i]) % 1.0

        x = int(bx * w + math.sin(angle_rad) * fall_progress * h * 0.3) % w
        y = int(fall_progress * (h + length * 2)) - length

        if y < -length or y > h + length:
            continue

        alpha = int(255 * opacity_val)
        draw.line(
            [(x, y), (int(x + dx), int(y + dy))],
            fill=(cr, cg, cb, alpha), width=1,
        )

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")


@register_effect("dust")
def dust(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Floating dust motes.

    params:
        count: int (number of particles, default 60)
        size_range: tuple (min, max radius, default (1, 4))
        speed: float (drift speed, default 0.3)
        opacity: float (0.0–1.0, default 0.4)
        color: str (hex color, default "#FFE8C0")
    """
    count = params.get("count", 60)
    size_range = params.get("size_range", (1, 4))
    speed = params.get("speed", 0.3)
    opacity_val = params.get("opacity", 0.4)
    color_hex = params.get("color", "#FFE8C0")

    ch = color_hex.lstrip("#")
    cr, cg, cb = int(ch[0:2], 16), int(ch[2:4], 16), int(ch[4:6], 16)

    w, h = frame.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    particles = _generate_particles(count, seed=77, w=w, h=h)
    rng = np.random.RandomState(77)
    sizes = rng.uniform(size_range[0], size_range[1], count)
    drift_angles = rng.uniform(0, 2 * math.pi, count)

    for i in range(count):
        bx, by = particles[i]
        size = sizes[i]

        # Gentle drifting motion
        drift = progress * speed
        x = int((bx + math.sin(drift_angles[i] + drift * 3) * 0.05) * w) % w
        y = int((by + math.cos(drift_angles[i] + drift * 2) * 0.05) * h) % h

        # Opacity pulses with a slow sine wave
        pulse = 0.5 + 0.5 * math.sin(progress * math.pi * 4 + i)
        alpha = int(255 * opacity_val * pulse)
        r = int(size)
        draw.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=(cr, cg, cb, alpha),
        )

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")


@register_effect("confetti")
def confetti(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Falling confetti pieces.

    params:
        count: int (number of confetti pieces, default 80)
        size_range: tuple (min, max side length, default (4, 12))
        speed: float (fall speed, default 0.8)
        opacity: float (0.0–1.0, default 0.8)
        colors: list (hex colors, default festive palette)
    """
    count = params.get("count", 80)
    size_range = params.get("size_range", (4, 12))
    speed = params.get("speed", 0.8)
    opacity_val = params.get("opacity", 0.8)
    colors = params.get("colors", [
        "#FF4444", "#44FF44", "#4444FF", "#FFFF44", "#FF44FF", "#44FFFF",
        "#FF8800", "#FF0088", "#00FF88",
    ])

    w, h = frame.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    particles = _generate_particles(count, seed=55, w=w, h=h)
    rng = np.random.RandomState(55)
    sizes = rng.uniform(size_range[0], size_range[1], count)
    phases = rng.uniform(0, 1, count)
    color_indices = rng.randint(0, len(colors), count)
    rotations = rng.uniform(0, 2 * math.pi, count)

    for i in range(count):
        bx, _ = particles[i]
        size = sizes[i]

        fall_progress = (progress * speed + phases[i]) % 1.0
        x = int((bx + math.sin(fall_progress * math.pi * 2 + rotations[i]) * 0.05) * w) % w
        y = int(fall_progress * (h + size * 4)) - int(size * 2)

        if y < -size or y > h + size:
            continue

        # Parse color
        c_hex = colors[color_indices[i]].lstrip("#")
        cr, cg, cb = int(c_hex[0:2], 16), int(c_hex[2:4], 16), int(c_hex[4:6], 16)
        alpha = int(255 * opacity_val)

        # Draw rotated rectangle (simplified as a polygon)
        angle = rotations[i] + progress * math.pi * 2
        half = size / 2
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        corners = [
            (x + cos_a * half - sin_a * half * 0.4, y + sin_a * half + cos_a * half * 0.4),
            (x - cos_a * half - sin_a * half * 0.4, y - sin_a * half + cos_a * half * 0.4),
            (x - cos_a * half + sin_a * half * 0.4, y - sin_a * half - cos_a * half * 0.4),
            (x + cos_a * half + sin_a * half * 0.4, y + sin_a * half - cos_a * half * 0.4),
        ]
        draw.polygon(corners, fill=(cr, cg, cb, alpha))

    result = frame.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")
