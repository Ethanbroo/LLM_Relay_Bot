"""Quality Gate Orchestrator - runs all tiers in sequence.

Image pipeline (original):
1. Tier 1 (Heuristic) - Fast, free sanity checks
2. Tier 2 (CLIP) - Semantic alignment
3. Tier 3 (Identity) - Face embedding consistency
4. Tier 4 (LLM Vision) - Human-level quality review (optional, expensive)

Video pipeline (extended):
1.   Tier 1 (Heuristic) - File format, size, video metadata
1.5  Tier 1.5 (Motion) - Frozen frame / jitter detection
2.   Tier 2 (CLIP) - Thumbnail/first-frame semantic alignment
3.   Tier 3 (Identity) - First-frame face check
3.5  Tier 3.5 (Temporal) - Multi-frame identity drift
4.   Tier 4 (LLM Vision) - Optional GPT-4o-mini review

Early rejection: If any tier fails, stop immediately (don't waste compute).
"""

import logging
from typing import Optional

import numpy as np

from .models import (
    QualityGateResult,
    AggregatedGateResult,
    GateDecision,
    GateTier
)
from .heuristic_gate import HeuristicGate
from .clip_alignment_gate import CLIPAlignmentGate
from .identity_gate import IdentityConsistencyGate
from .llm_vision_gate import LLMVisionGate
from .motion_quality_gate import MotionQualityGate
from .temporal_consistency_gate import TemporalConsistencyGate
from .frame_extractor import FrameExtractor
from ..character.models import CharacterProfile
from ..character.face_embedder import FaceEmbedder

logger = logging.getLogger(__name__)


class QualityGateOrchestrator:
    """
    Runs all quality gates in sequence with early rejection.

    Design principle: Cheap gates first, expensive gates last.
    Stop immediately on failure to avoid wasting compute.
    """

    def __init__(
        self,
        character: CharacterProfile,
        openai_api_key: Optional[str] = None,
        enable_tier4: bool = False,  # Tier 4 is expensive, enable selectively
    ):
        """
        Initialize orchestrator.

        Args:
            character: Character profile for identity checking
            openai_api_key: OpenAI API key for Tier 4 (optional)
            enable_tier4: Whether to run Tier 4 LLM vision gate
        """
        self.character = character
        self.enable_tier4 = enable_tier4

        # Initialize gates
        self.tier1 = HeuristicGate()
        self.tier2 = CLIPAlignmentGate()
        self.tier3 = IdentityConsistencyGate(character=character)

        if enable_tier4:
            if not openai_api_key:
                raise ValueError("openai_api_key required when enable_tier4=True")
            self.tier4 = LLMVisionGate(openai_api_key=openai_api_key)
        else:
            self.tier4 = None

    def evaluate(
        self,
        image_path: str,
        prompt: str,
        is_hero_shot: bool = False,
        force_tier4: bool = False,
    ) -> AggregatedGateResult:
        """
        Run all quality gates on a single image.

        Args:
            image_path: Path to generated image
            prompt: Generation prompt (for CLIP alignment)
            is_hero_shot: Whether this is the primary image (affects Tier 4 threshold)
            force_tier4: Run Tier 4 even if disabled (for high-priority images)

        Returns:
            AggregatedGateResult with all tier results
        """
        logger.info("Running quality gates on %s", image_path)

        tier_results = []
        total_cost = 0.0
        total_time = 0.0

        # Tier 1: Heuristic checks (always run first)
        logger.info("Running Tier 1: Heuristic checks")
        tier1_result = self.tier1.evaluate(image_path)
        tier_results.append(tier1_result)
        total_time += tier1_result.execution_time_s

        if tier1_result.decision == GateDecision.FAIL:
            logger.warning("Tier 1 FAILED - stopping early")
            return self._build_result(
                tier_results,
                overall_decision=GateDecision.FAIL,
                rejection_reason=tier1_result.reason,
                total_cost=total_cost,
                total_time=total_time
            )

        # Tier 2: CLIP alignment
        logger.info("Running Tier 2: CLIP alignment")
        tier2_result = self.tier2.evaluate(image_path, prompt)
        tier_results.append(tier2_result)
        total_time += tier2_result.execution_time_s

        if tier2_result.decision == GateDecision.FAIL:
            logger.warning("Tier 2 FAILED - stopping early")
            return self._build_result(
                tier_results,
                overall_decision=GateDecision.FAIL,
                rejection_reason=tier2_result.reason,
                total_cost=total_cost,
                total_time=total_time
            )

        # Tier 3: Identity consistency (THE CRITICAL GATE)
        logger.info("Running Tier 3: Identity consistency")
        tier3_result = self.tier3.evaluate(image_path)
        tier_results.append(tier3_result)
        total_time += tier3_result.execution_time_s

        if tier3_result.decision == GateDecision.FAIL:
            logger.warning("Tier 3 FAILED - identity mismatch - stopping early")
            return self._build_result(
                tier_results,
                overall_decision=GateDecision.FAIL,
                rejection_reason=tier3_result.reason,
                total_cost=total_cost,
                total_time=total_time
            )

        # Tier 4: LLM vision (optional, expensive)
        tier4_result = None
        if (self.enable_tier4 or force_tier4) and self.tier4:
            logger.info("Running Tier 4: LLM vision review")
            context = {
                "prompt": prompt,
                "character_id": self.character.character_id,
                "is_hero_shot": is_hero_shot,
            }
            tier4_result = self.tier4.evaluate(image_path, context)
            tier_results.append(tier4_result)
            total_cost += tier4_result.gate_cost_usd
            total_time += tier4_result.execution_time_s

            if tier4_result.decision == GateDecision.FAIL:
                logger.warning("Tier 4 FAILED - LLM rejected")
                return self._build_result(
                    tier_results,
                    overall_decision=GateDecision.FAIL,
                    rejection_reason=tier4_result.reason,
                    total_cost=total_cost,
                    total_time=total_time
                )

        # Aggregate decisions
        overall_decision = self._aggregate_decisions(tier_results)
        rejection_reason = None if overall_decision == GateDecision.PASS else self._get_rejection_reason(tier_results)

        logger.info(
            "Quality gates complete: %s (cost: $%.4f, time: %.2fs)",
            overall_decision.value.upper(),
            total_cost,
            total_time
        )

        return self._build_result(
            tier_results,
            overall_decision=overall_decision,
            rejection_reason=rejection_reason,
            total_cost=total_cost,
            total_time=total_time
        )

    def _aggregate_decisions(self, tier_results: list[QualityGateResult]) -> GateDecision:
        """
        Aggregate tier decisions into overall decision.

        Logic:
        - If ANY tier = FAIL → overall FAIL
        - If ALL tiers = PASS → overall PASS
        - If ANY tier = MARGINAL (and none FAIL) → overall MARGINAL
        """
        has_fail = any(r.decision == GateDecision.FAIL for r in tier_results)
        has_marginal = any(r.decision == GateDecision.MARGINAL for r in tier_results)

        if has_fail:
            return GateDecision.FAIL
        elif has_marginal:
            return GateDecision.MARGINAL
        else:
            return GateDecision.PASS

    def _get_rejection_reason(self, tier_results: list[QualityGateResult]) -> str:
        """Get rejection reason from first failing tier."""
        for result in tier_results:
            if result.decision == GateDecision.FAIL:
                return f"Tier {result.tier.value} failed: {result.reason}"

        # If no FAIL, check for MARGINAL
        marginal_reasons = [
            f"Tier {r.tier.value}: {r.reason}"
            for r in tier_results
            if r.decision == GateDecision.MARGINAL
        ]
        if marginal_reasons:
            return "Marginal quality - " + "; ".join(marginal_reasons)

        return "Unknown reason"

    def _build_result(
        self,
        tier_results: list[QualityGateResult],
        overall_decision: GateDecision,
        rejection_reason: Optional[str],
        total_cost: float,
        total_time: float
    ) -> AggregatedGateResult:
        """Helper to build AggregatedGateResult."""
        return AggregatedGateResult(
            overall_decision=overall_decision,
            tier_results=tier_results,
            total_cost_usd=total_cost,
            total_time_s=total_time,
            rejection_reason=rejection_reason,
        )

    def evaluate_batch(
        self,
        image_paths: list[str],
        prompts: list[str],
        is_hero_shots: Optional[list[bool]] = None,
    ) -> list[AggregatedGateResult]:
        """
        Evaluate multiple images.

        Note: Early rejection is per-image, not batched.
        Each image goes through all tiers independently.

        Args:
            image_paths: List of image paths
            prompts: List of prompts (same order as images)
            is_hero_shots: Optional list of hero_shot flags

        Returns:
            List of AggregatedGateResults (same order as input)
        """
        if is_hero_shots is None:
            is_hero_shots = [False] * len(image_paths)

        if len(prompts) != len(image_paths):
            raise ValueError("prompts must have same length as image_paths")
        if len(is_hero_shots) != len(image_paths):
            raise ValueError("is_hero_shots must have same length as image_paths")

        results = []
        for image_path, prompt, is_hero in zip(image_paths, prompts, is_hero_shots):
            result = self.evaluate(image_path, prompt, is_hero)
            results.append(result)

        return results

    def get_pass_rate(self, results: list[AggregatedGateResult]) -> dict:
        """
        Calculate pass rate statistics across a batch of results.

        Useful for monitoring pipeline health and drift detection.

        Args:
            results: List of aggregated results

        Returns:
            Dict with pass_rate, fail_rate, marginal_rate, avg_cost, etc.
        """
        total = len(results)
        if total == 0:
            return {}

        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if r.failed)
        marginal = sum(1 for r in results if r.needs_review)

        total_cost = sum(r.total_cost_usd for r in results)
        total_time = sum(r.total_time_s for r in results)

        # Breakdown by tier
        tier3_failures = sum(
            1 for r in results
            if r.get_tier_result(GateTier.TIER_3_IDENTITY) and
            r.get_tier_result(GateTier.TIER_3_IDENTITY).decision == GateDecision.FAIL
        )

        return {
            "total_evaluated": total,
            "passed": passed,
            "failed": failed,
            "marginal": marginal,
            "pass_rate": passed / total,
            "fail_rate": failed / total,
            "marginal_rate": marginal / total,
            "avg_cost_usd": total_cost / total,
            "avg_time_s": total_time / total,
            "total_cost_usd": total_cost,
            "identity_failure_rate": tier3_failures / total,  # Critical metric for drift
        }


class VideoGateOrchestrator:
    """Runs quality gates in sequence for video content.
    Cheap gates first, expensive gates last. Early rejection.

    Gate order for video:
    1.0  HeuristicGate         — File format, size, corruption (FREE)
    1.5  MotionQualityGate     — Frozen frame / jitter detection (FREE)
    2.0  CLIPAlignmentGate     — Does thumbnail match prompt? (FREE, local CLIP)
    3.0  IdentityGate          — First-frame face check (FREE, local InsightFace)
    3.5  TemporalConsistencyGate — Multi-frame identity drift (FREE, local InsightFace)
    4.0  LLMVisionGate         — Optional GPT-4o-mini review ($0.01-0.03)
    """

    def __init__(self, character_profile, content_format: str, provider: Optional[str] = None):
        self.gates = self._build_gate_chain(character_profile, content_format, provider)

    def _build_gate_chain(self, profile, content_format, provider=None):
        chain = [HeuristicGate(media_type="video")]

        # Motion gate only for video formats
        if content_format != "static_image":
            chain.append(MotionQualityGate())

        chain.append(CLIPAlignmentGate())

        # Skip identity gates for providers without character reference support
        skip_identity = provider and provider.startswith("siliconflow")

        # Identity gates only when character is involved and provider supports it
        if not skip_identity and content_format in ("avatar_talking", "narrative_reel", "gameplay_overlay"):
            chain.append(IdentityConsistencyGate(
                reference_embedding=profile.face_embedding,
                threshold=0.65,
            ))
            # Temporal only for video with character
            chain.append(TemporalConsistencyGate(
                face_embedder=profile.face_embedder,
                reference_embedding=profile.face_embedding,
            ))

        # LLM vision gate optional, only for hero content
        if content_format == "narrative_reel":
            chain.append(LLMVisionGate())

        return chain

    async def evaluate(self, result, provider: Optional[str] = None):
        from .temporal_consistency_gate import GateResult
        for gate in self.gates:
            gate_result = await gate.evaluate(
                result.video_path if hasattr(result, 'video_path') else result.image_path
            )
            if not gate_result.passed:
                return gate_result  # Early rejection
        return GateResult(passed=True, gate_name="all_gates_passed")
