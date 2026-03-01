"""Multi-tier quality gate system for generated assets.

This module implements a 6-tier quality gate system:
- Tier 1: Heuristic checks (file format, resolution, basic sanity)
- Tier 1.5: Motion quality checks (frozen frames, jitter — video only)
- Tier 2: CLIP semantic alignment (does image match prompt?)
- Tier 3: Face embedding identity consistency (is this the right person?)
- Tier 3.5: Temporal consistency (multi-frame identity drift — video only)
- Tier 4: LLM vision model review (GPT-4o-mini final check)
"""

from .models import QualityGateResult, AggregatedGateResult, GateDecision, GateTier
from .heuristic_gate import HeuristicGate
from .clip_alignment_gate import CLIPAlignmentGate
from .identity_gate import IdentityConsistencyGate
from .llm_vision_gate import LLMVisionGate
from .gate_orchestrator import QualityGateOrchestrator, VideoGateOrchestrator
from .frame_extractor import FrameExtractor
from .temporal_consistency_gate import TemporalConsistencyGate, GateResult
from .motion_quality_gate import MotionQualityGate

__all__ = [
    'QualityGateResult',
    'AggregatedGateResult',
    'GateDecision',
    'GateTier',
    'GateResult',
    'HeuristicGate',
    'CLIPAlignmentGate',
    'IdentityConsistencyGate',
    'LLMVisionGate',
    'QualityGateOrchestrator',
    'VideoGateOrchestrator',
    'FrameExtractor',
    'TemporalConsistencyGate',
    'MotionQualityGate',
]
