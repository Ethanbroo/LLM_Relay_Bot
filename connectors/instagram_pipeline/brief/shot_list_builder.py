"""Shot list builder for NARRATIVE_REEL format.

Claude expands a content brief into a structured multi-shot list
with durations, camera movements, and transitions.
"""

import json
import logging
from typing import Optional

from .models import ShotSpec, InstagramContentBrief
from ..character.models import CharacterProfile

logger = logging.getLogger(__name__)

SHOT_LIST_SYSTEM_PROMPT = """You are a video director planning shots for a 15-30 second Instagram Reel.

Design 3-6 shots that tell a cohesive visual story. Each shot should:
- Have a specific duration (2-5 seconds)
- Use intentional camera movement and framing
- Flow naturally from the previous shot via transitions
- Stay true to the character's visual style

Output ONLY a JSON object with this structure:
{
  "shots": [
    {
      "shot_index": 0,
      "scene": "environment description",
      "action": "what the character is doing",
      "expression": "facial expression",
      "framing": "portrait_tight|portrait_medium|half_body|full_body|environmental",
      "camera_angle": "eye_level|slightly_above|slightly_below|low_angle",
      "camera_movement": "static|slow_pan_right|tracking|dolly_in|orbit",
      "shot_size": "close-up|medium|wide|extreme_wide",
      "lighting": "lighting description",
      "wardrobe": "clothing description",
      "background_detail": "background elements",
      "duration": 3,
      "transition": "cut|crossfade|whip_pan"
    }
  ],
  "total_duration": 15,
  "visual_flow_notes": "how shots connect narratively"
}"""


class ShotListBuilder:
    """Expands a content brief into a structured multi-shot list."""

    def __init__(self, character: CharacterProfile, claude_client=None):
        self.character = character
        self.claude = claude_client

    def _get_completion(self, system_prompt: str, user_message: str, temperature: float = 0.8, max_tokens: int = 2048) -> str:
        """Call Claude API for structured completion."""
        if self.claude and hasattr(self.claude, 'get_completion'):
            return self.claude.get_completion(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def build_shot_list(
        self,
        brief: InstagramContentBrief,
        target_duration: int = 15,
        max_shots: int = 6,
    ) -> list[ShotSpec]:
        """Generate a structured shot list from a content brief.

        Args:
            brief: Content brief with narrative hook
            target_duration: Target total duration in seconds
            max_shots: Maximum number of shots

        Returns:
            List of ShotSpec objects with video-specific fields populated
        """
        user_message = (
            f"Create a shot list for a {target_duration}-second Instagram Reel.\n\n"
            f"Narrative Hook: {brief.narrative_hook}\n"
            f"Tone: {brief.tone}\n"
            f"Target Emotion: {brief.target_emotion}\n"
            f"Pillar: {brief.content_pillar}\n\n"
            f"Character Style:\n"
            f"- Photography: {self.character.style_dna.photography_style}\n"
            f"- Palette: {self.character.style_dna.color_palette}\n"
            f"- Wardrobe: {self.character.style_dna.wardrobe_style}\n\n"
            f"Maximum shots: {max_shots}\n"
            f"Each shot should be 2-5 seconds."
        )

        try:
            response = self._get_completion(
                system_prompt=SHOT_LIST_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.8,
                max_tokens=2048,
            )
        except Exception as e:
            logger.error("Claude API call failed for shot list: %s", e)
            raise RuntimeError(f"Failed to generate shot list: {e}")

        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON from Claude: %s", response[:200])
            raise ValueError(f"Claude returned invalid JSON: {e}")

        shots = []
        for shot_data in data.get("shots", [])[:max_shots]:
            shot = ShotSpec(
                shot_index=shot_data.get("shot_index", len(shots)),
                character_id=brief.character_id,
                scene=shot_data.get("scene", ""),
                action=shot_data.get("action", ""),
                expression=shot_data.get("expression", ""),
                framing=shot_data.get("framing", "half_body"),
                camera_angle=shot_data.get("camera_angle", "eye_level"),
                lighting=shot_data.get("lighting", "natural"),
                wardrobe=shot_data.get("wardrobe", ""),
                background_detail=shot_data.get("background_detail", ""),
                duration=shot_data.get("duration", 3),
                camera=shot_data.get("camera_movement", "static"),
                shot_size=shot_data.get("shot_size", "medium"),
                transition_from_prev=shot_data.get("transition", "cut"),
                is_hero_shot=(shot_data.get("shot_index", len(shots)) == 0),
            )
            shots.append(shot)

        logger.info(
            "Built shot list: %d shots, %ds total",
            len(shots),
            sum(s.duration for s in shots),
        )

        return shots
