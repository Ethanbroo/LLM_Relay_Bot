"""Example effect plugin: vintage color grading.

Demonstrates how to create an effect plugin using the EffectPlugin interface.
This applies a warm vintage look — faded blacks, warm highlights, and
reduced saturation.
"""

import numpy as np
from PIL import Image, ImageEnhance

from connectors.video_pipeline.plugins.interface import EffectPlugin


class Plugin(EffectPlugin):
    """Vintage color grading effect plugin."""

    @property
    def plugin_name(self) -> str:
        return "vintage_effect"

    @property
    def plugin_version(self) -> str:
        return "1.0.0"

    def get_effects(self) -> dict:
        return {
            "vintage": vintage_grade,
            "vintage_fade": vintage_fade,
        }


def vintage_grade(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Apply vintage color grading — warm tones, desaturated, faded blacks.

    Params:
        intensity: 0.0-1.0, how strong the effect is (default: 0.7)
        warmth: 0.0-1.0, warm tone strength (default: 0.3)
    """
    intensity = params.get("intensity", 0.7)
    warmth = params.get("warmth", 0.3)

    # Desaturate slightly
    enhancer = ImageEnhance.Color(frame)
    frame = enhancer.enhance(1.0 - (0.4 * intensity))

    # Convert to numpy for color manipulation
    arr = np.array(frame, dtype=np.float32)

    # Fade the blacks (lift shadows)
    fade_amount = 30 * intensity
    arr = arr + fade_amount
    arr = np.clip(arr, 0, 255)

    # Warm tone shift (add to red, subtract from blue)
    arr[:, :, 0] = np.clip(arr[:, :, 0] + (20 * warmth), 0, 255)  # Red
    arr[:, :, 2] = np.clip(arr[:, :, 2] - (15 * warmth), 0, 255)  # Blue

    return Image.fromarray(arr.astype(np.uint8))


def vintage_fade(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Animated vintage fade — effect intensifies over the clip duration.

    Params:
        max_intensity: Peak intensity at end of clip (default: 0.8)
    """
    max_intensity = params.get("max_intensity", 0.8)
    current_intensity = progress * max_intensity

    return vintage_grade(frame, progress, {
        "intensity": current_intensity,
        "warmth": current_intensity * 0.4,
    })
