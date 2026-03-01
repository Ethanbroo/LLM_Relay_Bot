"""Asset generation pipeline for Instagram content.

This module handles image and video generation using various AI providers.
Image: ComfyUI (PuLID + ControlNet) or Flux via fal.ai (fallback)
Video: Kling via fal.ai, SiliconFlow/Wan2.2
"""

from .base import GenerationRequest, GenerationResult, AbstractAssetGenerator
from .image_generator import FluxImageGenerator
from .comfyui_client import ComfyUIClient, ComfyUIError
from .comfyui_image_generator import ComfyUIImageGenerator
from .workflow_templates import WorkflowTemplateEngine, WorkflowSlots, SCENE_CONFIGS
from .provider_registry import ProviderRegistry
from .video_provider_registry import VideoProviderRegistry, VideoGenerationResult
from .video_generator import VideoGenerator, GenerationError
from .avatar_generator import AvatarGenerator
from .clip_sequencer import ClipSequencer

__all__ = [
    'GenerationRequest',
    'GenerationResult',
    'AbstractAssetGenerator',
    'FluxImageGenerator',
    'ComfyUIClient',
    'ComfyUIError',
    'ComfyUIImageGenerator',
    'WorkflowTemplateEngine',
    'WorkflowSlots',
    'SCENE_CONFIGS',
    'ProviderRegistry',
    'VideoProviderRegistry',
    'VideoGenerationResult',
    'VideoGenerator',
    'GenerationError',
    'AvatarGenerator',
    'ClipSequencer',
]
