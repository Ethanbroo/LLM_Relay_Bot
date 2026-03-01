# Instagram Pipeline Implementation Status

## Completed Components

### Stage 0: Character Foundation ✓
- [character/models.py](character/models.py) - `CharacterProfile`, `IdentityAnchor`, `StyleDNA`
- [character/registry.py](character/registry.py) - Character persistence and loading
- [character/lora_trainer.py](character/lora_trainer.py) - LoRA training via fal.ai
- [character/face_embedder.py](character/face_embedder.py) - InsightFace embedding extraction

### Stage 1: Content Brief & Calendar System ✓
- [brief/models.py](brief/models.py) - `InstagramContentBrief`, `ShotSpec`, `PostIntent`
- [brief/calendar.py](brief/calendar.py) - Weekly content planning with pillar weights

### Stage 2: Post Intent Construction ✓
- [brief/intent_builder.py](brief/intent_builder.py) - LLM-based brief expansion via Claude
- [brief/shot_spec_builder.py](brief/shot_spec_builder.py) - Prompt assembly from structured fields

### Stage 3: Asset Generation Pipeline ✓
- [generation/base.py](generation/base.py) - Provider abstraction interface
- [generation/image_generator.py](generation/image_generator.py) - Flux + LoRA via fal.ai
- [generation/provider_registry.py](generation/provider_registry.py) - Multi-provider fallback

### Stage 4: Multi-Tier Quality Gate System ✓
- [quality/models.py](quality/models.py) - `QualityGateResult`, `AggregatedGateResult`, `GateDecision`, `GateTier`
- [quality/heuristic_gate.py](quality/heuristic_gate.py) - Tier 1: File format, resolution, corruption checks
- [quality/clip_alignment_gate.py](quality/clip_alignment_gate.py) - Tier 2: CLIP semantic alignment
- [quality/identity_gate.py](quality/identity_gate.py) - Tier 3: Face embedding identity consistency (CRITICAL)
- [quality/llm_vision_gate.py](quality/llm_vision_gate.py) - Tier 4: GPT-4o-mini vision review
- [quality/gate_orchestrator.py](quality/gate_orchestrator.py) - Runs all tiers with early rejection

## Remaining Components to Implement

### Stage 5: Post-Processing & Assembly
Files needed:
- `postprocess/image_processor.py` - Upscaling, final polish
- `assembly/post_assembler.py` - Single image post assembly
- `assembly/carousel_assembler.py` - Multi-image carousel
- Assembly templates in `assembly/templates/`

### Stage 6: Staging, Approval & Instagram Publishing
Files needed:
- `staging/models.py` - `StagedPost`, `ReviewStatus`
- `staging/stager.py` - Writes output directory + audit entry
- `staging/instagram_poster.py` - Meta Graph API integration
- `staging/review_watcher.py` - File watcher for auto-post on approval

### Workflow Integration
Files needed:
- `workflows/instagram_workflow.py` - Top-level workflow coordinator
- `scheduler/instagram_scheduler.py` - Daily content generation schedule
- `pipeline_entry.py` - Main orchestration entry point

## Architecture Compliance

✓ Hash-anchored reproducibility (all models use `canonical_hash()`)
✓ Character as first-class object (structured `CharacterProfile` dataclass)
✓ Shared infrastructure for UC2 (provider abstraction, character system)
✓ Uses existing Claude client from `llm_integration/claude_client.py`
✓ Follows bot's connector architecture pattern

## Next Steps

1. **Complete Quality Gates** (highest priority)
   - Implement identity_gate.py using face_embedder.py
   - This is critical for preventing identity drift

2. **Implement Post-Processing**
   - Basic image post-processor for final polish
   - Assembly templates for Instagram formats

3. **Implement Staging & Publishing**
   - Staged output directory structure
   - Meta Graph API integration for posting
   - Human review workflow integration

4. **Create Workflow Orchestrator**
   - Tie all stages together
   - Integrate with existing multi_agent_v2 supervisor
   - Add to scheduler for automatic execution

## Cost Estimates (per post)

- LoRA Training (one-time): $3-5
- Image Generation (Flux + LoRA): $0.055 per image
- Quality Gates:
  - Tiers 1-3: Free (local compute)
  - Tier 4 (GPT-4o-mini): ~$0.002 per review
- Total per single-image post: ~$0.06
- Total per carousel (3 images): ~$0.18

## Key Design Decisions

1. **Why InsightFace for identity consistency?**
   - ArcFace embeddings are the industry standard for face recognition
   - 512-dim vectors provide precise identity measurement
   - Cosine similarity gives objective pass/fail scores

2. **Why 4-tier quality gates?**
   - Tier 1 catches obvious failures fast (no cost)
   - Tier 2 ensures semantic alignment without manual review
   - Tier 3 is the critical identity check
   - Tier 4 provides human-level final check when needed

3. **Why fal.ai as primary provider?**
   - Best Flux + LoRA performance/cost ratio as of early 2026
   - Fast inference times (15-30s per image)
   - Provider abstraction allows easy switching if this changes

## Testing Strategy

Once implementation is complete, test in this order:

1. **Stage 0**: Create test character with 25-40 reference images
2. **Stage 1**: Generate content briefs, verify pillar distribution
3. **Stage 2**: Expand briefs with Claude, verify prompt quality
4. **Stage 3**: Generate test images, verify LoRA loading
5. **Stage 4**: Run quality gates, verify identity consistency
6. **End-to-End**: Generate complete post, verify all stages integrate

## Known Limitations

1. **Distinctive marks fidelity**: ~30-50% (documented in feasibility analysis)
2. **Hand artifacts**: Common in Flux - mitigated by quality gates
3. **Background consistency**: Varies by scene complexity
4. **Cost per regen**: $0.06 per retry - budget 2-3 attempts per post
