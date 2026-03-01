"""Template Ecosystem for the video pipeline.

Pre-built video templates for common social media content types.
A template defines the visual structure, timing, effect chain, and text
placement — the user only provides content (images, text, character IDs).

Built-in templates:
- instagram_reel_slideshow: 3-7 images with transitions + music
- instagram_reel_quote: Background image + animated quote text
- tiktok_before_after: Split screen, before/after reveal
- product_showcase: Rotating product shots with specs overlay
- day_in_life: Time-stamped scenes, casual vibe
- tutorial_steps: Numbered steps with demonstrations
- text_story: Animated text on colored backgrounds
- photo_montage: Photo grid to fullscreen transitions
- countdown: "Top N" style countdown
- audiogram: Audio waveform visualization

Custom templates can be added to connectors/video_pipeline/templates/custom/
and registered via the template registry.
"""

__version__ = "0.1.0"

from .base import BaseTemplate, TemplateInput  # noqa: F401
from .registry import (  # noqa: F401
    get_template,
    list_templates,
    register_template,
    TemplateRegistry,
)
