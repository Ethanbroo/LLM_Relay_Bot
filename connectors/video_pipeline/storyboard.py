"""Storyboard generation from content briefs via LLM.

Converts a high-level content brief (from the existing content strategy system)
into a structured Storyboard that can be compiled into a Timeline.
"""

import json
import logging
from typing import Optional

from .schemas import Storyboard

logger = logging.getLogger(__name__)

# System prompt for storyboard generation
STORYBOARD_SYSTEM_PROMPT = """You are a video content director for social media.
Given a content brief, create a detailed storyboard for a short-form video.

Output ONLY valid JSON matching this structure:
{
    "concept": "One-sentence video concept",
    "target_platform": "instagram_reel",
    "target_duration_seconds": 15,
    "scenes": [
        {
            "prompt": "Detailed image generation prompt for this scene",
            "text": "Optional text overlay for this scene",
            "text_position": "bottom_center",
            "duration_ms": 3000,
            "character_id": "aurora_v1",
            "zoom_start": 1.0,
            "zoom_end": 1.05
        }
    ],
    "music_mood": "calm ambient",
    "voiceover_text": null,
    "hashtags": ["#lifestyle", "#aesthetic"],
    "caption": "Caption text for the post"
}

Rules:
1. Each scene needs a detailed 'prompt' field for AI image generation.
2. Keep scenes between 2-6 for short-form content.
3. Each scene should be 2-5 seconds long (duration_ms: 2000-5000).
4. Text overlays should be short (1-6 words) for readability.
5. Prompts must be photorealistic, detailed, and describe a single moment.
6. If character_id is provided in the brief, include it in each scene.
7. Ensure visual flow -- scenes should feel like they belong in the same video.
"""


def generate_storyboard_prompt(brief: dict) -> str:
    """Build the user prompt for storyboard generation from a content brief.

    Args:
        brief: Content brief dict. Expected keys:
            - concept: str (video concept)
            - target_platform: str
            - target_duration_seconds: int
            - character_ids: list[str] (optional)
            - narrative_hook: str (optional)
            - tone: str (optional)
            - content_pillar: str (optional)

    Returns:
        User prompt string for the LLM
    """
    parts = [f"Create a storyboard for this video concept: {brief.get('concept', 'lifestyle content')}"]

    if brief.get("target_platform"):
        parts.append(f"Platform: {brief['target_platform']}")
    if brief.get("target_duration_seconds"):
        parts.append(f"Target duration: {brief['target_duration_seconds']} seconds")
    if brief.get("character_ids"):
        parts.append(f"Character(s): {', '.join(brief['character_ids'])}")
    if brief.get("narrative_hook"):
        parts.append(f"Narrative hook: {brief['narrative_hook']}")
    if brief.get("tone"):
        parts.append(f"Tone: {brief['tone']}")
    if brief.get("content_pillar"):
        parts.append(f"Content pillar: {brief['content_pillar']}")

    return "\n".join(parts)


def parse_storyboard_response(response_text: str) -> Storyboard:
    """Parse LLM response into a validated Storyboard.

    Args:
        response_text: Raw LLM response (expected to be JSON)

    Returns:
        Validated Storyboard instance

    Raises:
        ValueError: If response cannot be parsed or validated
    """
    # Strip markdown code fences if present
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM response is not valid JSON: {e}")

    try:
        return Storyboard(**data)
    except Exception as e:
        raise ValueError(f"LLM response does not match Storyboard schema: {e}")


def generate_storyboard(
    brief: dict,
    claude_client=None,
    log_daemon=None,
) -> Storyboard:
    """Generate a storyboard from a content brief using Claude.

    Args:
        brief: Content brief dict
        claude_client: ClaudeClient instance (from llm_integration)
        log_daemon: Optional LogDaemon for audit events

    Returns:
        Validated Storyboard

    Raises:
        ValueError: If storyboard generation or parsing fails
        RuntimeError: If Claude client is not available
    """
    if claude_client is None:
        raise RuntimeError("Claude client required for storyboard generation")

    user_prompt = generate_storyboard_prompt(brief)

    if log_daemon:
        log_daemon.ingest_event(
            event_type="VIDEO_STORYBOARD_REQUESTED",
            actor="video_pipeline.storyboard",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "concept": brief.get("concept", ""),
                "platform": brief.get("target_platform", ""),
            }
        )

    logger.info("Generating storyboard for: %s", brief.get("concept", "unknown"))

    # Call Claude to generate the storyboard
    response = claude_client.send_message(
        system_prompt=STORYBOARD_SYSTEM_PROMPT,
        user_message=user_prompt,
    )

    # Extract text content from response
    if isinstance(response, dict):
        response_text = response.get("content", response.get("text", str(response)))
    else:
        response_text = str(response)

    storyboard = parse_storyboard_response(response_text)

    if log_daemon:
        log_daemon.ingest_event(
            event_type="VIDEO_STORYBOARD_CREATED",
            actor="video_pipeline.storyboard",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload={
                "concept": storyboard.concept,
                "platform": storyboard.target_platform,
                "scene_count": len(storyboard.scenes),
                "duration_seconds": storyboard.target_duration_seconds,
            }
        )

    logger.info(
        "Storyboard created: %d scenes, %ds, platform=%s",
        len(storyboard.scenes), storyboard.target_duration_seconds,
        storyboard.target_platform
    )

    return storyboard
