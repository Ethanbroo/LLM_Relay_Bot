"""A/B test image generation models for character consistency.

Generates 2 images per run using the selected model and prompt.
Downloads results to test_output/ for visual comparison.

Usage:
    python scripts/test_image_gen.py --model minimax --scene "sitting at a cafe"
    python scripts/test_image_gen.py --model ideogram --scene "beach at sunset"
    python scripts/test_image_gen.py --model recraft --scene "reading in a window seat"
    python scripts/test_image_gen.py --model instant --scene "walking in a garden"
    python scripts/test_image_gen.py --model flux --scene "cooking in a kitchen"
    python scripts/test_image_gen.py --model minimax --prompt "A young woman with curly hair..."
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import fal_client
import httpx
from dotenv import load_dotenv

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

CHARACTER_DIR = PROJECT_ROOT / "data" / "characters" / "solana_v1"
OUTPUT_DIR = PROJECT_ROOT / "output" / "test_image_gen"

# Model configurations
MODELS = {
    "minimax": {
        "endpoint": "fal-ai/minimax/image-01/subject-reference",
        "cost_per_image": 0.01,
        "uses_ref_image": True,
        "ref_image_param": "image_url",  # single URL string
    },
    "recraft": {
        "endpoint": "fal-ai/recraft-v3",
        "cost_per_image": 0.04,
        "uses_ref_image": False,
    },
    "flux": {
        "endpoint": "fal-ai/flux-lora",
        "cost_per_image": 0.04,
        "uses_ref_image": False,
    },
    "instant": {
        "endpoint": "fal-ai/instant-character",
        "cost_per_image": 0.04,
        "uses_ref_image": True,
        "ref_image_param": "image_url",  # single URL string
    },
    "ideogram": {
        "endpoint": "fal-ai/ideogram/character",
        "cost_per_image": 0.10,
        "uses_ref_image": True,
        "ref_image_param": "reference_image_urls",  # list of URLs
    },
    "ideogram_hq": {
        "endpoint": "fal-ai/ideogram/character",
        "cost_per_image": 0.15,
        "uses_ref_image": True,
        "ref_image_param": "reference_image_urls",
    },
}

# Cache uploaded reference image URL across runs
_ref_url_cache = {}


def load_character_profile():
    profile_path = CHARACTER_DIR / "character_profile.json"
    with open(profile_path) as f:
        return json.load(f)


def build_prompt(profile, scene):
    """Build generation prompt from character profile + scene."""
    anchor = profile["identity_anchor"]
    style = profile["style_dna"]

    parts = [
        f"Photorealistic portrait of a young woman: {anchor['face_description']}",
        anchor["hair_description"],
        anchor["skin_description"],
        scene,
        style["photography_style"],
        style["lighting_preference"],
        f"Color palette: {style['color_palette']}",
    ]
    return ". ".join(parts)


def upload_reference_image():
    """Upload hero reference image to fal.ai storage. Cached per session."""
    hero_path = str(CHARACTER_DIR / "training_images" / "00_hero_reference.png")
    if hero_path not in _ref_url_cache:
        print(f"Uploading reference image: {hero_path}")
        _ref_url_cache[hero_path] = fal_client.upload_file(hero_path)
        print(f"Uploaded: {_ref_url_cache[hero_path][:80]}...")
    return _ref_url_cache[hero_path]


def get_next_run_number():
    """Find next sequential run number from test_output/."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = [d.name for d in OUTPUT_DIR.iterdir() if d.is_dir()]
    if not existing:
        return 1
    nums = []
    for name in existing:
        try:
            nums.append(int(name.split("_")[0]))
        except (ValueError, IndexError):
            pass
    return max(nums, default=0) + 1


def build_arguments(model_name, model_config, prompt, ref_url):
    """Build fal.ai API arguments for the given model."""
    endpoint = model_config["endpoint"]

    if model_name == "minimax":
        args = {
            "prompt": prompt,
            "image_url": ref_url,
        }
    elif model_name == "recraft":
        args = {
            "prompt": prompt,
            "image_size": {"width": 1024, "height": 1024},
        }
    elif model_name == "flux":
        args = {
            "prompt": prompt,
            "image_size": {"width": 1024, "height": 1024},
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "enable_safety_checker": False,
            "output_format": "jpeg",
            "sync_mode": True,
        }
    elif model_name == "instant":
        args = {
            "prompt": prompt,
            "image_url": ref_url,
            "image_size": "square_hd",
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
        }
    elif model_name in ("ideogram", "ideogram_hq"):
        speed = "BALANCED" if model_name == "ideogram_hq" else "TURBO"
        # Use portrait for full body prompts, square for face shots
        size = "portrait_4_3" if "full body" in prompt.lower() else "square_hd"
        args = {
            "prompt": prompt,
            "reference_image_urls": [ref_url],
            "rendering_speed": speed,
            "style": "REALISTIC",
            "image_size": size,
            "expand_prompt": False,
        }
    else:
        raise ValueError(f"Unknown model: {model_name}")

    return args


def download_image(url, local_path):
    """Download image from URL to local path."""
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url) as r:
        r.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def generate_images(model_name, prompt, scene, num_images=2):
    """Generate images and save results."""
    model_config = MODELS[model_name]
    endpoint = model_config["endpoint"]

    # Upload reference image if needed
    ref_url = None
    if model_config.get("uses_ref_image"):
        ref_url = upload_reference_image()

    # Create output directory
    run_num = get_next_run_number()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / f"{run_num:03d}_{model_name}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Model: {model_name} ({endpoint})")
    print(f"Cost: ~${model_config['cost_per_image'] * num_images:.2f} for {num_images} images")
    print(f"Output: {run_dir}")
    print(f"{'='*60}")
    print(f"\nPrompt:\n{prompt[:200]}{'...' if len(prompt) > 200 else ''}\n")

    results = []
    for i in range(num_images):
        args = build_arguments(model_name, model_config, prompt, ref_url)

        print(f"Generating image {i+1}/{num_images}...")
        start = time.time()

        try:
            result = fal_client.subscribe(
                endpoint,
                arguments=args,
                with_logs=False,
            )
        except Exception as e:
            print(f"  FAILED: {e}")
            results.append({"error": str(e), "image_index": i + 1})
            continue

        gen_time = time.time() - start

        # Extract image URL
        image_url = None
        if "images" in result and result["images"]:
            image_url = result["images"][0].get("url")
        elif "image" in result:
            image_url = result["image"].get("url")

        if not image_url:
            print(f"  No image URL in response: {json.dumps(result, indent=2)[:300]}")
            results.append({"error": "No image URL in response", "image_index": i + 1})
            continue

        # Download
        ext = "png" if "png" in image_url.lower() else "jpg"
        local_path = run_dir / f"image_{i+1}.{ext}"
        download_image(image_url, str(local_path))

        seed_used = result.get("seed")
        cost = model_config["cost_per_image"]

        print(f"  Done in {gen_time:.1f}s | ${cost:.3f} | seed={seed_used} | {local_path.name}")

        results.append({
            "image_index": i + 1,
            "image_url": image_url,
            "local_path": str(local_path),
            "generation_time_s": round(gen_time, 2),
            "cost_usd": cost,
            "seed": seed_used,
        })

    # Save metadata
    metadata = {
        "run_number": run_num,
        "model": model_name,
        "endpoint": endpoint,
        "cost_per_image": model_config["cost_per_image"],
        "total_cost": model_config["cost_per_image"] * num_images,
        "prompt": prompt,
        "scene": scene,
        "reference_image_used": model_config.get("uses_ref_image", False),
        "reference_image_url": ref_url,
        "timestamp": timestamp,
        "results": results,
    }

    metadata_path = run_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nMetadata saved: {metadata_path}")
    print(f"Total estimated cost: ${metadata['total_cost']:.2f}")

    return run_dir, results


def main():
    parser = argparse.ArgumentParser(description="A/B test image generation models")
    parser.add_argument(
        "--model",
        choices=list(MODELS.keys()),
        required=True,
        help="Model to test",
    )
    parser.add_argument(
        "--scene",
        default="casual portrait, natural setting, golden hour light",
        help="Scene description to add to the prompt",
    )
    parser.add_argument(
        "--prompt",
        help="Full custom prompt (overrides auto-build from character profile)",
    )
    parser.add_argument(
        "--num",
        type=int,
        default=2,
        help="Number of images to generate (default: 2)",
    )

    args = parser.parse_args()

    # fal_client reads FAL_KEY, but .env uses FAL_API_KEY
    if not os.environ.get("FAL_KEY"):
        if os.environ.get("FAL_API_KEY"):
            os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
        else:
            print("ERROR: FAL_KEY or FAL_API_KEY not set. Check your .env file.")
            sys.exit(1)

    # Load character and build prompt
    profile = load_character_profile()

    if args.prompt:
        prompt = args.prompt
    else:
        prompt = build_prompt(profile, args.scene)

    run_dir, results = generate_images(args.model, prompt, args.scene, args.num)

    # Print summary
    successful = [r for r in results if "error" not in r]
    print(f"\n{'='*60}")
    print(f"DONE: {len(successful)}/{args.num} images generated")
    print(f"View results: open {run_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
