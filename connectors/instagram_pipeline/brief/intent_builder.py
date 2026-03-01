"""Post Intent Construction - LLM-based expansion of content briefs.

This module (Stage 2) takes a bare-bones InstagramContentBrief from the calendar
and expands it into a fully specified PostIntent using Claude.

The LLM's job is creative elaboration:
- Turn narrative hook into detailed scene description
- Decide time of day, weather, mood
- Generate 1-4 ShotSpecs for the post (carousel = multiple shots)
- Write caption draft matching the caption_style
- Select appropriate hashtags

Design principle: The LLM elaborates, but does NOT generate prompts.
ShotSpec.build_generation_prompt() assembles prompts from structured fields.
"""

import logging
from typing import Optional

from .models import InstagramContentBrief, PostIntent, ShotSpec, ContentFormat, VideoIntent
from ..character.models import CharacterProfile
from ..character.registry import CharacterRegistry
from ..utils.hashing import canonical_hash

logger = logging.getLogger(__name__)


# System prompt for Claude - defines the creative expansion task
INTENT_BUILDER_SYSTEM_PROMPT = """You are a creative director for an AI Instagram model's content pipeline.

Your job is to take a content brief (a narrative hook, pillar, format, and tone) and expand it into a detailed, structured post specification.

Key requirements:
1. Scene Description: Elaborate the narrative hook into a vivid, specific scene. Include time of day, weather, setting details, and mood.
2. Shot Specifications: Design 1-4 shots (depending on format):
   - single_image: 1 shot (the hero shot)
   - carousel: 3-4 shots (mix of angles/framings for visual variety)
   - reel: Not your concern (handled by video pipeline)
3. Caption: Write a caption matching the specified caption_style:
   - short_punchy: 1-2 sentences, impactful
   - storytelling: 3-5 sentences, narrative arc
   - question: Ends with an engaging question
   - minimal: 5-10 words maximum
   - relatable_commentary: Casual observation, slightly self-aware
4. Hashtags: Select 8-12 relevant hashtags. Mix of:
   - Broad reach (#ootd, #lifestyle)
   - Niche community (#slowliving, #minimalstyle)
   - No banned/spammy tags

Guidelines:
- Stay true to the character's style_dna (aesthetic consistency)
- Use the narrative hook as inspiration, but don't repeat it verbatim
- Shots should feel natural and spontaneous, not posed/staged
- Avoid generic influencer clichés
- The character is an AI model, but content should feel authentic to a real person

Output format: JSON object matching PostIntent schema (provided in user message)."""


# User message template for intent expansion
INTENT_EXPANSION_PROMPT_TEMPLATE = """Expand the following Instagram content brief into a detailed post specification.

Content Brief:
- Pillar: {content_pillar}
- Format: {post_format}
- Narrative Hook: {narrative_hook}
- Tone: {tone}
- Target Emotion: {target_emotion}
- Caption Style: {caption_style}
- Scheduled Time: {scheduled_post_time}

Character Style DNA:
- Photography Style: {photography_style}
- Color Palette: {color_palette}
- Composition Tendency: {composition_tendency}
- Lighting Preference: {lighting_preference}
- Wardrobe Style: {wardrobe_style}
- Environment Range: {environment_range}

Character Identity Anchor (for reference, NOT for prompt generation):
- Face: {face_description}
- Hair: {hair_description}
- Skin: {skin_description}
- Distinctive Marks: {distinctive_marks}

Output a JSON object with this exact structure:
{{
  "elaborated_scene": "detailed scene description with time of day, weather, mood, setting",
  "time_of_day": "morning|afternoon|evening|night|golden_hour",
  "weather_mood": "bright_sunny|overcast|rainy|dramatic_clouds|clear_night",
  "shot_count": 1-4 (1 for single_image, 3-4 for carousel),
  "shots": [
    {{
      "shot_index": 0,
      "scene": "specific scene detail for this shot",
      "action": "what the character is doing",
      "expression": "facial expression and emotional state",
      "framing": "portrait_tight|portrait_medium|half_body|full_body|environmental",
      "camera_angle": "eye_level|slightly_above|slightly_below|low_angle|high_angle",
      "lighting": "soft_natural|hard_directional|backlit|window_light|low_key",
      "wardrobe": "clothing description matching style_dna",
      "background_detail": "background elements and setting",
      "is_hero_shot": true (first shot only)
    }}
  ],
  "caption_draft": "caption text matching the specified caption_style",
  "hashtag_set": ["hashtag1", "hashtag2", ...],
  "visual_cohesion_notes": "how this post fits into feed aesthetic"
}}

Respond with ONLY the JSON object, no additional text."""


class IntentBuilder:
    """
    Expands InstagramContentBrief into PostIntent using Claude.

    This is the creative elaboration layer — the LLM decides scene details,
    shot compositions, caption, and hashtags. But it outputs structured data,
    not prose or prompts.
    """

    def __init__(
        self,
        character_id: str,
        registry: Optional[CharacterRegistry] = None,
        claude_client=None
    ):
        """
        Initialize intent builder.

        Args:
            character_id: Character to build intents for
            registry: Optional CharacterRegistry (creates new if not provided)
            claude_client: Optional Claude client (imports from llm_integration if not provided)
        """
        self.character_id = character_id
        self.registry = registry or CharacterRegistry()
        self.character = self.registry.load(character_id)
        self.claude = claude_client

    def _get_completion(self, system_prompt: str, user_message: str, temperature: float = 0.8, max_tokens: int = 2048) -> str:
        """Call Claude API for structured completion."""
        if self.claude and hasattr(self.claude, 'get_completion'):
            return self._get_completion(
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

    def build_intent(self, brief: InstagramContentBrief) -> PostIntent:
        """
        Expand content brief into full PostIntent.

        Calls Claude with structured prompt, parses JSON response,
        validates shot specs, and returns PostIntent with computed hash.

        Args:
            brief: Content brief from calendar

        Returns:
            PostIntent with elaborated scene, shots, caption, hashtags

        Raises:
            ValueError: If LLM returns invalid JSON or missing required fields
            RuntimeError: If Claude API call fails
        """
        logger.info(
            "Building post intent for brief: pillar=%s, format=%s, hook='%s'",
            brief.content_pillar,
            brief.post_format,
            brief.narrative_hook[:50]
        )

        # Build user message from template
        user_message = INTENT_EXPANSION_PROMPT_TEMPLATE.format(
            content_pillar=brief.content_pillar,
            post_format=brief.post_format,
            narrative_hook=brief.narrative_hook,
            tone=brief.tone,
            target_emotion=brief.target_emotion,
            caption_style=brief.caption_style,
            scheduled_post_time=brief.scheduled_post_time or "not scheduled",
            photography_style=self.character.style_dna.photography_style,
            color_palette=self.character.style_dna.color_palette,
            composition_tendency=self.character.style_dna.composition_tendency,
            lighting_preference=self.character.style_dna.lighting_preference,
            wardrobe_style=self.character.style_dna.wardrobe_style,
            environment_range=", ".join(self.character.style_dna.environment_range),
            face_description=self.character.identity_anchor.face_description,
            hair_description=self.character.identity_anchor.hair_description,
            skin_description=self.character.identity_anchor.skin_description,
            distinctive_marks=", ".join(self.character.identity_anchor.distinctive_marks),
        )

        # Call Claude with JSON mode
        try:
            response = self._get_completion(
                system_prompt=INTENT_BUILDER_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.8,  # Higher creativity for content generation
                max_tokens=2048,
            )
        except Exception as e:
            logger.error("Claude API call failed: %s", e)
            raise RuntimeError(f"Failed to generate post intent: {e}")

        # Parse JSON response (strip markdown code fences if present)
        import json
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        try:
            intent_data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON from Claude: %s", response[:200])
            raise ValueError(f"Claude returned invalid JSON: {e}")

        # Validate required fields
        required_fields = [
            "elaborated_scene", "time_of_day", "weather_mood",
            "shot_count", "shots", "caption_draft", "hashtag_set"
        ]
        missing = [f for f in required_fields if f not in intent_data]
        if missing:
            raise ValueError(f"Claude response missing required fields: {missing}")

        # Build ShotSpec objects from JSON
        shot_specs = []
        for shot_data in intent_data["shots"]:
            shot_spec = ShotSpec(
                shot_index=shot_data["shot_index"],
                character_id=self.character_id,
                scene=shot_data["scene"],
                action=shot_data["action"],
                expression=shot_data["expression"],
                framing=shot_data["framing"],
                camera_angle=shot_data["camera_angle"],
                lighting=shot_data["lighting"],
                wardrobe=shot_data["wardrobe"],
                background_detail=shot_data["background_detail"],
                is_hero_shot=shot_data.get("is_hero_shot", False),
            )
            shot_specs.append(shot_spec)

        # Validate shot count matches format
        expected_shot_count = self._get_expected_shot_count(brief.post_format)
        if len(shot_specs) != expected_shot_count:
            logger.warning(
                "Shot count mismatch: expected %d for format '%s', got %d. Adjusting.",
                expected_shot_count,
                brief.post_format,
                len(shot_specs)
            )
            # Take first N or pad with duplicates
            if len(shot_specs) > expected_shot_count:
                shot_specs = shot_specs[:expected_shot_count]
            elif len(shot_specs) < expected_shot_count:
                # Duplicate last shot to fill (rare edge case)
                while len(shot_specs) < expected_shot_count:
                    shot_specs.append(shot_specs[-1])

        # Build PostIntent
        intent = PostIntent(
            brief=brief,
            elaborated_scene=intent_data["elaborated_scene"],
            time_of_day=intent_data["time_of_day"],
            weather_mood=intent_data["weather_mood"],
            character_pose=intent_data.get("character_pose", ""),
            character_expression=intent_data.get("character_expression", ""),
            wardrobe_detail=intent_data.get("wardrobe_detail", ""),
            shot_specs=shot_specs,
            caption_draft=intent_data["caption_draft"],
            hashtag_set=intent_data["hashtag_set"],
        )

        # Compute intent hash for reproducibility
        intent_dict = {
            "brief_hash": brief.brief_hash,
            "elaborated_scene": intent.elaborated_scene,
            "shots": [
                {
                    "scene": s.scene,
                    "action": s.action,
                    "expression": s.expression,
                    "framing": s.framing,
                    "camera_angle": s.camera_angle,
                    "lighting": s.lighting,
                    "wardrobe": s.wardrobe,
                    "background_detail": s.background_detail,
                }
                for s in intent.shot_specs
            ],
        }
        intent.intent_hash = canonical_hash(intent_dict)

        logger.info(
            "Post intent built: %d shots, hash=%s, caption='%s'",
            len(shot_specs),
            intent.intent_hash[:12],
            intent.caption_draft[:50]
        )

        return intent

    def _get_expected_shot_count(self, post_format: str) -> int:
        """Get expected number of shots for a given post format."""
        if post_format == "single_image":
            return 1
        elif post_format == "carousel":
            return 3  # Default to 3 for carousels
        elif post_format == "reel":
            return 1  # Reels use different pipeline, but expect 1 thumbnail
        else:
            logger.warning("Unknown post format '%s', defaulting to 1 shot", post_format)
            return 1

    def build_batch(
        self,
        briefs: list[InstagramContentBrief],
        fail_fast: bool = False
    ) -> list[PostIntent]:
        """
        Build intents for multiple briefs.

        Useful for batch processing a week or month of content.

        Args:
            briefs: List of content briefs
            fail_fast: If True, raise on first error. If False, skip failed briefs and continue.

        Returns:
            List of PostIntents (may be shorter than input if fail_fast=False)
        """
        intents = []
        for i, brief in enumerate(briefs):
            try:
                intent = self.build_intent(brief)
                intents.append(intent)
            except Exception as e:
                logger.error(
                    "Failed to build intent for brief %d/%d (pillar=%s): %s",
                    i + 1,
                    len(briefs),
                    brief.content_pillar,
                    e
                )
                if fail_fast:
                    raise

        logger.info("Built %d/%d intents successfully", len(intents), len(briefs))
        return intents

    def build_video_intent(self, brief: InstagramContentBrief) -> VideoIntent:
        """Build a VideoIntent for video content formats.

        Calls Claude to expand the brief into video-specific specifications
        (shot list, dialogue, scene descriptions) and returns a VideoIntent
        ready for the VideoGenerator.

        Args:
            brief: Content brief with a video ContentFormat

        Returns:
            VideoIntent ready for video generation
        """
        if brief.content_format == ContentFormat.STATIC_IMAGE:
            raise ValueError(
                "build_video_intent called with STATIC_IMAGE format. "
                "Use build_intent() instead."
            )

        logger.info(
            "Building video intent: format=%s, pillar=%s, hook='%s'",
            brief.content_format.value,
            brief.content_pillar,
            brief.narrative_hook[:50],
        )

        # Build format-specific prompt for Claude
        video_prompt = self._build_video_expansion_prompt(brief)

        try:
            response = self._get_completion(
                system_prompt=self._get_video_system_prompt(brief.content_format),
                user_message=video_prompt,
                temperature=0.8,
                max_tokens=2048,
            )
        except Exception as e:
            logger.error("Claude API call failed for video intent: %s", e)
            raise RuntimeError(f"Failed to generate video intent: {e}")

        import json
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

        # Helper to parse duration values from LLM (handles "8s", "10", 5, etc.)
        def _parse_duration(val, default):
            if isinstance(val, (int, float)):
                return int(val)
            if isinstance(val, str):
                return int(''.join(c for c in val if c.isdigit()) or default)
            return default

        # Build ShotSpec list for narrative reels
        shot_list = []
        if brief.content_format == ContentFormat.NARRATIVE_REEL:
            for shot_data in data.get("shots", []):
                shot_list.append(ShotSpec(
                    shot_index=shot_data.get("shot_index", 0),
                    character_id=self.character_id,
                    scene=shot_data.get("scene", ""),
                    action=shot_data.get("action", ""),
                    expression=shot_data.get("expression", ""),
                    framing=shot_data.get("framing", "half_body"),
                    camera_angle=shot_data.get("camera_angle", "eye_level"),
                    lighting=shot_data.get("lighting", "natural"),
                    wardrobe=shot_data.get("wardrobe", ""),
                    background_detail=shot_data.get("background_detail", ""),
                    duration=_parse_duration(shot_data.get("duration"), 5),
                    camera=shot_data.get("camera_movement", ""),
                    shot_size=shot_data.get("shot_size", "medium"),
                    transition_from_prev=shot_data.get("transition", "cut"),
                ))

        # Determine duration
        if brief.content_format == ContentFormat.AVATAR_TALKING:
            duration = _parse_duration(data.get("duration"), 15)
        elif brief.content_format == ContentFormat.NARRATIVE_REEL:
            duration = sum(s.duration for s in shot_list) if shot_list else 15
        elif brief.content_format == ContentFormat.CINEMATIC_CLIP:
            duration = _parse_duration(data.get("duration"), 10)
        else:
            duration = _parse_duration(data.get("duration"), 15)

        # Get character reference images
        character_refs = []
        if self.character.lora_model_path:
            # The video generator will handle uploading refs
            character_refs = data.get("character_refs", [])

        intent = VideoIntent(
            content_format=brief.content_format,
            prompt=data.get("prompt", data.get("scene_description", brief.narrative_hook)),
            shot_list=shot_list,
            character_refs=character_refs,
            audio_url=data.get("audio_url"),
            source_video_url=data.get("source_video_url"),
            duration=duration,
            aspect_ratio=data.get("aspect_ratio", "9:16"),
            native_audio=data.get("native_audio", True),
            cfg_scale=data.get("cfg_scale", 0.5),
            caption_draft=data.get("caption_draft", ""),
            hashtag_set=data.get("hashtag_set", []),
            character_id=self.character_id,
            brief_hash=brief.brief_hash,
        )

        intent.with_hash()

        logger.info(
            "Video intent built: format=%s, duration=%ds, shots=%d, hash=%s",
            brief.content_format.value,
            duration,
            len(shot_list),
            (intent.intent_hash or "")[:12],
        )

        return intent

    def _get_video_system_prompt(self, content_format: ContentFormat) -> str:
        """Get format-specific system prompt for video intent expansion."""
        base = (
            "You are a creative director for an AI Instagram model's video content pipeline. "
            "Output ONLY valid JSON."
        )

        format_instructions = {
            ContentFormat.AVATAR_TALKING: (
                "Generate a talking-head video spec. The character speaks directly to camera. "
                "Include: prompt (scene description), dialogue_text (what they say), "
                "duration (seconds), caption_draft, hashtag_set. "
                "Dialogue should be casual, authentic, 2-4 sentences."
            ),
            ContentFormat.NARRATIVE_REEL: (
                "Generate a multi-shot narrative reel spec with 3-6 shots. "
                "Include: prompt (overall scene), shots (array with scene, action, expression, "
                "framing, camera_angle, lighting, wardrobe, background_detail, duration, "
                "camera_movement, shot_size, transition), caption_draft, hashtag_set."
            ),
            ContentFormat.CINEMATIC_CLIP: (
                "Generate a single-shot cinematic clip spec. Atmospheric, aesthetic. "
                "Include: prompt (vivid cinematic scene description), duration (5-10s), "
                "caption_draft, hashtag_set. Focus on mood and visual storytelling."
            ),
            ContentFormat.GAMEPLAY_OVERLAY: (
                "Generate a gameplay overlay spec. AI character reacts to gameplay. "
                "Include: prompt (transformation description), reaction_text (character's "
                "commentary), duration, caption_draft, hashtag_set."
            ),
        }

        return base + "\n\n" + format_instructions.get(content_format, "")

    def _build_video_expansion_prompt(self, brief: InstagramContentBrief) -> str:
        """Build the user message for video intent expansion."""
        return (
            f"Content Brief:\n"
            f"- Format: {brief.content_format.value}\n"
            f"- Pillar: {brief.content_pillar}\n"
            f"- Narrative Hook: {brief.narrative_hook}\n"
            f"- Tone: {brief.tone}\n"
            f"- Target Emotion: {brief.target_emotion}\n"
            f"- Caption Style: {brief.caption_style}\n\n"
            f"Character Style DNA:\n"
            f"- Photography Style: {self.character.style_dna.photography_style}\n"
            f"- Color Palette: {self.character.style_dna.color_palette}\n"
            f"- Wardrobe Style: {self.character.style_dna.wardrobe_style}\n\n"
            f"Character Identity:\n"
            f"- Face: {self.character.identity_anchor.face_description}\n"
            f"- Hair: {self.character.identity_anchor.hair_description}\n\n"
            f"Output a JSON object with the required fields for {brief.content_format.value} format."
        )
