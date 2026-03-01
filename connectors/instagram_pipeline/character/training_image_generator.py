"""Generate reference images for LoRA training using Recraft V3 via fal.ai.

Creates a diverse set of 35 images covering all required angles,
lighting conditions, and expressions for consistent character identity.

Uses fal-ai/recraft-v3 at $0.04/image ($1.40 total for 35 images).
Recraft V3 produces genuinely photorealistic portraits — far superior
to Flux Schnell for LoRA training data.

Usage:
    python -m connectors.instagram_pipeline.character.training_image_generator solana_v1
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Image generation prompts organized by required training categories.
# Each prompt anchors the character identity description and varies
# angle, lighting, expression, and background as required by the LoRA trainer.

ANGLE_PROMPTS = {
    "front_neutral": [
        "looking directly at camera, neutral relaxed expression, straight-on angle",
        "facing camera squarely, calm composed expression, direct eye contact",
    ],
    "front_smile": [
        "looking at camera with warm natural smile, showing teeth slightly, front facing",
        "genuine smile at camera, eyes slightly crinkled, facing forward",
    ],
    "front_laugh": [
        "laughing openly at camera, mouth open in genuine laughter, front facing",
        "mid-laugh expression, joyful and candid, looking toward camera",
    ],
    "three_quarter_left": [
        "three-quarter view turned slightly left, looking past camera, thoughtful expression",
        "angled 45 degrees to the left, soft gaze, relaxed jaw",
    ],
    "three_quarter_right": [
        "three-quarter view turned slightly right, gentle smile, natural pose",
        "angled 45 degrees to the right, chin slightly lifted, confident expression",
    ],
    "profile_left": [
        "left profile view, looking into the distance, contemplative mood",
        "full left side profile, sharp jawline visible, serene expression",
    ],
    "profile_right": [
        "right profile view, wind gently moving hair, peaceful expression",
        "full right side profile, natural light catching features, calm look",
    ],
    "looking_down": [
        "head tilted slightly downward, looking at something in hands, soft expression",
        "glancing down with slight smile, eyelashes visible, modest pose",
    ],
    "looking_up": [
        "head tilted slightly upward, looking at sky, hopeful expression",
        "chin lifted, gazing upward, light falling on face from above",
    ],
}

LIGHTING_CONDITIONS = [
    "bright natural outdoor sunlight, clear day",
    "overcast soft diffused light, cloudy day outdoors",
    "indoor window light, soft directional shadows",
    "golden hour warm light, long shadows, amber tones",
    "low indoor evening light, warm ambient lamp glow",
]

BACKGROUNDS = [
    "plain neutral background",
    "outdoor park with blurred greenery",
    "indoor room with soft neutral decor",
    "urban street scene blurred behind",
    "coastal scenery softly blurred",
    "cafe interior warm tones blurred",
    "white wall with natural light",
]


def _condense_description(anchor) -> str:
    """Condense character anchor into a short description for Recraft's 1000-char limit.

    Keeps the most visually distinctive features that define identity.
    """
    # Extract just the key features from each field
    parts = []

    # Face: keep eyes, lips, jawline — skip minor details
    face = anchor.face_description
    if "eyes" in face:
        # Extract eye description
        for phrase in face.split(","):
            if "eye" in phrase.lower():
                parts.append(phrase.strip())
                break
    parts.append("full lips, high cheekbones")

    # Hair: keep color and length
    hair = anchor.hair_description
    # Take first two comma-separated phrases (color + length)
    hair_parts = [p.strip() for p in hair.split(",")][:2]
    parts.append(", ".join(hair_parts))

    # Skin: keep complexion
    skin = anchor.skin_description
    skin_parts = [p.strip() for p in skin.split(",")][:1]
    parts.append(skin_parts[0])

    # Body: keep build
    body = anchor.body_description
    body_parts = [p.strip() for p in body.split(",")][:1]
    parts.append(body_parts[0])

    desc = "a woman with " + ", ".join(parts)
    return desc


def build_training_prompts(character_description: str) -> list[dict]:
    """Build the full set of 35 training image prompts.

    Returns list of dicts with 'prompt', 'angle', 'lighting', 'index' keys.
    """
    prompts = []
    idx = 0

    # Cycle through all angle/lighting/background combinations
    bg_cycle = iter(BACKGROUNDS * 10)  # Enough to cover all prompts

    for angle_name, angle_variations in ANGLE_PROMPTS.items():
        for lighting in LIGHTING_CONDITIONS:
            # Alternate between prompt variations for variety
            angle_prompt = angle_variations[idx % len(angle_variations)]
            bg = next(bg_cycle)

            full_prompt = (
                f"professional portrait photo of {character_description}, "
                f"{angle_prompt}, {lighting}, {bg}, "
                f"sharp focus, photorealistic"
            )

            prompts.append({
                "prompt": full_prompt,
                "angle": angle_name,
                "lighting": lighting.split(",")[0],
                "index": idx,
            })
            idx += 1

            # Stop at 35 images (recommended count)
            if idx >= 35:
                return prompts

    return prompts


async def generate_training_images(
    character_id: str,
    output_dir: Optional[str] = None,
):
    """Generate training images for a character using Recraft V3 via fal.ai ($0.04/image).

    Args:
        character_id: Character ID to load from registry
        output_dir: Optional output directory override
    """
    from .registry import CharacterRegistry

    registry = CharacterRegistry()
    profile = registry.load(character_id)

    # Build condensed character description from identity anchor.
    # Recraft V3 has a 1000-char prompt limit, so we keep the core
    # identity features and drop finer details the LoRA will learn.
    anchor = profile.identity_anchor
    char_desc = _condense_description(anchor)

    prompts = build_training_prompts(char_desc)

    # Output directory
    if output_dir is None:
        out_path = Path(f"data/characters/{character_id}/training_images")
    else:
        out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Generating %d training images for %s to %s",
        len(prompts), character_id, out_path,
    )

    import fal_client
    import httpx

    async with httpx.AsyncClient() as http:
        for item in prompts:
            filename = f"{item['index']:02d}_{item['angle']}_{item['lighting'].replace(' ', '_')}.png"
            filepath = out_path / filename

            if filepath.exists():
                logger.info("Skipping existing: %s", filename)
                continue

            logger.info(
                "Generating image %d/%d: %s / %s",
                item["index"] + 1, len(prompts), item["angle"], item["lighting"],
            )

            try:
                result = fal_client.subscribe(
                    "fal-ai/recraft-v3",
                    arguments={
                        "prompt": item["prompt"],
                        "image_size": {"width": 1024, "height": 1024},
                        "style": "realistic_image",
                    },
                    with_logs=False,
                )

                # Download the image (Recraft returns webp, save as-is then convert)
                image_url = result["images"][0]["url"]
                resp = await http.get(image_url, timeout=60.0)
                resp.raise_for_status()

                # Recraft returns webp; convert to PNG for LoRA training compatibility
                content_type = resp.headers.get("content-type", "")
                if "webp" in content_type or image_url.endswith(".webp"):
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(resp.content))
                    img.save(filepath, "PNG")
                else:
                    with open(filepath, "wb") as f:
                        f.write(resp.content)

                logger.info("Saved: %s (%d bytes)", filename, filepath.stat().st_size)

            except Exception as e:
                logger.error("Failed to generate image %d: %s", item["index"], e)
                continue

    generated = list(out_path.glob("*.png"))
    logger.info(
        "Training image generation complete: %d/%d images saved to %s",
        len(generated), len(prompts), out_path,
    )
    return str(out_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    character_id = sys.argv[1] if len(sys.argv) > 1 else "solana_v1"
    asyncio.run(generate_training_images(character_id))
