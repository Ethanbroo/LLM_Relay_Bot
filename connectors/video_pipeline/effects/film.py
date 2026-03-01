"""Film and cinematic effects.

Effects:
- film_grain: Photographic film grain noise
- vignette: Darkened edges (radial gradient)
- letterbox: Cinematic black bars (top and bottom)
- light_leak: Animated warm light leak overlay
"""

import numpy as np
from PIL import Image, ImageDraw
from .registry import register_effect


@register_effect("film_grain")
def film_grain(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Add photographic film grain noise.

    params:
        intensity: float (0.0–1.0, default 0.3)
        monochrome: bool (True = uniform grain, False = per-channel)
        seed: int (RNG seed for deterministic grain, default 42)
    """
    intensity = params.get("intensity", 0.3)
    monochrome = params.get("monochrome", True)
    seed = params.get("seed", 42)

    # Deterministic RNG: seed varies per frame via progress so grain
    # changes frame-to-frame but is reproducible across runs.
    rng = np.random.RandomState(seed + int(progress * 10000))

    arr = np.array(frame, dtype=np.float32)

    if monochrome:
        noise = rng.normal(0, 25 * intensity, (arr.shape[0], arr.shape[1]))
        noise = np.stack([noise] * 3, axis=-1)
    else:
        noise = rng.normal(0, 25 * intensity, arr.shape)

    result = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(result)


@register_effect("vignette")
def vignette(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Add a vignette (darkened edges).

    params:
        strength: float (0.0–1.0, how dark edges get, default 0.5)
        radius: float (0.0–1.0, where darkening starts, default 0.8)
        animate: bool (if True, vignette fades in with progress)
    """
    strength = params.get("strength", 0.5)
    radius = params.get("radius", 0.8)
    if params.get("animate", False):
        strength *= progress

    w, h = frame.size

    # Create radial gradient mask
    Y, X = np.ogrid[:h, :w]
    center_x, center_y = w / 2, h / 2

    dist = np.sqrt((X - center_x) ** 2 + (Y - center_y) ** 2)
    max_dist = np.sqrt(center_x ** 2 + center_y ** 2)
    dist = dist / max_dist

    mask = np.clip(1 - (dist - radius) / (1 - radius) * strength, 0, 1)
    mask = np.where(dist < radius, 1.0, mask)

    arr = np.array(frame, dtype=np.float32)
    for c in range(3):
        arr[:, :, c] *= mask

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


@register_effect("letterbox")
def letterbox(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Add cinematic letterbox bars.

    params:
        bar_height_percent: float (fraction of height per bar, default 0.12)
        color: str (hex color for bars, default "#000000")
        animate: bool (if True, bars slide in with progress)
    """
    bar_pct = params.get("bar_height_percent", 0.12)
    color = params.get("color", "#000000")

    if params.get("animate", False):
        bar_pct *= min(progress * 3, 1.0)  # Bars fully in by ~33% progress

    w, h = frame.size
    bar_h = int(h * bar_pct)

    if bar_h <= 0:
        return frame

    result = frame.copy()
    draw = ImageDraw.Draw(result)
    draw.rectangle([0, 0, w, bar_h], fill=color)
    draw.rectangle([0, h - bar_h, w, h], fill=color)

    return result


@register_effect("light_leak")
def light_leak(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Animated warm light leak overlay.

    Simulates light entering the camera from the side, sweeping across.

    params:
        intensity: float (0.0–1.0, default 0.3)
        color: tuple (R, G, B, default warm orange-yellow)
        direction: str ("left", "right", "top", "bottom", default "right")
    """
    intensity = params.get("intensity", 0.3)
    leak_color = params.get("color", (255, 200, 100))
    if isinstance(leak_color, (list, tuple)) and len(leak_color) >= 3:
        leak_r, leak_g, leak_b = int(leak_color[0]), int(leak_color[1]), int(leak_color[2])
    else:
        leak_r, leak_g, leak_b = 255, 200, 100
    direction = params.get("direction", "right")

    w, h = frame.size
    arr = np.array(frame, dtype=np.float32)

    # Create gradient that sweeps with progress
    if direction in ("left", "right"):
        X = np.arange(w, dtype=np.float32) / w
        if direction == "left":
            X = 1.0 - X
        # Gaussian-shaped leak centered at the progress position
        center = progress
        sigma = 0.15
        gradient = np.exp(-((X - center) ** 2) / (2 * sigma ** 2))
        gradient = gradient[np.newaxis, :]  # (1, W)
    else:
        Y = np.arange(h, dtype=np.float32) / h
        if direction == "top":
            Y = 1.0 - Y
        center = progress
        sigma = 0.15
        gradient = np.exp(-((Y - center) ** 2) / (2 * sigma ** 2))
        gradient = gradient[:, np.newaxis]  # (H, 1)

    # Apply additive light leak
    leak = np.zeros_like(arr)
    leak[:, :, 0] = leak_r * gradient * intensity
    leak[:, :, 1] = leak_g * gradient * intensity
    leak[:, :, 2] = leak_b * gradient * intensity

    result = np.clip(arr + leak, 0, 255).astype(np.uint8)
    return Image.fromarray(result)
