"""
Authority Model — Three-tier decision authority for v2.0.

Governs who may make each type of decision at each point in the relay pipeline.
Replaces the vague notion of "user confirmation" with a formal, auditable governance model.

Tier 1 — Primary Authority (User): Full authority over all semantic decisions.
Tier 2 — Delegated Authority (Policy): Executes pre-authorized policies within declared scope.
Tier 3 — System Authority (Orchestrator): Procedural routing only; never semantic decisions.

Key design decisions that prevent future problems:
- Decision types are enumerated. Anything not in the enum requires Tier 1.
- Tier 3 authority is hard-coded to procedural actions only. It cannot be expanded
  through configuration — only through code review and deployment.
- Every authority resolution is logged. The authority tier used for each decision
  is part of the audit trail, not just the decision itself.
- "User unavailable" is handled by ConflictResolutionProtocol, not here.
  This module answers "who CAN decide," not "what to do when they're absent."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Callable

from multi_agent_v2.goal_contract import GoalContract, AuthorityModel


class DecisionType(str, Enum):
    """Enumeration of all decision types in the relay pipeline."""
    # GoalContract lifecycle
    CONFIRM_GOAL_CONTRACT = "confirm_goal_contract"
    CONFIRM_SEMANTIC_ANCHOR = "confirm_semantic_anchor"

    # Creative / output decisions
    APPROVE_CREATIVE_ENHANCEMENT = "approve_creative_enhancement"
    SELECT_OUTPUT_FORMAT = "select_output_format"

    # Validation decisions
    SELECT_VALIDATION_TIER = "select_validation_tier"
    ACCEPT_SYNTHETIC_VALIDATION = "accept_synthetic_validation"

    # Policy management
    DEFINE_POLICY = "define_policy"
    APPROVE_POLICY_EXECUTION = "approve_policy_execution"
    AUDIT_POLICY_EXECUTION = "audit_policy_execution"

    # Refinement loop
    TRIGGER_REFINEMENT_LOOP = "trigger_refinement_loop"

    # Escalation
    RESOLVE_CONFLICT = "resolve_conflict"
    ACCEPT_ESCALATED_DECISION = "accept_escalated_decision"


class AuthorityTier(int, Enum):
    """Who holds authority for a given decision."""
    PRIMARY = 1      # Live user — required for all semantic decisions
    DELEGATED = 2    # Pre-authorized policy — bounded by declared scope
    SYSTEM = 3       # Orchestrator — procedural routing only


# Hard-coded: which decisions require which minimum tier.
# Tier 1 (PRIMARY) = only the live user can decide.
# Tier 2 (DELEGATED) = policy may decide if in scope, else escalate to Tier 1.
# Tier 3 (SYSTEM) = orchestrator may decide autonomously within defined bounds.
DECISION_AUTHORITY_MATRIX: Dict[DecisionType, AuthorityTier] = {
    DecisionType.CONFIRM_GOAL_CONTRACT:         AuthorityTier.PRIMARY,
    DecisionType.CONFIRM_SEMANTIC_ANCHOR:       AuthorityTier.PRIMARY,
    DecisionType.APPROVE_CREATIVE_ENHANCEMENT:  AuthorityTier.DELEGATED,
    DecisionType.SELECT_OUTPUT_FORMAT:          AuthorityTier.DELEGATED,
    DecisionType.SELECT_VALIDATION_TIER:        AuthorityTier.DELEGATED,
    DecisionType.ACCEPT_SYNTHETIC_VALIDATION:   AuthorityTier.PRIMARY,
    DecisionType.DEFINE_POLICY:                 AuthorityTier.PRIMARY,
    DecisionType.APPROVE_POLICY_EXECUTION:      AuthorityTier.PRIMARY,
    DecisionType.AUDIT_POLICY_EXECUTION:        AuthorityTier.DELEGATED,
    DecisionType.TRIGGER_REFINEMENT_LOOP:       AuthorityTier.SYSTEM,
    DecisionType.RESOLVE_CONFLICT:              AuthorityTier.PRIMARY,
    DecisionType.ACCEPT_ESCALATED_DECISION:     AuthorityTier.PRIMARY,
}

# Tier 3 system decisions: hard bounds on what the orchestrator may decide.
SYSTEM_AUTHORITY_BOUNDS: Dict[DecisionType, dict] = {
    DecisionType.TRIGGER_REFINEMENT_LOOP: {
        "max_iterations": 3,
        "description": "Orchestrator may trigger up to 3 refinement loop iterations "
                       "before surfacing to user.",
    },
}


@dataclass
class AuthorityResolution:
    """
    Result of resolving authority for a specific decision.

    Records which tier was used, whether a policy was applied, and the audit trail entry.
    """
    decision_type: DecisionType
    required_tier: AuthorityTier
    resolved_tier: AuthorityTier
    resolved_by: str                     # user_id, policy_id, or "system"
    policy_applied: Optional[str] = None # policy_id if Tier 2 was used
    within_system_bounds: Optional[bool] = None  # for Tier 3 decisions
    notes: str = ""


class AuthorityModelResolver:
    """
    Resolves decision authority given a GoalContract and the current user availability.

    This class answers: "For this decision type, which authority tier applies,
    and is the appropriate authority available?"

    It does NOT handle unavailability — that is delegated to ConflictResolutionProtocol.
    It does NOT make decisions — it resolves authority only.
    """

    def __init__(
        self,
        goal_contract: GoalContract,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._contract = goal_contract
        self._authority = goal_contract.authority_model
        self._audit = audit_callback
        self._system_iteration_counts: Dict[DecisionType, int] = {}

    def required_tier(self, decision_type: DecisionType) -> AuthorityTier:
        """Return the minimum authority tier required for a decision type."""
        return DECISION_AUTHORITY_MATRIX.get(decision_type, AuthorityTier.PRIMARY)

    def can_policy_handle(self, decision_type: DecisionType) -> bool:
        """
        Return True if a pre-authorized policy covers this decision type.

        The GoalContract's authority_model.policy_scope declares which decision
        types the policy owner is authorized to handle. We check both:
        - The minimum required tier allows delegation (Tier 2 or 3)
        - The decision type is in the declared policy scope
        """
        required = self.required_tier(decision_type)
        if required == AuthorityTier.PRIMARY:
            return False  # Must have live user; no delegation possible
        return decision_type.value in self._authority.policy_scope

    def can_system_handle(self, decision_type: DecisionType) -> bool:
        """
        Return True if the orchestrator (Tier 3) may handle this decision type
        and has not exceeded its defined iteration bounds.
        """
        required = self.required_tier(decision_type)
        if required != AuthorityTier.SYSTEM:
            return False
        bounds = SYSTEM_AUTHORITY_BOUNDS.get(decision_type, {})
        max_iter = bounds.get("max_iterations", 1)
        current = self._system_iteration_counts.get(decision_type, 0)
        return current < max_iter

    def record_system_action(self, decision_type: DecisionType) -> None:
        """Increment the iteration counter for a system-authority decision."""
        self._system_iteration_counts[decision_type] = (
            self._system_iteration_counts.get(decision_type, 0) + 1
        )

    def resolve(
        self,
        decision_type: DecisionType,
        user_available: bool,
        policy_id: Optional[str] = None,
    ) -> AuthorityResolution:
        """
        Resolve authority for a decision.

        Returns an AuthorityResolution describing who resolves the decision,
        at what tier, and with what constraints. Does NOT make the decision itself.

        If the live user is unavailable and no policy/system authority applies,
        the resolution indicates HALT is required — handled by ConflictResolutionProtocol.
        """
        required = self.required_tier(decision_type)

        # --- Tier 1: Live user required ---
        if required == AuthorityTier.PRIMARY:
            resolved_by = self._authority.primary_authority if user_available else "UNAVAILABLE"
            resolution = AuthorityResolution(
                decision_type=decision_type,
                required_tier=required,
                resolved_tier=AuthorityTier.PRIMARY,
                resolved_by=resolved_by,
                notes="Requires live user confirmation." if user_available
                      else "User unavailable. ConflictResolutionProtocol must handle.",
            )
            self._emit_audit("AUTHORITY_RESOLVED", resolution)
            return resolution

        # --- Tier 2: Policy may handle ---
        if required == AuthorityTier.DELEGATED:
            if self.can_policy_handle(decision_type) and policy_id:
                resolution = AuthorityResolution(
                    decision_type=decision_type,
                    required_tier=required,
                    resolved_tier=AuthorityTier.DELEGATED,
                    resolved_by=policy_id,
                    policy_applied=policy_id,
                    notes="Pre-authorized policy applied (Policy-in-the-Loop).",
                )
                self._emit_audit("AUTHORITY_RESOLVED_POLICY", resolution)
                return resolution
            # Policy not available — escalate to user
            resolved_by = self._authority.primary_authority if user_available else "UNAVAILABLE"
            resolution = AuthorityResolution(
                decision_type=decision_type,
                required_tier=required,
                resolved_tier=AuthorityTier.PRIMARY,
                resolved_by=resolved_by,
                notes="No applicable policy in scope; escalated to user."
                      if user_available
                      else "No applicable policy and user unavailable. Must halt.",
            )
            self._emit_audit("AUTHORITY_ESCALATED_TO_USER", resolution)
            return resolution

        # --- Tier 3: System may handle ---
        if required == AuthorityTier.SYSTEM:
            if self.can_system_handle(decision_type):
                self.record_system_action(decision_type)
                bounds = SYSTEM_AUTHORITY_BOUNDS.get(decision_type, {})
                resolution = AuthorityResolution(
                    decision_type=decision_type,
                    required_tier=required,
                    resolved_tier=AuthorityTier.SYSTEM,
                    resolved_by="system_orchestrator",
                    within_system_bounds=True,
                    notes=bounds.get("description", "System routing decision."),
                )
                self._emit_audit("AUTHORITY_RESOLVED_SYSTEM", resolution)
                return resolution
            # System bounds exceeded — surface to user
            resolved_by = self._authority.primary_authority if user_available else "UNAVAILABLE"
            resolution = AuthorityResolution(
                decision_type=decision_type,
                required_tier=required,
                resolved_tier=AuthorityTier.PRIMARY,
                resolved_by=resolved_by,
                within_system_bounds=False,
                notes="System authority bounds exceeded. Surfaced to user."
                      if user_available
                      else "Bounds exceeded and user unavailable. Must halt.",
            )
            self._emit_audit("AUTHORITY_BOUNDS_EXCEEDED", resolution)
            return resolution

        # Should never reach here — all tiers handled above
        raise RuntimeError(f"Unhandled authority tier: {required}")

    def _emit_audit(self, event_type: str, resolution: AuthorityResolution) -> None:
        if self._audit:
            self._audit(event_type, {
                "decision_type": resolution.decision_type.value,
                "required_tier": resolution.required_tier.value,
                "resolved_tier": resolution.resolved_tier.value,
                "resolved_by": resolution.resolved_by,
                "policy_applied": resolution.policy_applied,
                "within_system_bounds": resolution.within_system_bounds,
                "notes": resolution.notes,
                "contract_id": self._contract.contract_id,
            })
