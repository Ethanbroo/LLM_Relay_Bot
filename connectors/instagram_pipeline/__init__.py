"""Instagram AI Model Content Generation Pipeline.

This pipeline generates Instagram content featuring a consistent AI-generated character.
Built entirely within the LLM Relay Bot's connector architecture, it follows the same
patterns for approval gates, audit logging, and policy enforcement.

Architecture Stages:
- Stage 0: Character Foundation (one-time setup)
- Stage 1: Content Brief & Calendar System
- Stage 2: Post Intent Construction (image + video)
- Stage 3: Asset Generation Pipeline (Flux images + Kling video)
- Stage 4: Multi-Tier Quality Gate System (image + video gates)
- Stage 5: Post-Processing & Assembly (image/video/audio)
- Stage 6: Staging, Approval & Instagram Publishing

Content Formats:
- STATIC_IMAGE: Existing Flux image pipeline
- AVATAR_TALKING: Kling Avatar v2 Pro + ElevenLabs TTS
- NARRATIVE_REEL: Kling O3 Elements multi-shot video
- CINEMATIC_CLIP: Kling V3 Pro text-to-video
- GAMEPLAY_OVERLAY: Kling O1 Edit video-to-video

Design Principles:
1. Hash-anchored reproducibility
2. Character identity as a first-class object
3. Budget-aware generation with fallback chains
4. Cheap quality gates first, expensive last

See architecture plan document for complete specification.
"""

__version__ = "0.2.0"
