"""Blur and focus effects.

Effects:
- gaussian_blur: Standard gaussian blur
- motion_blur: Directional motion blur (simulates camera movement)
- depth_of_field: Radial blur with sharp center (simulates shallow DOF)
"""

import numpy as np
from PIL import Image, ImageFilter
from .registry import register_effect


@register_effect("gaussian_blur")
def gaussian_blur(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Apply gaussian blur.

    params:
        radius: float (blur radius in pixels, default 3.0)
        animate: bool (if True, blur increases with progress)
    """
    radius = params.get("radius", 3.0)
    if params.get("animate", False):
        radius *= progress
    if radius <= 0:
        return frame
    return frame.filter(ImageFilter.GaussianBlur(radius=radius))


@register_effect("motion_blur")
def motion_blur(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Apply directional motion blur.

    Simulates camera movement in a given direction.

    params:
        strength: int (kernel size, default 15. Must be odd.)
        angle: float (direction in degrees, 0 = horizontal right, 90 = vertical down)
        animate: bool
    """
    strength = params.get("strength", 15)
    angle = params.get("angle", 0.0)
    if params.get("animate", False):
        strength = max(1, int(strength * progress))

    # Ensure odd kernel size
    if strength % 2 == 0:
        strength += 1
    if strength < 3:
        return frame

    # Create motion blur kernel
    kernel_size = strength
    kernel = np.zeros((kernel_size, kernel_size), dtype=np.float32)
    center = kernel_size // 2

    # Draw a line through the kernel at the given angle
    angle_rad = np.radians(angle)
    cos_a = np.cos(angle_rad)
    sin_a = np.sin(angle_rad)

    for i in range(kernel_size):
        offset = i - center
        x = int(center + offset * cos_a + 0.5)
        y = int(center + offset * sin_a + 0.5)
        if 0 <= x < kernel_size and 0 <= y < kernel_size:
            kernel[y, x] = 1.0

    # Normalize
    total = kernel.sum()
    if total > 0:
        kernel /= total

    # Apply via convolution
    arr = np.array(frame, dtype=np.float32)
    from scipy.ndimage import convolve
    result = np.stack([
        convolve(arr[:, :, c], kernel, mode="reflect")
        for c in range(3)
    ], axis=-1)

    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))


@register_effect("depth_of_field")
def depth_of_field(frame: Image.Image, progress: float, params: dict) -> Image.Image:
    """Apply radial depth-of-field blur (sharp center, blurred edges).

    params:
        blur_radius: float (max blur at edges, default 8.0)
        focus_radius: float (0.0–1.0, fraction of image that stays sharp, default 0.3)
        center_x: float (0.0–1.0, horizontal focus center, default 0.5)
        center_y: float (0.0–1.0, vertical focus center, default 0.5)
        animate: bool
    """
    blur_radius = params.get("blur_radius", 8.0)
    focus_radius = params.get("focus_radius", 0.3)
    center_x = params.get("center_x", 0.5)
    center_y = params.get("center_y", 0.5)

    if params.get("animate", False):
        blur_radius *= progress

    if blur_radius <= 0:
        return frame

    w, h = frame.size

    # Create blurred version
    blurred = frame.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # Create gradient mask (white = sharp original, black = blurred)
    Y, X = np.ogrid[:h, :w]
    cx = center_x * w
    cy = center_y * h
    max_dist = np.sqrt((w / 2) ** 2 + (h / 2) ** 2)
    dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) / max_dist

    # Smooth transition from sharp to blurred
    mask = np.clip(1.0 - (dist - focus_radius) / (1.0 - focus_radius), 0, 1)
    mask = np.where(dist < focus_radius, 1.0, mask)

    # Blend original and blurred using mask
    orig_arr = np.array(frame, dtype=np.float32)
    blur_arr = np.array(blurred, dtype=np.float32)
    mask_3d = np.stack([mask] * 3, axis=-1)
    result = orig_arr * mask_3d + blur_arr * (1 - mask_3d)

    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))
