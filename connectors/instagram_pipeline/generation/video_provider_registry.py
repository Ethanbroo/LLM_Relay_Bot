"""Video provider registry — routes content formats to endpoints.

Routes ContentFormat to the appropriate video generation endpoint with fallback chain.
Supports fal.ai/Kling (Standard tier), SiliconFlow/Wan2.2, and FFmpeg PiP composite.
Each content format has a primary endpoint, a fallback, and a last-resort
static image path (existing Flux pipeline).
"""

import logging
import tempfile
from typing import Optional

from ..brief.models import ContentFormat, VideoIntent
from ..config.budget import CostTracker

logger = logging.getLogger(__name__)


class VideoGenerationResult:
    """Result from a video generation attempt."""

    def __init__(
        self,
        video_url: str = "",
        video_path: str = "",
        generation_time_s: float = 0.0,
        cost_usd: float = 0.0,
        provider: str = "fal.ai",
        endpoint: str = "",
        provider_tier: str = "primary",
        duration_seconds: float = 0.0,
        has_audio: bool = False,
        cost_summary: Optional[dict] = None,
        is_static_fallback: bool = False,
    ):
        self.video_url = video_url
        self.video_path = video_path
        self.generation_time_s = generation_time_s
        self.cost_usd = cost_usd
        self.provider = provider
        self.endpoint = endpoint
        self.provider_tier = provider_tier
        self.duration_seconds = duration_seconds
        self.has_audio = has_audio
        self.cost_summary = cost_summary
        self.is_static_fallback = is_static_fallback


class VideoProviderRegistry:
    """Routes content formats to video endpoints with degradation fallback."""

    ENDPOINTS = {
        ContentFormat.NARRATIVE_REEL: {
            "primary": "fal-ai/kling-video/o3/standard/reference-to-video",
            "fallback": "siliconflow/wan2.2-i2v",
            "last_resort": "static_image",
        },
        ContentFormat.AVATAR_TALKING: {
            "primary": "fal-ai/kling-video/ai-avatar/v2/standard",
            "fallback": "siliconflow/wan2.2-i2v",
            "last_resort": "static_image",
        },
        ContentFormat.CINEMATIC_CLIP: {
            "primary": "fal-ai/kling-video/v3/standard/text-to-video",
            "fallback": "siliconflow/wan2.2-t2v",
            "last_resort": "static_image",
        },
        ContentFormat.GAMEPLAY_OVERLAY: {
            "primary": "ffmpeg_pip_composite",
            "fallback": None,
            "last_resort": "static_image",
        },
    }

    def get_endpoint(self, content_format: ContentFormat, tier: str) -> Optional[str]:
        """Get the endpoint for a given content format and tier."""
        chain = self.ENDPOINTS.get(content_format, {})
        return chain.get(tier)

    def get_endpoint_chain(self, content_format: ContentFormat) -> list[tuple[str, str]]:
        """Return ordered list of (tier_name, endpoint) for a content format."""
        chain = self.ENDPOINTS.get(content_format, {})
        result = []
        for tier in ["primary", "fallback", "last_resort"]:
            endpoint = chain.get(tier)
            if endpoint is not None:
                result.append((tier, endpoint))
        return result

    def build_request_payload(
        self, endpoint: str, intent: VideoIntent
    ) -> dict:
        """Build the request payload for a specific endpoint."""

        # SiliconFlow/Wan2.2 endpoints
        if endpoint.startswith("siliconflow/"):
            model_key = "i2v" if "i2v" in endpoint else "t2v"
            return {
                "_provider": "siliconflow",
                "model_key": model_key,
                "prompt": intent.prompt,
                "image_url": intent.character_refs[0] if intent.character_refs else None,
                "size": "720x1280" if intent.aspect_ratio == "9:16" else "1280x720",
            }

        # FFmpeg PiP composite
        if endpoint == "ffmpeg_pip_composite":
            return {
                "_provider": "ffmpeg",
                "gameplay_path": intent.source_video_url,
                "character_refs": intent.character_refs,
                "prompt": intent.prompt,
                "aspect_ratio": intent.aspect_ratio,
            }

        # fal.ai/Kling endpoints
        if endpoint == "fal-ai/kling-video/o3/standard/reference-to-video":
            return self._build_o3_reference_payload(intent)
        elif endpoint == "fal-ai/kling-video/o3/standard/text-to-video":
            return self._build_o3_t2v_payload(intent)
        elif endpoint == "fal-ai/kling-video/ai-avatar/v2/standard":
            return self._build_avatar_payload(intent)
        elif endpoint == "fal-ai/kling-video/v3/standard/text-to-video":
            return self._build_v3_t2v_payload(intent)
        elif endpoint == "fal-ai/kling-video/v2.6/standard/image-to-video":
            return self._build_v26_i2v_payload(intent)
        elif endpoint == "fal-ai/kling-video/o1/video-to-video/edit":
            return self._build_o1_edit_payload(intent)
        else:
            raise ValueError(f"Unknown endpoint: {endpoint}")

    def _build_o3_reference_payload(self, intent: VideoIntent) -> dict:
        """Build payload for Kling O3 Standard Reference-to-Video."""
        payload = {
            "prompt": intent.prompt,
            "duration": str(intent.duration),
            "aspect_ratio": intent.aspect_ratio,
        }

        if intent.character_refs:
            payload["frontal_image_url"] = intent.character_refs[0]
            if len(intent.character_refs) > 1:
                payload["image_urls"] = intent.character_refs[1:]

        if intent.native_audio:
            payload["generate_audio"] = True
        if intent.voice_ids:
            payload["voice_ids"] = intent.voice_ids

        return payload

    def _build_o3_t2v_payload(self, intent: VideoIntent) -> dict:
        """Build payload for Kling O3 Standard text-to-video."""
        payload = {
            "prompt": intent.prompt,
            "duration": str(intent.duration),
            "aspect_ratio": intent.aspect_ratio,
        }

        if intent.shot_list:
            payload["multi_prompt"] = [
                {
                    "prompt": shot.scene + ". " + shot.action,
                    "duration": str(shot.duration),
                }
                for shot in intent.shot_list
            ]

        if intent.native_audio:
            payload["generate_audio"] = True
        if intent.voice_ids:
            payload["voice_ids"] = intent.voice_ids

        return payload

    def _build_avatar_payload(self, intent: VideoIntent) -> dict:
        """Build payload for Kling Avatar v2 Standard (talking head)."""
        payload = {}

        if intent.character_refs:
            payload["image_url"] = intent.character_refs[0]

        if intent.audio_url:
            payload["audio_url"] = intent.audio_url

        if intent.prompt:
            payload["prompt"] = intent.prompt

        return payload

    def _build_v3_t2v_payload(self, intent: VideoIntent) -> dict:
        """Build payload for Kling V3 Standard text-to-video."""
        payload = {
            "prompt": intent.prompt,
            "duration": str(intent.duration),
            "aspect_ratio": intent.aspect_ratio,
        }

        if intent.native_audio:
            payload["generate_audio"] = True

        return payload

    def _build_v26_i2v_payload(self, intent: VideoIntent) -> dict:
        """Build payload for Kling V2.6 Standard image-to-video."""
        payload = {
            "prompt": intent.prompt,
            "duration": str(intent.duration),
            "aspect_ratio": intent.aspect_ratio,
        }

        if intent.character_refs:
            payload["image_url"] = intent.character_refs[0]

        return payload

    def _build_o1_edit_payload(self, intent: VideoIntent) -> dict:
        """Build payload for Kling O1 video-to-video edit."""
        if not intent.source_video_url:
            raise ValueError("source_video_url required for gameplay_overlay")

        payload = {
            "prompt": intent.prompt,
            "video_url": intent.source_video_url,
        }

        if intent.character_refs:
            payload["elements"] = [
                {"image_url": ref}
                for ref in intent.character_refs
            ]

        return payload
