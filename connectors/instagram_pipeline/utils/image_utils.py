"""Image processing utilities for the Instagram pipeline.

Provides common image operations needed across multiple stages:
- Frame extraction from videos
- Image format conversion
- Resolution validation
- PIL helper functions
"""

import io
import base64
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageOps


def load_image(path: str) -> Image.Image:
    """
    Load image from file path with automatic orientation correction.

    Args:
        path: Path to image file

    Returns:
        PIL Image object with correct orientation

    Raises:
        FileNotFoundError: If image doesn't exist
        PIL.UnidentifiedImageError: If file isn't a valid image
    """
    img = Image.open(path)
    # Apply EXIF orientation if present (fixes rotated phone photos)
    img = ImageOps.exif_transpose(img)
    return img


def resize_to_square(img: Image.Image, size: int = 1024) -> Image.Image:
    """
    Resize image to square dimensions with center crop.

    Used for LoRA training images which must be exactly 1024x1024.

    Args:
        img: Input PIL Image
        size: Target size (default 1024 for Flux LoRA training)

    Returns:
        Square PIL Image of specified size
    """
    # Get current dimensions
    width, height = img.size

    # Center crop to square first
    if width > height:
        left = (width - height) / 2
        top = 0
        right = left + height
        bottom = height
    else:
        left = 0
        top = (height - width) / 2
        right = width
        bottom = top + width

    img_cropped = img.crop((left, top, right, bottom))

    # Resize to target size using high-quality Lanczos resampling
    img_resized = img_cropped.resize((size, size), Image.Resampling.LANCZOS)

    return img_resized


def validate_training_image(path: str) -> Tuple[bool, Optional[str]]:
    """
    Validate image meets requirements for LoRA training.

    Requirements:
    - Format: PNG
    - Dimensions: 1024x1024 (square)
    - Color mode: RGB (not RGBA, not grayscale)

    Args:
        path: Path to image file

    Returns:
        Tuple of (is_valid, error_message)
        error_message is None if valid
    """
    try:
        img = Image.open(path)

        # Check format
        if img.format != 'PNG':
            return False, f"Must be PNG format, got {img.format}"

        # Check dimensions
        width, height = img.size
        if width != 1024 or height != 1024:
            return False, f"Must be 1024x1024, got {width}x{height}"

        # Check color mode
        if img.mode != 'RGB':
            return False, f"Must be RGB mode, got {img.mode}"

        return True, None

    except Exception as e:
        return False, f"Failed to load image: {e}"


def image_to_base64(img: Image.Image, format: str = 'PNG') -> str:
    """
    Convert PIL Image to base64-encoded string.

    Used for API uploads and storage in JSON metadata.

    Args:
        img: PIL Image object
        format: Output format (PNG, JPEG, etc.)

    Returns:
        Base64-encoded string (without data URI prefix)
    """
    buffer = io.BytesIO()
    img.save(buffer, format=format)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('utf-8')


def base64_to_image(b64_string: str) -> Image.Image:
    """
    Convert base64-encoded string to PIL Image.

    Args:
        b64_string: Base64 string (with or without data URI prefix)

    Returns:
        PIL Image object
    """
    # Strip data URI prefix if present
    if ',' in b64_string:
        b64_string = b64_string.split(',', 1)[1]

    image_data = base64.b64decode(b64_string)
    return Image.open(io.BytesIO(image_data))


def save_image_optimized(
    img: Image.Image,
    path: str,
    format: str = 'PNG',
    quality: int = 95
) -> None:
    """
    Save image with optimization for file size while preserving quality.

    Args:
        img: PIL Image to save
        path: Output path
        format: Image format (PNG, JPEG, etc.)
        quality: Quality level for lossy formats (1-100)
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if format.upper() == 'PNG':
        # PNG is lossless but can be compressed
        img.save(path, format='PNG', optimize=True)
    elif format.upper() in ('JPEG', 'JPG'):
        # JPEG with quality setting
        if img.mode == 'RGBA':
            # Convert RGBA to RGB for JPEG
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[3])  # Use alpha channel as mask
            rgb_img.save(path, format='JPEG', quality=quality, optimize=True)
        else:
            img.save(path, format='JPEG', quality=quality, optimize=True)
    else:
        img.save(path, format=format)


def get_image_dimensions(path: str) -> Tuple[int, int]:
    """
    Get image dimensions without loading entire file into memory.

    Args:
        path: Path to image file

    Returns:
        Tuple of (width, height)
    """
    with Image.open(path) as img:
        return img.size


def convert_to_rgb(img: Image.Image) -> Image.Image:
    """
    Convert image to RGB mode (strips alpha channel, converts grayscale).

    Required for many image generation APIs that don't support RGBA.

    Args:
        img: PIL Image in any mode

    Returns:
        PIL Image in RGB mode
    """
    if img.mode == 'RGB':
        return img
    elif img.mode == 'RGBA':
        # Create white background and composite
        rgb_img = Image.new('RGB', img.size, (255, 255, 255))
        rgb_img.paste(img, mask=img.split()[3])
        return rgb_img
    else:
        # Grayscale, palette, etc.
        return img.convert('RGB')
