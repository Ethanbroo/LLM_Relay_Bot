# Instagram AI Model Content Generation Pipeline

## Overview

This pipeline generates Instagram content featuring a consistent AI-generated character. Built entirely within the LLM Relay Bot's connector architecture, it follows the same patterns for approval gates, audit logging, and policy enforcement.

## Key Features

- **Consistent Character Identity**: LoRA training + face embedding verification ensures the same face every time
- **Hash-Anchored Reproducibility**: Every stage output tied to canonical hash for audit traceability
- **Multi-Tier Quality Gates**: 4-tier system ensures quality and identity consistency
- **Provider Abstraction**: Swap AI providers without touching pipeline code
- **Shared UC2 Infrastructure**: Designed for reuse in gaming ads use case

## Architecture Stages

### Stage 0: Character Foundation (One-Time Setup)
Create and train character identity:
1. Define `CharacterProfile` with `IdentityAnchor` and `StyleDNA`
2. Train Flux LoRA on 25-40 reference images (30-90 min, $3-5)
3. Extract face embeddings for identity consistency gating

**Files**: [character/](character/)

### Stage 1: Content Brief & Calendar System
Generate content planning briefs:
- Weighted content pillar selection (personal_moment 30%, lifestyle 25%, fashion 20%)
- Day-of-week format rotation (Mon/Wed/Fri feed, Tue/Thu reels, Weekend carousels)
- Vice-style narrative seeds ("morning light hitting the apartment just right")

**Files**: [brief/models.py](brief/models.py), [brief/calendar.py](brief/calendar.py)

### Stage 2: Post Intent Construction
LLM-based expansion of briefs:
- Claude expands narrative hook into detailed scene description
- Generates 1-4 `ShotSpec` objects with structured fields
- Assembles Flux prompts from structured data (no creative writing in assembly)

**Files**: [brief/intent_builder.py](brief/intent_builder.py), [brief/shot_spec_builder.py](brief/shot_spec_builder.py)

### Stage 3: Asset Generation Pipeline
Image generation with provider abstraction:
- Flux.1-dev + LoRA via fal.ai ($0.055 per image)
- Provider registry with automatic fallback
- Batch generation support

**Files**: [generation/](generation/)

### Stage 4: Multi-Tier Quality Gate System
**CRITICAL for identity consistency:**
- **Tier 1** (Heuristic): File format, resolution, basic sanity checks
- **Tier 2** (CLIP): Semantic alignment with prompt
- **Tier 3** (Identity): Face embedding similarity (cosine >= 0.65 = pass)
- **Tier 4** (LLM Vision): GPT-4o-mini final review

**Files**: [quality/](quality/)

### Stage 5: Post-Processing & Assembly
*(To be implemented)*
- Image upscaling and final polish
- Instagram format assembly (1:1, 4:5, 9:16)
- Caption and hashtag formatting

### Stage 6: Staging, Approval & Instagram Publishing
*(To be implemented)*
- Staged output directory with review status
- Human review workflow integration
- Meta Graph API posting

## Quick Start

### 1. Environment Setup

Add to `.env`:
```bash
# fal.ai for LoRA training and inference
FAL_API_KEY=your_key_here

# OpenAI for Tier 4 quality gate
OPENAI_API_KEY=your_key_here

# Instagram posting (Stage 6)
INSTAGRAM_ACCESS_TOKEN=your_token_here
INSTAGRAM_BUSINESS_ACCOUNT_ID=your_id_here

# Character config
DEFAULT_CHARACTER_ID=aurora_v1
```

### 2. Install Dependencies

```bash
pip install fal-client insightface onnxruntime opencv-python httpx
```

### 3. Create a Character (Stage 0)

```python
from connectors.instagram_pipeline.character.models import (
    CharacterProfile, IdentityAnchor, StyleDNA
)
from connectors.instagram_pipeline.character.registry import CharacterRegistry

# Define identity
anchor = IdentityAnchor(
    face_description="oval face, wide-set hazel eyes, slight upturned nose, small gap between front teeth",
    body_description="5'7 slim athletic build, slight hip asymmetry",
    hair_description="dark auburn wavy hair, natural frizz at roots, mid-length",
    skin_description="warm olive undertone, faint acne scarring on chin, natural pores visible",
    distinctive_marks=("small birthmark inner left wrist",)
)

# Define style
style_dna = StyleDNA(
    photography_style="film photography, natural light, slight grain, muted warm tones",
    color_palette="earthy tones, terracotta, cream, sage green",
    composition_tendency="slightly off-center subjects, environmental context visible",
    lighting_preference="golden hour, overcast natural light, indoor window light",
    wardrobe_style="casual elevated - linen, neutral tones, minimal jewelry",
    environment_range=("coffee shop", "outdoor park", "home interior", "street/urban")
)

# Create character
registry = CharacterRegistry()
character = registry.create_new_character(
    character_id="aurora_v1",
    display_name="Aurora",
    identity_anchor=anchor,
    style_dna=style_dna,
    lora_trigger_word="AURORA_V1",
    version=1
)
```

### 4. Train LoRA (Stage 0)

Prepare 25-40 reference images (1024x1024 PNG) with variety in:
- **Angles**: front neutral, front smile, 3/4 left/right, profile left/right, looking down/up
- **Lighting**: bright natural, overcast, indoor window, golden hour, low light
- **Expressions**: neutral, smile, laugh, thoughtful, slight smirk
- **Backgrounds**: At least 5 distinct backgrounds

```python
from connectors.instagram_pipeline.character.lora_trainer import LoRATrainer
from pathlib import Path

trainer = LoRATrainer(character, {"FAL_API_KEY": "your_key"})

# Validate images first
image_dir = Path("data/characters/aurora_v1/reference_images")
is_valid, issues = trainer.validate_training_images(image_dir)
if not is_valid:
    print("Validation issues:", issues)
else:
    # Submit training (30-90 minutes, $3-5)
    weights_path, weights_hash = trainer.train(image_dir)

    # Update character registry
    registry.update_lora("aurora_v1", weights_path, weights_hash, "fal.ai-v1")
```

### 5. Extract Face Embeddings (Stage 0)

```python
from connectors.instagram_pipeline.character.face_embedder import FaceEmbedder

embedder = FaceEmbedder()
embedding_path = embedder.build_reference_embeddings(
    character=character,
    reference_image_dir="data/characters/aurora_v1/reference_images"
)

# Update character registry
registry.update_face_embeddings("aurora_v1", embedding_path)
```

### 6. Generate Content (Stages 1-3)

```python
from connectors.instagram_pipeline.brief.calendar import ContentCalendar
from connectors.instagram_pipeline.brief.intent_builder import IntentBuilder
from connectors.instagram_pipeline.generation.image_generator import FluxImageGenerator
from connectors.instagram_pipeline.generation.base import GenerationRequest
from datetime import datetime
from llm_integration.claude_client import ClaudeClient

# Stage 1: Generate brief
calendar = ContentCalendar(character_id="aurora_v1")
brief = calendar.generate_brief(post_date=datetime.now())

# Stage 2: Expand into PostIntent
intent_builder = IntentBuilder(claude_client=ClaudeClient())
character = registry.load("aurora_v1")
intent = intent_builder.build_intent(brief, character)
shot_specs = intent_builder.build_shot_specs(intent, character)

# Stage 3: Generate images
generator = FluxImageGenerator(api_key="your_fal_key")
for shot_spec in shot_specs:
    prompt_dict = shot_spec.build_generation_prompt(character)

    request = GenerationRequest(
        prompt=prompt_dict["prompt"],
        negative_prompt=prompt_dict["negative_prompt"],
        lora_path=character.lora_model_path,
        lora_scale=prompt_dict["lora_scale"],
    )

    result = generator.generate(request)
    print(f"Generated: {result.image_url}, Cost: ${result.cost_usd}")
```

### 7. Run Quality Gates (Stage 4)

```python
from connectors.instagram_pipeline.quality.identity_gate import IdentityConsistencyGate

# Download generated image first
local_path = generator.download_asset(result.image_url, "output/test_image.jpg")

# Run identity gate (Tier 3 - most critical)
identity_gate = IdentityConsistencyGate(character=character)
gate_result = identity_gate.evaluate(local_path)

if gate_result.decision == GateDecision.PASS:
    print(f"Identity check PASSED (similarity: {gate_result.score:.3f})")
elif gate_result.decision == GateDecision.FAIL:
    print(f"Identity check FAILED: {gate_result.reason}")
    # Regenerate with different seed
else:
    print(f"Identity check MARGINAL: {gate_result.reason}")
    # Flag for human review
```

## Data Directory Structure

```
data/characters/{character_id}/
├── character_profile.json           # CharacterProfile JSON
├── lora_weights.safetensors         # Trained LoRA weights
├── lora_weights_hash.txt            # SHA-256 for drift detection
├── reference_images/                # 25-40 training images
│   ├── ref_001_front_neutral.png
│   ├── ref_002_front_smile.png
│   └── ...
└── face_embeddings/
    └── reference_embeddings.npy     # InsightFace vectors (N, 512)
```

## Cost Model (per post)

| Component | Cost | Notes |
|-----------|------|-------|
| LoRA Training (one-time) | $3-5 | 30-90 minutes via fal.ai |
| Single Image Generation | $0.055 | Flux.1-dev + LoRA |
| Carousel (3 images) | $0.165 | 3x single image |
| Quality Gates Tier 1-3 | Free | Local compute |
| Quality Gate Tier 4 | $0.002 | GPT-4o-mini vision |
| **Total per post** | **$0.06-$0.17** | Depends on format |

Budget 2-3 regeneration attempts per post for quality (identity consistency or other issues).

## Identity Consistency Thresholds

Face embedding cosine similarity scores:
- **>= 0.65**: Strong match (PASS) - definitely the same person
- **0.50 - 0.64**: Marginal match (MARGINAL) - flag for human review
- **< 0.50**: Identity failure (FAIL) - regenerate required

These thresholds are derived from InsightFace ArcFace literature and empirical testing.

## Architecture Principles

1. **Hash-Anchored Reproducibility**: Every stage output has a canonical hash. If you regenerate the same brief with the same character, audit logs show exact comparison.

2. **Character as First-Class Object**: Character is a structured `CharacterProfile` dataclass, not a prompt string. Every downstream stage queries it.

3. **UC2 Reusability**: Every module (character, audio, quality gates, assembly) built with clean interfaces for gaming ads use case.

## Current Implementation Status

✅ **Completed**:
- Stage 0: Character Foundation (models, registry, LoRA training, face embeddings)
- Stage 1: Content Brief & Calendar System
- Stage 2: Post Intent Construction
- Stage 3: Asset Generation Pipeline
- Stage 4: Identity Consistency Gate (Tier 3)

🚧 **In Progress**:
- Stage 4: Remaining quality gates (Tiers 1, 2, 4)
- Stage 5: Post-Processing & Assembly
- Stage 6: Staging, Approval & Publishing

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for detailed progress tracking.

## Known Limitations

1. **Distinctive marks**: ~30-50% fidelity (documented in feasibility analysis). Design character identity around structural face features (gap teeth, wide-set eyes) rather than specific marks.

2. **Hand artifacts**: Common in Flux. Mitigated by negative prompts and quality gates.

3. **Background consistency**: Varies by scene complexity. More complex backgrounds = more variation.

## Next Steps

1. Complete remaining quality gates (Tiers 1, 2, 4)
2. Implement post-processing and assembly
3. Create staging workflow with human review
4. Integrate Meta Graph API for posting
5. Add to scheduler for automatic daily execution

## Related Documentation

- Architecture Plan: See full plan provided in initial requirements
- LLM Relay Bot: Parent project architecture
- Use Case 2 (Gaming Ads): Planned reuse of this infrastructure
