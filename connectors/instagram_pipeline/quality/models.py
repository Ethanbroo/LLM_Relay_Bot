"""Data models for quality gate system."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class GateDecision(Enum):
    """Decision from a quality gate."""
    PASS = "pass"                    # Asset passes, continue to next tier
    FAIL = "fail"                    # Asset fails, reject immediately
    MARGINAL = "marginal"            # Borderline — flag for human review
    SKIP = "skip"                    # Gate skipped (e.g., missing dependencies)


class GateTier(Enum):
    """Quality gate tier levels."""
    TIER_1_HEURISTIC = 1
    TIER_1_5_MOTION = 15       # Video: frozen frame / jitter detection
    TIER_2_CLIP = 2
    TIER_3_IDENTITY = 3
    TIER_3_5_TEMPORAL = 35     # Video: multi-frame identity drift
    TIER_4_LLM_VISION = 4


@dataclass
class QualityGateResult:
    """
    Result from a single quality gate evaluation.

    Each tier produces one of these. The orchestrator aggregates
    all tier results to make the final decision.
    """
    tier: GateTier
    decision: GateDecision
    score: Optional[float] = None     # Numeric score if applicable (e.g., 0.72 for CLIP)
    threshold: Optional[float] = None # Threshold used for pass/fail
    reason: str = ""                  # Human-readable explanation
    details: dict = field(default_factory=dict)  # Additional diagnostic info
    gate_cost_usd: float = 0.0        # Cost to run this gate (for Tier 4 LLM calls)
    execution_time_s: float = 0.0


@dataclass
class AggregatedGateResult:
    """
    Final aggregated result from all quality gates.

    This is what the pipeline uses to decide whether to accept or reject
    the generated asset.
    """
    overall_decision: GateDecision
    tier_results: list[QualityGateResult]
    total_cost_usd: float = 0.0
    total_time_s: float = 0.0
    rejection_reason: Optional[str] = None  # Only set if overall_decision = FAIL

    @property
    def passed(self) -> bool:
        """Returns True if asset passed all gates."""
        return self.overall_decision == GateDecision.PASS

    @property
    def failed(self) -> bool:
        """Returns True if asset failed any gate."""
        return self.overall_decision == GateDecision.FAIL

    @property
    def needs_review(self) -> bool:
        """Returns True if asset is marginal and needs human review."""
        return self.overall_decision == GateDecision.MARGINAL

    def get_tier_result(self, tier: GateTier) -> Optional[QualityGateResult]:
        """Get result for specific tier."""
        for result in self.tier_results:
            if result.tier == tier:
                return result
        return None
