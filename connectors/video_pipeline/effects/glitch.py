"""Glitch and digital distortion effects.

Effects:
- chromatic_aberration: RGB channel offset (color fringing)
- scanlines: CRT-style horizontal scanlines
- rgb_shift: Animated RGB channel displacement
- pixel_sort: Sort pixels by brightness in rows (glitch art)
"""

import numpy as np
from PIL import Image, ImageDraw
from .registry import register_effect


@register_effect("chromatic_aberration")
def chromatic_aberration(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """RGB channel offset (color fringing at edges).

    params:
        offset: int (max pixel offset per channel, default 5)
        animate: bool (if True, offset increases with progress)
    """
    offset = params.get("offset", 5)
    if params.get("animate", False):
        offset = max(1, int(offset * progress))

    r, g, b = frame.split()
    w, h = frame.size

    # Shift R channel left, B channel right, G stays centered
    r_arr = np.array(r)
    b_arr = np.array(b)

    r_shifted = np.zeros_like(r_arr)
    b_shifted = np.zeros_like(b_arr)

    # Shift R left
    if offset < w:
        r_shifted[:, :w - offset] = r_arr[:, offset:]
        r_shifted[:, w - offset:] = r_arr[:, -1:]
    # Shift B right
    if offset < w:
        b_shifted[:, offset:] = b_arr[:, :w - offset]
        b_shifted[:, :offset] = b_arr[:, :1]

    return Image.merge("RGB", [
        Image.fromarray(r_shifted),
        g,
        Image.fromarray(b_shifted),
    ])


@register_effect("scanlines")
def scanlines(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """CRT-style horizontal scanlines.

    params:
        line_width: int (pixels between lines, default 2)
        opacity: float (0.0–1.0, darkness of lines, default 0.3)
        animate: bool (if True, lines scroll downward with progress)
    """
    line_width = params.get("line_width", 2)
    opacity = params.get("opacity", 0.3)

    w, h = frame.size
    arr = np.array(frame, dtype=np.float32)

    # Create scanline pattern
    scroll_offset = 0
    if params.get("animate", False):
        scroll_offset = int(progress * line_width * 2)

    for y in range(h):
        if ((y + scroll_offset) // line_width) % 2 == 0:
            arr[y, :, :] *= (1 - opacity)

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


@register_effect("rgb_shift")
def rgb_shift(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Animated RGB channel displacement.

    Each channel is shifted in a different direction that changes with progress.

    params:
        max_offset: int (maximum pixel displacement, default 8)
        speed: float (how fast the shift cycles, default 1.0)
    """
    max_offset = params.get("max_offset", 8)
    speed = params.get("speed", 1.0)

    # Oscillate offsets with progress
    phase = progress * speed * 2 * np.pi
    r_dx = int(max_offset * np.sin(phase))
    r_dy = int(max_offset * np.cos(phase))
    b_dx = int(max_offset * np.sin(phase + 2.094))  # +120 degrees
    b_dy = int(max_offset * np.cos(phase + 2.094))

    r, g, b = frame.split()
    w, h = frame.size

    def shift_channel(ch_arr, dx, dy):
        result = np.zeros_like(ch_arr)
        src_x_start = max(0, -dx)
        src_x_end = min(w, w - dx)
        src_y_start = max(0, -dy)
        src_y_end = min(h, h - dy)
        dst_x_start = max(0, dx)
        dst_x_end = min(w, w + dx)
        dst_y_start = max(0, dy)
        dst_y_end = min(h, h + dy)
        # Ensure matching dimensions
        copy_w = min(src_x_end - src_x_start, dst_x_end - dst_x_start)
        copy_h = min(src_y_end - src_y_start, dst_y_end - dst_y_start)
        if copy_w > 0 and copy_h > 0:
            result[dst_y_start:dst_y_start + copy_h, dst_x_start:dst_x_start + copy_w] = \
                ch_arr[src_y_start:src_y_start + copy_h, src_x_start:src_x_start + copy_w]
        return result

    r_shifted = shift_channel(np.array(r), r_dx, r_dy)
    b_shifted = shift_channel(np.array(b), b_dx, b_dy)

    return Image.merge("RGB", [
        Image.fromarray(r_shifted),
        g,
        Image.fromarray(b_shifted),
    ])


@register_effect("pixel_sort")
def pixel_sort(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Sort pixels by brightness in rows (glitch art aesthetic).

    params:
        threshold: float (0.0–1.0, brightness threshold for sorting, default 0.3)
        direction: str ("horizontal" or "vertical", default "horizontal")
        coverage: float (0.0–1.0, fraction of rows/cols affected, default 0.5)
    """
    threshold = params.get("threshold", 0.3)
    direction = params.get("direction", "horizontal")
    coverage = params.get("coverage", 0.5)

    arr = np.array(frame, dtype=np.uint8)
    h, w, _ = arr.shape

    # Brightness per pixel
    gray = np.mean(arr, axis=2) / 255.0

    if direction == "horizontal":
        num_affected = int(h * coverage)
        # Deterministically select rows based on progress
        rng = np.random.RandomState(int(progress * 10000) % 2**31)
        rows = rng.choice(h, size=min(num_affected, h), replace=False)

        for row in rows:
            mask = gray[row, :] > threshold
            indices = np.where(mask)[0]
            if len(indices) < 2:
                continue
            start, end = indices[0], indices[-1] + 1
            brightness = np.mean(arr[row, start:end, :], axis=1)
            order = np.argsort(brightness)
            arr[row, start:end, :] = arr[row, start:end, :][order]
    else:
        num_affected = int(w * coverage)
        rng = np.random.RandomState(int(progress * 10000) % 2**31)
        cols = rng.choice(w, size=min(num_affected, w), replace=False)

        for col in cols:
            mask = gray[:, col] > threshold
            indices = np.where(mask)[0]
            if len(indices) < 2:
                continue
            start, end = indices[0], indices[-1] + 1
            brightness = np.mean(arr[start:end, col, :], axis=1)
            order = np.argsort(brightness)
            arr[start:end, col, :] = arr[start:end, col, :][order]

    return Image.fromarray(arr)
