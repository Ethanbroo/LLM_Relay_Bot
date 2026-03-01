"""Video Creation Pipeline for LLM Relay Bot.

Adds the ability to produce MP4 and WebM video files from sequences of
AI-generated frames, with transitions, audio, text overlays, and timing control.

Architecture Stages:
- Storyboard generation (LLM-driven, via content brief)
- Timeline construction (storyboard -> precise rendering spec)
- Frame compositing (Pillow-based, Ken Burns, transitions, text overlays)
- Video encoding (FFmpeg subprocess)
- Audio mixing (pydub + FFmpeg)
- Quality gating (reuses existing tier 1-4 gates for AI-generated frames)

Integration Points:
- Audit: Emits VIDEO_* events to Phase 3 LogDaemon
- RBAC: video.create and video.render permissions via Phase 1 RBAC
- Quality: Reuses connectors.instagram_pipeline.quality gates
- Identity: Reuses connectors.instagram_pipeline.character for face verification

Design Principles:
1. Python-native (no Node.js / Remotion dependency)
2. FFmpeg for encoding (industry standard, already on most systems)
3. Stateless compositor (same timeline + frame number = same output)
4. Hash-anchored reproducibility at every stage
"""

__version__ = "0.1.0"
