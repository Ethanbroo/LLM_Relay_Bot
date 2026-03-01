"""
Policy-in-the-Loop (PITL) Transition Protocol — v2.0

Formally governs pre-authorized policy execution as delegated authority.

Key insight from the outline:
"When the system executes a pre-authorized policy, a human made that decision—but
at policy-definition time, not at execution time. The system must make this delegation
explicit in logs, outputs, and user-facing summaries."

PITL is NOT equivalent to Human-in-the-Loop (HITL). This module enforces that
distinction structurally:
- Every policy has a declared scope; execution outside scope is a hard error.
- Every execution is logged with the policy ID, decision type, timestamp, and outcome.
- After every session in PITL mode, a Policy Execution Summary is generated
  for the Policy Owner to review.
- Policy conflicts (two policies contradict) halt the pipeline immediately.
  The system never attempts autonomous conflict resolution between policies.

Design decisions that avoid future problems:
- Policies are version-stamped. If a policy is updated between sessions,
  the new version is used but the old version is logged for comparison.
- Policy expiry is enforced. Expired policies are treated as absent.
- The PITL acknowledgment in the GoalContract is required. This prevents
  policies from executing silently when the user didn't set them up intentionally.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


class PolicyStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


@dataclass
class PolicyDefinition:
    """
    A pre-authorized policy that may execute delegated decisions.

    Scope declaration is mandatory. A policy without explicit scope cannot execute.
    expiry_at: ISO datetime string. None means no expiry (not recommended).
    """
    policy_id: str
    version: str
    decision_types_covered: List[str]    # Subset of DecisionType values
    conditions: Dict[str, Any]           # Conditions under which policy applies
    action: str                          # What the policy does when applied
    defined_by: str                      # User or Policy Owner who defined this
    defined_at: str
    expiry_at: Optional[str] = None
    review_required_after: int = 1       # Force review after N sessions

    def is_in_scope(self, decision_type: str) -> bool:
        return decision_type in self.decision_types_covered

    def is_expired(self) -> bool:
        if self.expiry_at is None:
            return False
        expiry = datetime.fromisoformat(self.expiry_at)
        return datetime.now(timezone.utc) > expiry

    def status(self) -> PolicyStatus:
        if self.is_expired():
            return PolicyStatus.EXPIRED
        return PolicyStatus.ACTIVE

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyExecutionRecord:
    """Audit record for a single policy execution."""
    record_id: str
    policy_id: str
    policy_version: str
    decision_type: str
    decision_outcome: Any
    applied_at: str
    contract_id: str
    session_id: str
    in_declared_scope: bool
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PolicyConflictError(Exception):
    """Raised when two policies produce contradictory outcomes for the same decision."""
    def __init__(self, decision_type: str, policy_a: str, policy_b: str) -> None:
        super().__init__(
            f"Policy conflict at decision type '{decision_type}': "
            f"policy '{policy_a}' and policy '{policy_b}' are contradictory. "
            f"Pipeline halted. Policy Owner must resolve before execution can continue."
        )
        self.decision_type = decision_type
        self.policy_a = policy_a
        self.policy_b = policy_b


class PolicyOutOfScopeError(Exception):
    """Raised when a policy is asked to execute outside its declared scope."""
    def __init__(self, policy_id: str, decision_type: str) -> None:
        super().__init__(
            f"Policy '{policy_id}' is not authorized for decision type '{decision_type}'. "
            f"Halting rather than extrapolating policy intent."
        )
        self.policy_id = policy_id
        self.decision_type = decision_type


class PolicyInLoopProtocol:
    """
    Manages pre-authorized policy execution with full auditability.

    Provides apply() for single policy execution, detect_conflicts() for
    pre-flight conflict checking, and generate_session_summary() for
    post-session review by the Policy Owner.
    """

    def __init__(
        self,
        goal_contract,
        policies: Optional[List[PolicyDefinition]] = None,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._contract = goal_contract
        self._policies: Dict[str, PolicyDefinition] = {
            p.policy_id: p for p in (policies or [])
        }
        self._audit = audit_callback
        self._session_id = session_id or str(uuid.uuid4())
        self._execution_log: List[PolicyExecutionRecord] = []

    def register(self, policy: PolicyDefinition) -> None:
        """Register a policy. Supersedes any existing policy with the same ID."""
        if policy.policy_id in self._policies:
            old = self._policies[policy.policy_id]
            self._emit_audit("POLICY_SUPERSEDED", {
                "policy_id": policy.policy_id,
                "old_version": old.version,
                "new_version": policy.version,
            })
        self._policies[policy.policy_id] = policy
        self._emit_audit("POLICY_REGISTERED", {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "decision_types_covered": policy.decision_types_covered,
            "expiry_at": policy.expiry_at,
            "defined_by": policy.defined_by,
        })

    def find_applicable(
        self, decision_type: str
    ) -> List[PolicyDefinition]:
        """Return all active (non-expired) policies that cover this decision type."""
        return [
            p for p in self._policies.values()
            if p.is_in_scope(decision_type) and p.status() == PolicyStatus.ACTIVE
        ]

    def detect_conflicts(self, decision_type: str) -> None:
        """
        Check for conflicting policies at a decision type before execution.
        Raises PolicyConflictError if two policies would produce contradictory outcomes.
        This is called pre-flight by RelayOrchestrator to fail fast.
        """
        applicable = self.find_applicable(decision_type)
        if len(applicable) <= 1:
            return
        # Simple conflict detection: if more than one policy applies and they
        # have different action fields, treat as a conflict.
        # Production would use a more sophisticated semantic comparison.
        actions = {p.policy_id: p.action for p in applicable}
        action_values = list(set(actions.values()))
        if len(action_values) > 1:
            ids = list(actions.keys())
            raise PolicyConflictError(decision_type, ids[0], ids[1])

    def apply(
        self,
        decision_type: str,
        framed_options: List[Dict[str, Any]],
        context_snapshot: Dict[str, Any],
    ) -> Optional[Any]:
        """
        Apply the applicable policy for a decision type.

        Returns the policy's decision outcome, or None if no applicable policy.
        Raises PolicyConflictError if multiple contradictory policies apply.
        Raises PolicyOutOfScopeError if the policy is forced to decide outside scope.

        Every execution is logged to the execution_log and audit trail.
        """
        # Check PITL is acknowledged in GoalContract
        if not self._contract.authority_model.policy_in_loop_acknowledged:
            return None  # PITL not enabled; caller handles

        # Pre-flight conflict detection
        self.detect_conflicts(decision_type)

        applicable = self.find_applicable(decision_type)
        if not applicable:
            return None  # No policy covers this; caller must escalate

        policy = applicable[0]  # Conflict detection guarantees at most one

        # Enforce scope boundary (belt-and-suspenders)
        if not policy.is_in_scope(decision_type):
            raise PolicyOutOfScopeError(policy.policy_id, decision_type)

        # Execute policy action
        # In production this would invoke a structured action handler.
        # For now, the action field is returned as the decision outcome.
        outcome = self._execute_policy_action(policy, framed_options, context_snapshot)

        record = PolicyExecutionRecord(
            record_id=str(uuid.uuid4()),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            decision_type=decision_type,
            decision_outcome=outcome,
            applied_at=datetime.now(timezone.utc).isoformat(),
            contract_id=self._contract.contract_id,
            session_id=self._session_id,
            in_declared_scope=True,
            notes=f"Policy '{policy.policy_id}' applied via Policy-in-the-Loop. "
                  "This is delegated authority, not live human oversight.",
        )
        self._execution_log.append(record)

        self._emit_audit("POLICY_EXECUTED", {
            "record_id": record.record_id,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "decision_type": decision_type,
            "in_declared_scope": True,
            "contract_id": self._contract.contract_id,
            "session_id": self._session_id,
            "pitl_mode": True,  # Explicit PITL flag in every log entry
        })

        return outcome

    def _execute_policy_action(
        self,
        policy: PolicyDefinition,
        framed_options: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> Any:
        """
        Execute the policy action. Returns the resolved decision value.

        Action semantics (extensible):
        - "select_first": Select the first framed option.
        - "select_none": Decline all options (user must choose later).
        - "use_default:<value>": Return a literal default value.
        - Anything else: Return the action string as a signal for the caller.
        """
        action = policy.action
        if action == "select_first" and framed_options:
            return framed_options[0]
        if action == "select_none":
            return None
        if action.startswith("use_default:"):
            return action.split(":", 1)[1]
        # Unknown action → return as-is; caller interprets
        return action

    def generate_session_summary(self) -> Dict[str, Any]:
        """
        Generate a Policy Execution Summary for the Policy Owner to review.

        Called at end of every session where PITL mode was active.
        The summary shows: what was decided, by which policy, and whether any
        decisions fell outside declared scope (should never happen, but flagged).
        """
        summary = {
            "session_id": self._session_id,
            "contract_id": self._contract.contract_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_policy_executions": len(self._execution_log),
            "pitl_acknowledged": self._contract.authority_model.policy_in_loop_acknowledged,
            "executions": [r.to_dict() for r in self._execution_log],
            "out_of_scope_count": sum(
                1 for r in self._execution_log if not r.in_declared_scope
            ),
            "reviewer_action_required": any(
                not r.in_declared_scope for r in self._execution_log
            ),
        }
        self._emit_audit("PITL_SESSION_SUMMARY_GENERATED", {
            "session_id": self._session_id,
            "total_executions": summary["total_policy_executions"],
            "reviewer_action_required": summary["reviewer_action_required"],
        })
        return summary

    def _emit_audit(self, event_type: str, payload: dict) -> None:
        if self._audit:
            self._audit(event_type, payload)
