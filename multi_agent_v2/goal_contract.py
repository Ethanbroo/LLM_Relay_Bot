"""
GoalContract v2.0 — Canonical intent representation for the multi-agent relay.

The GoalContract is the authoritative source of truth for:
- What the user explicitly wants (objective)
- Why they want it (semantic_intent_anchor)
- Constraints, success criteria, non-goals
- Who holds authority (authority_model)
- How conflicts are resolved (conflict_resolution)
- Minimum validation quality required (validation_tier_minimum)

Design decisions that avoid future problems:
- User MUST confirm the GoalContract before any agent acts on it.
  Skipping confirmation is not possible; the contract is immutable after confirmation.
- The semantic_intent_anchor is an LLM inference but is always presented to the user
  for correction. It acts as a drift detector, not an authoritative reading.
- risk_tolerance has three levels only. Enum prevents ambiguity.
- policy_in_loop_acknowledged is a required boolean that must be True when policies
  exist, forcing the user to consciously accept delegated authority.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class RiskTolerance(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OnTimeout(str, Enum):
    HALT = "halt"
    APPLY_POLICY = "apply_policy"
    ESCALATE = "escalate"


class ValidationTier(int, Enum):
    """Minimum validation tier: 1 = real external, 2 = structural, 3 = synthetic."""
    REAL = 1
    STRUCTURAL = 2
    SYNTHETIC = 3


@dataclass
class AuthorityModel:
    """
    Three-tier authority model.

    Tier 1 (primary_authority): The live user. All semantic decisions require this.
    Tier 2 (policy_owner): Pre-authorized policy executor. Delegated authority only.
    Tier 3 (system): Orchestrator. Procedural routing only, never semantic decisions.

    policy_in_loop_acknowledged MUST be True when policy_scope is non-empty.
    This forces the user to explicitly accept that policy execution is delegated,
    not live human, authority.
    """
    primary_authority: str                # user identifier
    policy_owner: Optional[str] = None   # role or user_id; None means no delegation
    policy_scope: List[str] = field(default_factory=list)  # decision types covered
    policy_in_loop_acknowledged: bool = False

    def validate(self) -> None:
        """Raise ValueError if the authority model is internally inconsistent."""
        if self.policy_scope and not self.policy_in_loop_acknowledged:
            raise ValueError(
                "policy_in_loop_acknowledged must be True when policy_scope is non-empty. "
                "The user must explicitly accept that pre-authorized policies execute "
                "with delegated authority, not live human oversight."
            )
        if self.policy_in_loop_acknowledged and not self.policy_scope:
            raise ValueError(
                "policy_in_loop_acknowledged is True but policy_scope is empty. "
                "Either define a policy scope or set policy_in_loop_acknowledged to False."
            )


@dataclass
class ConflictResolution:
    """
    Governs system behavior when the user is unavailable at a decision point.

    timeout_hours: How long to wait before applying on_timeout behavior.
    on_timeout: What to do — halt (safest), apply_policy (if scope allows), escalate.

    Design note: Default is halt (24h). This prioritizes correctness over throughput.
    Users who want continuity pre-authorize policies explicitly.
    """
    timeout_hours: int = 24
    on_timeout: OnTimeout = OnTimeout.HALT


@dataclass
class GoalContractConstraints:
    time: Optional[str] = None
    budget: Optional[str] = None
    technical: List[str] = field(default_factory=list)


@dataclass
class GoalContract:
    """
    Canonical GoalContract v2.0.

    Immutable after user confirmation (confirmed_at is set).
    contract_id is a deterministic SHA-256 of the canonical JSON representation,
    making it tamper-evident: any mutation changes the ID.
    """
    objective: str
    semantic_intent_anchor: str          # Why the user wants this (LLM draft, user reviewed)
    success_criteria: List[str]
    non_goals: List[str]
    risk_tolerance: RiskTolerance
    authority_model: AuthorityModel
    conflict_resolution: ConflictResolution
    validation_tier_minimum: ValidationTier = ValidationTier.REAL
    constraints: GoalContractConstraints = field(default_factory=GoalContractConstraints)

    # Set at confirmation time — do not set manually
    contract_id: str = field(default="", init=False)
    confirmed_at: Optional[str] = field(default=None, init=False)
    confirmed_by: Optional[str] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.authority_model.validate()
        # Contract ID is provisional until confirmed
        self.contract_id = self._compute_id()

    def _compute_id(self) -> str:
        """Deterministic SHA-256 of canonical JSON (excludes confirmed_at/confirmed_by)."""
        canonical = {
            "objective": self.objective,
            "semantic_intent_anchor": self.semantic_intent_anchor,
            "success_criteria": sorted(self.success_criteria),
            "non_goals": sorted(self.non_goals),
            "risk_tolerance": self.risk_tolerance.value,
            "validation_tier_minimum": self.validation_tier_minimum.value,
        }
        serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

    def confirm(self, confirmed_by: str) -> None:
        """
        Lock the GoalContract. Must be called before any agent processes it.
        After confirmation the contract_id is final and the contract is immutable.
        Attempting to confirm twice raises RuntimeError.
        """
        if self.confirmed_at is not None:
            raise RuntimeError(
                f"GoalContract {self.contract_id} is already confirmed. "
                "A confirmed contract cannot be modified."
            )
        self.confirmed_at = datetime.now(timezone.utc).isoformat()
        self.confirmed_by = confirmed_by
        # Recompute ID to include confirmation — prevents retroactive alteration
        self.contract_id = self._compute_id()

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def to_dict(self) -> dict:
        """Serialize to dict for audit logging and transmission."""
        return {
            "contract_id": self.contract_id,
            "objective": self.objective,
            "semantic_intent_anchor": self.semantic_intent_anchor,
            "success_criteria": self.success_criteria,
            "non_goals": self.non_goals,
            "risk_tolerance": self.risk_tolerance.value,
            "validation_tier_minimum": self.validation_tier_minimum.value,
            "constraints": {
                "time": self.constraints.time,
                "budget": self.constraints.budget,
                "technical": self.constraints.technical,
            },
            "authority_model": {
                "primary_authority": self.authority_model.primary_authority,
                "policy_owner": self.authority_model.policy_owner,
                "policy_scope": self.authority_model.policy_scope,
                "policy_in_loop_acknowledged": self.authority_model.policy_in_loop_acknowledged,
            },
            "conflict_resolution": {
                "timeout_hours": self.conflict_resolution.timeout_hours,
                "on_timeout": self.conflict_resolution.on_timeout.value,
            },
            "confirmed_at": self.confirmed_at,
            "confirmed_by": self.confirmed_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoalContract":
        """Deserialize from dict (e.g., loaded from storage)."""
        authority = AuthorityModel(
            primary_authority=data["authority_model"]["primary_authority"],
            policy_owner=data["authority_model"].get("policy_owner"),
            policy_scope=data["authority_model"].get("policy_scope", []),
            policy_in_loop_acknowledged=data["authority_model"].get(
                "policy_in_loop_acknowledged", False
            ),
        )
        conflict = ConflictResolution(
            timeout_hours=data["conflict_resolution"].get("timeout_hours", 24),
            on_timeout=OnTimeout(data["conflict_resolution"].get("on_timeout", "halt")),
        )
        raw_constraints = data.get("constraints", {})
        constraints = GoalContractConstraints(
            time=raw_constraints.get("time"),
            budget=raw_constraints.get("budget"),
            technical=raw_constraints.get("technical", []),
        )
        contract = cls(
            objective=data["objective"],
            semantic_intent_anchor=data["semantic_intent_anchor"],
            success_criteria=data["success_criteria"],
            non_goals=data["non_goals"],
            risk_tolerance=RiskTolerance(data["risk_tolerance"]),
            authority_model=authority,
            conflict_resolution=conflict,
            validation_tier_minimum=ValidationTier(
                data.get("validation_tier_minimum", 1)
            ),
            constraints=constraints,
        )
        # Restore confirmation state if present
        if data.get("confirmed_at"):
            contract.confirmed_at = data["confirmed_at"]
            contract.confirmed_by = data.get("confirmed_by")
        return contract
