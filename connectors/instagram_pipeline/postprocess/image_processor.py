"""Image post-processing for final polish.

This module handles:
- Format conversion to Instagram-optimal formats
- Resolution adjustments for Instagram specs
- Optional sharpening and detail enhancement
- Disclosure label burning (AI-generated content notice)
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Instagram format specifications
INSTAGRAM_FORMATS = {
    "feed_square": {
        "width": 1080,
        "height": 1080,
        "aspect_ratio": "1:1",
        "description": "Square feed post (classic Instagram format)"
    },
    "feed_portrait": {
        "width": 1080,
        "height": 1350,
        "aspect_ratio": "4:5",
        "description": "Portrait feed post (optimal engagement)"
    },
    "feed_landscape": {
        "width": 1080,
        "height": 566,
        "aspect_ratio": "1.91:1",
        "description": "Landscape feed post"
    },
    "reels": {
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "description": "Reels/Stories vertical format"
    },
}


class ImageProcessor:
    """
    Post-processes generated images for Instagram publishing.

    Handles format conversion, resolution adjustment, and optional
    enhancement/disclosure labeling.
    """

    def __init__(
        self,
        enable_sharpening: bool = False,
        sharpen_amount: float = 1.5,
        jpeg_quality: int = 95,
    ):
        """
        Initialize image processor.

        Args:
            enable_sharpening: Apply subtle sharpening for clarity
            sharpen_amount: Sharpening strength (1.0-3.0, default 1.5)
            jpeg_quality: JPEG quality for output (1-100, default 95)
        """
        self.enable_sharpening = enable_sharpening
        self.sharpen_amount = sharpen_amount
        self.jpeg_quality = jpeg_quality

    def process(
        self,
        input_path: str,
        output_path: str,
        target_format: str = "feed_portrait",
        add_disclosure_label: bool = True,
    ) -> str:
        """
        Process image for Instagram.

        Args:
            input_path: Path to generated image
            output_path: Where to save processed image
            target_format: Instagram format (see INSTAGRAM_FORMATS)
            add_disclosure_label: Add AI disclosure label per platform policy

        Returns:
            Path to processed image
        """
        from PIL import Image, ImageFilter, ImageDraw, ImageFont

        logger.info(
            "Processing image for Instagram: %s -> %s (format: %s)",
            Path(input_path).name,
            Path(output_path).name,
            target_format
        )

        # Validate format
        if target_format not in INSTAGRAM_FORMATS:
            raise ValueError(
                f"Unknown format '{target_format}'. "
                f"Valid formats: {', '.join(INSTAGRAM_FORMATS.keys())}"
            )

        format_spec = INSTAGRAM_FORMATS[target_format]
        target_width = format_spec["width"]
        target_height = format_spec["height"]

        # Load image
        img = Image.open(input_path)

        # Convert to RGB if necessary
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Resize to target resolution
        img = self._resize_to_target(img, target_width, target_height)

        # Optional sharpening
        if self.enable_sharpening:
            img = self._apply_sharpening(img)

        # Add disclosure label if required
        if add_disclosure_label:
            img = self._add_disclosure_label(img)

        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Save with optimal settings
        img.save(
            output_path,
            "JPEG",
            quality=self.jpeg_quality,
            optimize=True,
            progressive=True,  # Progressive JPEGs load faster on mobile
        )

        logger.info(
            "Image processed successfully: %dx%d JPEG at quality %d",
            img.width,
            img.height,
            self.jpeg_quality
        )

        return output_path

    def _resize_to_target(
        self,
        img: "Image.Image",
        target_width: int,
        target_height: int
    ) -> "Image.Image":
        """
        Resize image to target dimensions.

        Uses smart cropping to maintain the most important part of the image
        (center-weighted) when aspect ratios don't match.
        """
        from PIL import Image

        current_width, current_height = img.size
        target_ratio = target_width / target_height
        current_ratio = current_width / current_height

        if abs(current_ratio - target_ratio) < 0.01:
            # Aspect ratios match, simple resize
            return img.resize((target_width, target_height), Image.Resampling.LANCZOS)

        # Aspect ratios don't match - need to crop
        if current_ratio > target_ratio:
            # Image is wider than target - crop width
            new_width = int(current_height * target_ratio)
            left = (current_width - new_width) // 2
            img = img.crop((left, 0, left + new_width, current_height))
        else:
            # Image is taller than target - crop height
            new_height = int(current_width / target_ratio)
            top = (current_height - new_height) // 2
            img = img.crop((0, top, current_width, top + new_height))

        # Resize to exact target
        return img.resize((target_width, target_height), Image.Resampling.LANCZOS)

    def _apply_sharpening(self, img: "Image.Image") -> "Image.Image":
        """Apply subtle sharpening for clarity."""
        from PIL import ImageFilter

        # UnsharpMask is better than simple SHARPEN filter
        return img.filter(
            ImageFilter.UnsharpMask(
                radius=2,
                percent=int(self.sharpen_amount * 100),
                threshold=3
            )
        )

    def _add_disclosure_label(self, img: "Image.Image") -> "Image.Image":
        """
        Add AI disclosure label per Instagram policy.

        Adds small text in bottom-right corner: "AI-generated"
        Semi-transparent, professional appearance.
        """
        from PIL import ImageDraw, ImageFont
        import copy

        # Create a copy to avoid modifying original
        img = img.copy()
        draw = ImageDraw.Draw(img, "RGBA")

        # Label text
        label_text = "AI-generated"

        # Try to use a nice font, fall back to default
        try:
            # Use a system font if available
            font_size = max(12, int(img.height * 0.015))  # Scale with image size
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            except:
                font = ImageFont.load_default()

        # Get text bounding box
        bbox = draw.textbbox((0, 0), label_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Position in bottom-right corner with padding
        padding = 15
        x = img.width - text_width - padding
        y = img.height - text_height - padding - 5

        # Draw semi-transparent background
        bg_padding = 5
        background_box = [
            x - bg_padding,
            y - bg_padding,
            x + text_width + bg_padding,
            y + text_height + bg_padding
        ]
        draw.rectangle(background_box, fill=(0, 0, 0, 128))  # Semi-transparent black

        # Draw text
        draw.text((x, y), label_text, fill=(255, 255, 255, 230), font=font)

        return img

    def process_batch(
        self,
        input_paths: list[str],
        output_dir: str,
        target_format: str = "feed_portrait",
        add_disclosure_label: bool = True,
    ) -> list[str]:
        """
        Process multiple images.

        Args:
            input_paths: List of input image paths
            output_dir: Directory to save processed images
            target_format: Instagram format for all images
            add_disclosure_label: Add disclosure label to all images

        Returns:
            List of output paths (same order as input)
        """
        output_paths = []

        for i, input_path in enumerate(input_paths):
            # Generate output filename
            input_name = Path(input_path).stem
            output_path = str(Path(output_dir) / f"{input_name}_processed.jpg")

            processed_path = self.process(
                input_path=input_path,
                output_path=output_path,
                target_format=target_format,
                add_disclosure_label=add_disclosure_label
            )

            output_paths.append(processed_path)

        return output_paths

    def get_format_info(self, format_name: str) -> dict:
        """Get specifications for an Instagram format."""
        if format_name not in INSTAGRAM_FORMATS:
            raise ValueError(f"Unknown format: {format_name}")
        return INSTAGRAM_FORMATS[format_name]

    def list_formats(self) -> dict:
        """List all available Instagram formats."""
        return INSTAGRAM_FORMATS
