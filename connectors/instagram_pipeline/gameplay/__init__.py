"""Gameplay clip integration for Instagram video content.

Provides clip ingestion, storage, scene detection, and AI character overlay.
"""

from .clip_registry import ClipRegistry, GameplayClip
from .overlay_compositor import OverlayCompositor

__all__ = [
    'ClipRegistry',
    'GameplayClip',
    'OverlayCompositor',
]
