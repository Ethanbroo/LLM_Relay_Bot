"""AI image generation for blog cover images.

Generates cover images using fal.ai Flux, with preference-weighted candidate
scoring that learns from user feedback over time.

Flow:
  1. LLM generates image prompt + assigns visual attributes from blog content
  2. 4 candidate attribute combos are created (varying 2 of 4 attributes)
  3. All 4 are generated in parallel via fal.ai Flux
  4. Candidates are scored using acceptance rates from image_preferences.json
  5. Best candidate is returned for WordPress upload
"""

import base64
import json
import logging
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic
import httpx

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
PREFERENCES_PATH = CONFIG_DIR / "image_preferences.json"

# fal.ai model and settings
FAL_MODEL = "fal-ai/flux/dev"
IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 800
FAL_COST_PER_IMAGE = 0.040  # $0.040 per image without LoRA

# Attribute options
STYLE_OPTIONS = [
    "illustration", "photo_realistic", "minimal_line_art",
    "editorial_photography", "watercolor", "flat_graphic",
]
SUBJECT_OPTIONS = [
    "people_interacting", "solo_person", "urban_scene",
    "nature_scene", "abstract_conceptual", "objects_symbolic",
]
COLOR_TONE_OPTIONS = [
    "warm", "cool", "muted", "vibrant", "monochrome", "brand_colors",
]
COMPOSITION_OPTIONS = [
    "close_up", "wide_shot", "centered_subject",
    "environmental", "overhead", "asymmetric",
]

# Scoring weights
WEIGHT_STYLE = 0.35
WEIGHT_SUBJECT = 0.25
WEIGHT_COLOR_TONE = 0.25
WEIGHT_COMPOSITION = 0.15

# Cold start: random variance added to scores for exploration
COLD_START_VARIANCE = 0.1

# Attribute → Flux prompt modifiers
STYLE_PROMPTS = {
    "illustration": "digital illustration style, clean lines, stylized",
    "photo_realistic": "photorealistic, high detail, natural lighting, DSLR quality",
    "minimal_line_art": "minimal line art, simple strokes, white background, clean",
    "editorial_photography": "editorial photography style, magazine quality, dramatic lighting",
    "watercolor": "watercolor painting style, soft edges, flowing colors, artistic",
    "flat_graphic": "flat graphic design, bold shapes, minimal detail, modern",
}
SUBJECT_PROMPTS = {
    "people_interacting": "two or three people interacting naturally",
    "solo_person": "single person, contemplative, natural pose",
    "urban_scene": "urban cityscape, streets, buildings, city life",
    "nature_scene": "natural outdoor setting, trees, greenery, open space",
    "abstract_conceptual": "abstract conceptual imagery, symbolic, metaphorical",
    "objects_symbolic": "meaningful objects, symbolic arrangement, still life",
}
COLOR_TONE_PROMPTS = {
    "warm": "warm color palette, golden tones, amber, soft orange",
    "cool": "cool color palette, blue tones, teal, calm atmosphere",
    "muted": "muted desaturated colors, soft tones, understated palette",
    "vibrant": "vibrant saturated colors, bold palette, eye-catching",
    "monochrome": "monochromatic color scheme, single hue variations",
    "brand_colors": "earthy warm tones with teal accents, friendly and inviting",
}
COMPOSITION_PROMPTS = {
    "close_up": "close-up framing, intimate perspective, detail focus",
    "wide_shot": "wide shot, full scene visible, environmental context",
    "centered_subject": "centered composition, subject in middle, symmetrical",
    "environmental": "environmental portrait, subject in context, scene-setting",
    "overhead": "overhead perspective, bird's eye view, looking down",
    "asymmetric": "asymmetric composition, rule of thirds, off-center subject",
}


@dataclass
class ImageCandidate:
    """A single generated image candidate with its attributes."""
    prompt: str
    attributes: dict  # {style, subject, color_tone, composition}
    image_bytes: Optional[bytes] = None
    score: float = 0.0
    error: Optional[str] = None


@dataclass
class ImageResult:
    """Result of image generation."""
    success: bool
    image_bytes_b64: Optional[str] = None  # base64-encoded image
    mime_type: str = "image/jpeg"
    prompt: str = ""
    attributes: dict = field(default_factory=dict)
    candidate_scores: list = field(default_factory=list)
    selected_index: int = 0
    error: Optional[str] = None


def _load_preferences() -> dict:
    """Load image preferences from config."""
    if PREFERENCES_PATH.exists():
        return json.loads(PREFERENCES_PATH.read_text())
    return {"cold_start_active": True, "total_blogs_with_feedback": 0, "attributes": {}}


def _get_acceptance_rate(prefs: dict, category: str, value: str) -> float:
    """Get acceptance rate for an attribute, defaulting to 0.5."""
    return prefs.get("attributes", {}).get(category, {}).get(value, {}).get("rate", 0.5)


def _generate_prompt_and_attributes(
    title: str, excerpt: str, opening: str
) -> tuple[str, dict]:
    """Use Sonnet to generate an image prompt and assign visual attributes.

    Returns (prompt_string, attributes_dict).
    """
    client = anthropic.Anthropic()

    system = (
        "You generate image descriptions for blog cover images. "
        "The blog is about adult friendship, loneliness, and social connection in Ontario, Canada. "
        "Output JSON only, no other text."
    )

    user_msg = f"""Blog title: {title}
Excerpt: {excerpt}
Opening: {opening[:500]}

Generate a cover image description and assign visual attributes.

Return JSON:
{{
  "prompt": "A detailed image description (2-3 sentences, max 100 words). Describe a scene that captures the emotional essence of this blog post. Be specific about setting, lighting, and mood. Do NOT include any text or words in the image.",
  "style": one of {json.dumps(STYLE_OPTIONS)},
  "subject": one of {json.dumps(SUBJECT_OPTIONS)},
  "color_tone": one of {json.dumps(COLOR_TONE_OPTIONS)},
  "composition": one of {json.dumps(COMPOSITION_OPTIONS)}
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    data = json.loads(raw)
    prompt = data["prompt"]
    attributes = {
        "style": data.get("style", "photo_realistic"),
        "subject": data.get("subject", "people_interacting"),
        "color_tone": data.get("color_tone", "warm"),
        "composition": data.get("composition", "wide_shot"),
    }

    # Validate attribute values
    for key, valid_options in [
        ("style", STYLE_OPTIONS),
        ("subject", SUBJECT_OPTIONS),
        ("color_tone", COLOR_TONE_OPTIONS),
        ("composition", COMPOSITION_OPTIONS),
    ]:
        if attributes[key] not in valid_options:
            attributes[key] = valid_options[0]

    return prompt, attributes


def _build_flux_prompt(base_prompt: str, attributes: dict) -> str:
    """Build a complete Flux prompt from base description + attribute modifiers."""
    parts = [base_prompt]
    parts.append(STYLE_PROMPTS.get(attributes["style"], ""))
    parts.append(SUBJECT_PROMPTS.get(attributes["subject"], ""))
    parts.append(COLOR_TONE_PROMPTS.get(attributes["color_tone"], ""))
    parts.append(COMPOSITION_PROMPTS.get(attributes["composition"], ""))
    parts.append("no text, no words, no letters, no watermarks")
    return ", ".join(p for p in parts if p)


def _create_candidate_variants(base_prompt: str, base_attributes: dict) -> list[ImageCandidate]:
    """Create 4 candidate variants by varying 2 of 4 attributes.

    Keeps 2 attributes constant (the ones LLM chose) and varies the other 2
    to create meaningful visual diversity without being completely random.
    """
    candidates = []

    # Candidate 1: base attributes (LLM's original choice)
    candidates.append(ImageCandidate(
        prompt=_build_flux_prompt(base_prompt, base_attributes),
        attributes=dict(base_attributes),
    ))

    # Pick 2 attributes to vary (randomly each run for exploration)
    attr_keys = list(base_attributes.keys())
    vary_keys = random.sample(attr_keys, 2)
    fixed_keys = [k for k in attr_keys if k not in vary_keys]

    option_pools = {
        "style": STYLE_OPTIONS,
        "subject": SUBJECT_OPTIONS,
        "color_tone": COLOR_TONE_OPTIONS,
        "composition": COMPOSITION_OPTIONS,
    }

    # Candidates 2-4: vary the selected attributes
    for i in range(3):
        variant_attrs = dict(base_attributes)
        for vk in vary_keys:
            pool = [o for o in option_pools[vk] if o != base_attributes[vk]]
            if pool:
                variant_attrs[vk] = pool[i % len(pool)]

        candidates.append(ImageCandidate(
            prompt=_build_flux_prompt(base_prompt, variant_attrs),
            attributes=dict(variant_attrs),
        ))

    return candidates


def _call_fal(prompt: str) -> Optional[bytes]:
    """Call fal.ai Flux to generate a single image. Returns image bytes or None."""
    import fal_client

    os.environ.setdefault("FAL_KEY", os.getenv("FAL_API_KEY", ""))

    arguments = {
        "prompt": prompt,
        "image_size": {"width": IMAGE_WIDTH, "height": IMAGE_HEIGHT},
        "num_inference_steps": 28,
        "guidance_scale": 3.5,
        "num_images": 1,
        "enable_safety_checker": False,
        "output_format": "jpeg",
        "sync_mode": True,
    }

    result = fal_client.subscribe(FAL_MODEL, arguments=arguments, with_logs=False)

    if "images" not in result or len(result["images"]) == 0:
        return None

    image_url = result["images"][0]["url"]

    # Handle data URLs (base64-encoded inline) vs regular HTTP URLs
    if image_url.startswith("data:"):
        # data:image/jpeg;base64,/9j/4AAQ...
        header, encoded = image_url.split(",", 1)
        return base64.b64decode(encoded)

    # Download the generated image from HTTP URL
    resp = httpx.get(image_url, timeout=30)
    resp.raise_for_status()
    return resp.content


def _generate_candidates_parallel(candidates: list[ImageCandidate]) -> list[ImageCandidate]:
    """Generate all candidates in parallel using ThreadPoolExecutor."""

    def _gen_one(candidate: ImageCandidate) -> ImageCandidate:
        try:
            image_bytes = _call_fal(candidate.prompt)
            if image_bytes:
                candidate.image_bytes = image_bytes
            else:
                candidate.error = "No image returned from fal.ai"
        except Exception as e:
            candidate.error = str(e)
            logger.warning("Candidate generation failed: %s", e)
        return candidate

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_gen_one, c): i for i, c in enumerate(candidates)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                candidates[idx] = future.result()
            except Exception as e:
                candidates[idx].error = str(e)

    return candidates


def _score_candidates(candidates: list[ImageCandidate]) -> list[float]:
    """Score candidates using image_preferences.json acceptance rates."""
    prefs = _load_preferences()
    cold_start = prefs.get("cold_start_active", True)
    scores = []

    for c in candidates:
        if c.image_bytes is None:
            scores.append(-1.0)
            continue

        attrs = c.attributes
        score = (
            _get_acceptance_rate(prefs, "style", attrs["style"]) * WEIGHT_STYLE
            + _get_acceptance_rate(prefs, "subject", attrs["subject"]) * WEIGHT_SUBJECT
            + _get_acceptance_rate(prefs, "color_tone", attrs["color_tone"]) * WEIGHT_COLOR_TONE
            + _get_acceptance_rate(prefs, "composition", attrs["composition"]) * WEIGHT_COMPOSITION
        )

        if cold_start:
            score += random.uniform(-COLD_START_VARIANCE, COLD_START_VARIANCE)

        scores.append(round(score, 4))

    return scores


def generate_blog_image(
    title: str, excerpt: str, opening_paragraphs: str
) -> ImageResult:
    """Generate an AI cover image for a blog post.

    Args:
        title: Blog post title
        excerpt: Short excerpt/description
        opening_paragraphs: First ~500 chars of blog content

    Returns:
        ImageResult with the best candidate's image data and metadata
    """
    logger.info("Generating blog cover image for: %s", title[:60])

    # Step 1-2: Generate prompt and attributes via LLM
    try:
        base_prompt, base_attributes = _generate_prompt_and_attributes(
            title, excerpt, opening_paragraphs
        )
        logger.info(
            "Image prompt generated: style=%s subject=%s color=%s composition=%s",
            base_attributes["style"],
            base_attributes["subject"],
            base_attributes["color_tone"],
            base_attributes["composition"],
        )
    except Exception as e:
        logger.error("Failed to generate image prompt: %s", e)
        return ImageResult(success=False, error=f"Prompt generation failed: {e}")

    # Step 3: Create 4 candidate variants
    candidates = _create_candidate_variants(base_prompt, base_attributes)
    logger.info("Created %d image candidates", len(candidates))

    # Step 4: Generate all candidates in parallel via fal.ai
    try:
        candidates = _generate_candidates_parallel(candidates)
        success_count = sum(1 for c in candidates if c.image_bytes is not None)
        logger.info("Generated %d/%d images successfully", success_count, len(candidates))
    except Exception as e:
        logger.error("Parallel image generation failed: %s", e)
        return ImageResult(success=False, error=f"Image generation failed: {e}")

    if not any(c.image_bytes for c in candidates):
        return ImageResult(success=False, error="All 4 candidates failed to generate")

    # Step 5: Score candidates
    scores = _score_candidates(candidates)
    logger.info("Candidate scores: %s", scores)

    # Step 6: Select best
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    best = candidates[best_idx]

    image_b64 = base64.b64encode(best.image_bytes).decode("ascii")

    logger.info(
        "Selected candidate %d (score: %.4f): style=%s subject=%s color=%s composition=%s",
        best_idx,
        scores[best_idx],
        best.attributes["style"],
        best.attributes["subject"],
        best.attributes["color_tone"],
        best.attributes["composition"],
    )

    return ImageResult(
        success=True,
        image_bytes_b64=image_b64,
        mime_type="image/jpeg",
        prompt=base_prompt,
        attributes=best.attributes,
        candidate_scores=[s for s in scores],
        selected_index=best_idx,
    )
