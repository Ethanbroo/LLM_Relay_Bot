"""Multi-tier still image validation gate for pre-animation quality control.

Runs BEFORE animation to ensure still images meet elevated quality thresholds.
Reuses existing quality gates from quality/ with tightened identity thresholds
(0.75/0.60 vs the default 0.65/0.50) since animation is expensive and we need
high confidence in identity match before spending on video generation.

Includes a retry loop: on validation failure, regenerates with a new seed
(up to max_attempts) before flagging for human review.
"""

import logging
import random
import time
from typing import Optional

from ..quality.models import (
    QualityGateResult,
    AggregatedGateResult,
    GateDecision,
    GateTier,
)
from ..quality.heuristic_gate import HeuristicGate
from ..quality.clip_alignment_gate import CLIPAlignmentGate
from ..quality.identity_gate import IdentityConsistencyGate
from ..character.models import CharacterProfile
from ..generation.base import AbstractAssetGenerator, GenerationRequest, GenerationResult
from .approval_store import ApprovalStore

logger = logging.getLogger(__name__)

# Tightened thresholds for pre-animation validation
STILL_IDENTITY_STRONG = 0.75    # Up from 0.65 — high confidence before animating
STILL_IDENTITY_MARGINAL = 0.60  # Up from 0.50

MAX_REGENERATION_ATTEMPTS = 4


class StillImageValidationGate:
    """Orchestrates multi-tier validation for still images before animation.

    Gate sequence (reuses existing gates with tighter thresholds):
    1. Heuristic  — file format, resolution, file size (HeuristicGate)
    2. CLIP       — semantic alignment with prompt (CLIPAlignmentGate)
    3. Identity   — face embedding at 0.75 threshold (IdentityConsistencyGate)

    On failure: retry with new seed (up to MAX_REGENERATION_ATTEMPTS).
    On success: record approval in ApprovalStore.
    """

    def __init__(
        self,
        character: CharacterProfile,
        approval_store: Optional[ApprovalStore] = None,
        identity_strong_threshold: float = STILL_IDENTITY_STRONG,
        identity_marginal_threshold: float = STILL_IDENTITY_MARGINAL,
    ):
        self.character = character
        self.store = approval_store

        # Reuse existing gates — no custom gate implementations needed
        self.tier1 = HeuristicGate()
        self.tier2 = CLIPAlignmentGate()
        self.tier3 = IdentityConsistencyGate(
            character=character,
            strong_threshold=identity_strong_threshold,
            marginal_threshold=identity_marginal_threshold,
        )

    def validate(
        self,
        image_path: str,
        prompt: str,
        scene: str = "",
        shot_hash: str = "",
        attempt_number: int = 1,
        seed: Optional[int] = None,
    ) -> AggregatedGateResult:
        """Run all validation tiers with early rejection.

        Returns:
            AggregatedGateResult with overall decision and per-tier results
        """
        start_time = time.time()
        tier_results = []

        # Tier 1: Heuristic checks (fast, free)
        t1_result = self.tier1.evaluate(image_path)
        tier_results.append(t1_result)
        if t1_result.decision == GateDecision.FAIL:
            return self._aggregate(tier_results, start_time)

        # Tier 2: CLIP alignment (moderate cost)
        t2_result = self.tier2.evaluate(image_path, prompt)
        tier_results.append(t2_result)
        if t2_result.decision == GateDecision.FAIL:
            return self._aggregate(tier_results, start_time)

        # Tier 3: Identity check (critical — uses tightened thresholds)
        t3_result = self.tier3.evaluate(image_path)
        tier_results.append(t3_result)

        # Build result
        result = self._aggregate(tier_results, start_time)

        # Record in approval store if available
        if self.store:
            gate_dict = {
                "overall_decision": result.overall_decision.value,
                "tier_results": [
                    {
                        "tier": r.tier.name,
                        "decision": r.decision.value,
                        "score": r.score,
                        "threshold": r.threshold,
                        "reason": r.reason,
                    }
                    for r in tier_results
                ],
            }

            # Extract scores
            identity_score = None
            clip_score = None
            for r in tier_results:
                if r.tier == GateTier.TIER_3_IDENTITY:
                    identity_score = r.score
                elif r.tier == GateTier.TIER_2_CLIP:
                    clip_score = r.score

            self.store.record(
                image_path=image_path,
                character_id=self.character.character_id,
                scene=scene,
                gate_results=gate_dict,
                attempt_number=attempt_number,
                prompt=prompt,
                seed=seed,
                shot_hash=shot_hash,
                identity_score=identity_score,
                clip_score=clip_score,
            )

        return result

    def validate_with_retry(
        self,
        generator: AbstractAssetGenerator,
        request: GenerationRequest,
        scene: str = "",
        shot_hash: str = "",
        max_attempts: int = MAX_REGENERATION_ATTEMPTS,
    ) -> tuple[Optional[str], AggregatedGateResult]:
        """Generate + validate loop with retry on failure.

        On each retry, mutates the seed for a new generation.
        Returns the first image that passes, or the last failed
        result if all attempts exhausted.

        Returns:
            (approved_image_path or None, final AggregatedGateResult)
        """
        last_result = None

        for attempt in range(1, max_attempts + 1):
            # Mutate seed on retries
            if attempt > 1:
                request.seed = random.randint(0, 2**32 - 1)

            logger.info(
                "Generation attempt %d/%d for scene=%s (seed=%s)",
                attempt, max_attempts, scene, request.seed,
            )

            # Generate
            gen_result = generator.generate(request)
            if not gen_result.image_path:
                logger.warning("Generation produced no image on attempt %d", attempt)
                continue

            # Validate
            result = self.validate(
                image_path=gen_result.image_path,
                prompt=request.prompt,
                scene=scene,
                shot_hash=shot_hash,
                attempt_number=attempt,
                seed=gen_result.seed_used,
            )
            last_result = result

            if result.passed:
                logger.info(
                    "Image approved on attempt %d (identity=%.3f)",
                    attempt,
                    self._get_identity_score(result),
                )
                return gen_result.image_path, result

            logger.info(
                "Attempt %d failed: %s (identity=%.3f)",
                attempt,
                result.rejection_reason or "unknown",
                self._get_identity_score(result),
            )

        # All retries exhausted
        logger.warning(
            "All %d attempts failed for scene=%s. Flagging for review.",
            max_attempts, scene,
        )
        return None, last_result

    def _aggregate(
        self,
        tier_results: list[QualityGateResult],
        start_time: float,
    ) -> AggregatedGateResult:
        """Aggregate tier results into a single decision."""
        total_cost = sum(r.gate_cost_usd for r in tier_results)
        total_time = time.time() - start_time

        # Overall decision: worst tier result wins
        decisions = [r.decision for r in tier_results]
        if GateDecision.FAIL in decisions:
            overall = GateDecision.FAIL
        elif GateDecision.MARGINAL in decisions:
            overall = GateDecision.MARGINAL
        else:
            overall = GateDecision.PASS

        # Rejection reason from first failing tier
        rejection_reason = None
        for r in tier_results:
            if r.decision == GateDecision.FAIL:
                rejection_reason = f"{r.tier.name}: {r.reason}"
                break

        return AggregatedGateResult(
            overall_decision=overall,
            tier_results=tier_results,
            total_cost_usd=total_cost,
            total_time_s=round(total_time, 2),
            rejection_reason=rejection_reason,
        )

    @staticmethod
    def _get_identity_score(result: AggregatedGateResult) -> float:
        """Extract identity score from aggregated result."""
        for r in result.tier_results:
            if r.tier == GateTier.TIER_3_IDENTITY:
                return r.score or 0.0
        return 0.0
