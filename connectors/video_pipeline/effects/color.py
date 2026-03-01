"""Color grading and adjustment effects.

Effects:
- brightness: Adjust image brightness
- contrast: Adjust image contrast
- saturation: Adjust color saturation
- warm_tone: Shift colors warm (reds/yellows up, blues down)
- cool_tone: Shift colors cool (blues up, reds down)
- sepia: Apply sepia tone
"""

import numpy as np
from PIL import Image, ImageEnhance
from .registry import register_effect


@register_effect("brightness")
def brightness(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Adjust brightness.

    params:
        factor: float (1.0 = original, >1 = brighter, <1 = darker)
        animate: bool (if True, interpolate from 1.0 to factor over progress)
    """
    factor = params.get("factor", 1.0)
    if params.get("animate", False):
        factor = 1.0 + (factor - 1.0) * progress
    return ImageEnhance.Brightness(frame).enhance(factor)


@register_effect("contrast")
def contrast(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Adjust contrast.

    params:
        factor: float (1.0 = original, >1 = more contrast)
        animate: bool
    """
    factor = params.get("factor", 1.0)
    if params.get("animate", False):
        factor = 1.0 + (factor - 1.0) * progress
    return ImageEnhance.Contrast(frame).enhance(factor)


@register_effect("saturation")
def saturation(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Adjust color saturation.

    params:
        factor: float (1.0 = original, 0.0 = grayscale, >1 = vivid)
        animate: bool
    """
    factor = params.get("factor", 1.0)
    if params.get("animate", False):
        factor = 1.0 + (factor - 1.0) * progress
    return ImageEnhance.Color(frame).enhance(factor)


@register_effect("warm_tone")
def warm_tone(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Apply warm color shift (increase reds/yellows, decrease blues).

    params:
        intensity: float (0.0–1.0, default 0.15)
        animate: bool
    """
    intensity = params.get("intensity", 0.15)
    if params.get("animate", False):
        intensity *= progress

    r, g, b = frame.split()
    r_arr = np.array(r, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    r_arr = np.clip(r_arr * (1 + intensity), 0, 255)
    b_arr = np.clip(b_arr * (1 - intensity * 0.5), 0, 255)
    return Image.merge("RGB", [
        Image.fromarray(r_arr.astype(np.uint8)),
        g,
        Image.fromarray(b_arr.astype(np.uint8)),
    ])


@register_effect("cool_tone")
def cool_tone(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Apply cool color shift (increase blues, decrease reds).

    params:
        intensity: float (0.0–1.0, default 0.15)
        animate: bool
    """
    intensity = params.get("intensity", 0.15)
    if params.get("animate", False):
        intensity *= progress

    r, g, b = frame.split()
    r_arr = np.array(r, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    r_arr = np.clip(r_arr * (1 - intensity * 0.5), 0, 255)
    b_arr = np.clip(b_arr * (1 + intensity), 0, 255)
    return Image.merge("RGB", [
        Image.fromarray(r_arr.astype(np.uint8)),
        g,
        Image.fromarray(b_arr.astype(np.uint8)),
    ])


@register_effect("sepia")
def sepia(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Apply sepia tone.

    params:
        intensity: float (0.0–1.0, default 0.8). 1.0 = full sepia, 0.0 = original
        animate: bool
    """
    intensity = params.get("intensity", 0.8)
    if params.get("animate", False):
        intensity *= progress

    arr = np.array(frame, dtype=np.float32)
    # Standard sepia matrix
    sepia_r = arr[:, :, 0] * 0.393 + arr[:, :, 1] * 0.769 + arr[:, :, 2] * 0.189
    sepia_g = arr[:, :, 0] * 0.349 + arr[:, :, 1] * 0.686 + arr[:, :, 2] * 0.168
    sepia_b = arr[:, :, 0] * 0.272 + arr[:, :, 1] * 0.534 + arr[:, :, 2] * 0.131

    result = np.stack([
        arr[:, :, 0] * (1 - intensity) + sepia_r * intensity,
        arr[:, :, 1] * (1 - intensity) + sepia_g * intensity,
        arr[:, :, 2] * (1 - intensity) + sepia_b * intensity,
    ], axis=-1)

    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))
