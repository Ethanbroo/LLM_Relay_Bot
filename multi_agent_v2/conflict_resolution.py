"""
Conflict Resolution Protocol — v2.0

Governs system behavior when the user is unavailable at a decision point.
User unavailability is a first-class scenario, not an afterthought.

Resolution path (applied in order):
1. If a pre-authorized policy covers the decision type → apply it (Policy-in-the-Loop).
   Log the policy used, the decision taken, and mark it as delegated authority.
2. If no policy covers it → place the decision in an async queue.
   Halt the current execution thread.
   Notify via configured notification channel.
3. On timeout (default 24h) without response → halt and preserve all state.
   Never infer. Never proceed past an unresolved decision point.
4. On resumption → present queued decision(s) with full context before continuing.

Design decisions that avoid future problems:
- The queue is persistent (survives process restart). Decisions are never lost.
- "Halt" means the pipeline stops immediately and returns a HaltSignal.
  The caller (RelayOrchestrator) is responsible for surfacing this to the user.
- Policy application here is governed by PolicyInLoopProtocol. This module
  delegates the actual policy execution there and only records the outcome.
- Notification is pluggable. Default is no-op (log only). Production deployments
  inject a real notifier (email, webhook, etc).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class ResolutionOutcome(str, Enum):
    POLICY_APPLIED = "policy_applied"
    QUEUED_FOR_ASYNC = "queued_for_async"
    HALTED = "halted"
    RESOLVED_BY_USER = "resolved_by_user"
    TIMED_OUT = "timed_out"


@dataclass
class PendingDecision:
    """A decision that could not be resolved immediately and is queued for async resolution."""
    decision_id: str
    decision_type: str
    context_snapshot: Dict[str, Any]          # Full pipeline context at time of pause
    framed_options: List[Dict[str, Any]]       # Options as presented (post-ECL)
    queued_at: str
    timeout_at: str
    contract_id: str
    resolved: bool = False
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_value: Optional[Any] = None
    resolution_outcome: Optional[str] = None

    def is_timed_out(self) -> bool:
        timeout = datetime.fromisoformat(self.timeout_at)
        return datetime.now(timezone.utc) > timeout

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PendingDecision":
        return cls(**data)


class HaltSignal:
    """
    Returned by the pipeline when execution must stop due to an unresolved decision.

    Contains enough context for resumption: the decision queue entry ID,
    the pipeline state snapshot, and instructions for the user.
    """

    def __init__(
        self,
        reason: str,
        pending_decision_id: str,
        contract_id: str,
        user_message: str,
    ) -> None:
        self.reason = reason
        self.pending_decision_id = pending_decision_id
        self.contract_id = contract_id
        self.user_message = user_message
        self.halted_at = datetime.now(timezone.utc).isoformat()

    def __repr__(self) -> str:
        return (
            f"HaltSignal(reason={self.reason!r}, "
            f"decision_id={self.pending_decision_id!r}, "
            f"contract_id={self.contract_id!r})"
        )

    def to_dict(self) -> dict:
        return {
            "halt": True,
            "reason": self.reason,
            "pending_decision_id": self.pending_decision_id,
            "contract_id": self.contract_id,
            "user_message": self.user_message,
            "halted_at": self.halted_at,
        }


class DecisionQueue:
    """
    Persistent async decision queue.

    Decisions are persisted as JSON Lines to a file so they survive process
    restart. On startup the queue is reloaded and any timed-out decisions
    are flagged (not silently discarded — the user is notified on resumption).

    Design note: We store all decisions including resolved ones.
    This gives a complete audit history of every pause-and-resume cycle.
    """

    def __init__(self, queue_file: str = "multi_agent_v2/decision_queue.jsonl") -> None:
        self._path = Path(queue_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._decisions: Dict[str, PendingDecision] = {}
        self._load()

    def _load(self) -> None:
        """Load existing decisions from disk."""
        if not self._path.exists():
            return
        with open(self._path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    d = PendingDecision.from_dict(data)
                    self._decisions[d.decision_id] = d
                except Exception:
                    pass  # Corrupted line; skip but don't crash

    def _append(self, decision: PendingDecision) -> None:
        """Append a single decision to the persistent queue file."""
        with open(self._path, "a") as f:
            f.write(json.dumps(decision.to_dict()) + "\n")

    def _rewrite(self) -> None:
        """Rewrite entire queue (used after resolution to update state)."""
        with open(self._path, "w") as f:
            for d in self._decisions.values():
                f.write(json.dumps(d.to_dict()) + "\n")

    def enqueue(self, decision: PendingDecision) -> None:
        self._decisions[decision.decision_id] = decision
        self._append(decision)

    def resolve(
        self,
        decision_id: str,
        resolved_by: str,
        resolution_value: Any,
        outcome: ResolutionOutcome,
    ) -> PendingDecision:
        d = self._decisions.get(decision_id)
        if d is None:
            raise KeyError(f"No pending decision with ID {decision_id!r}")
        if d.resolved:
            raise RuntimeError(f"Decision {decision_id!r} is already resolved.")
        d.resolved = True
        d.resolved_at = datetime.now(timezone.utc).isoformat()
        d.resolved_by = resolved_by
        d.resolution_value = resolution_value
        d.resolution_outcome = outcome.value
        self._rewrite()
        return d

    def get(self, decision_id: str) -> Optional[PendingDecision]:
        return self._decisions.get(decision_id)

    def pending(self, contract_id: Optional[str] = None) -> List[PendingDecision]:
        """Return all unresolved decisions, optionally filtered by contract."""
        return [
            d for d in self._decisions.values()
            if not d.resolved
            and (contract_id is None or d.contract_id == contract_id)
        ]

    def timed_out(self) -> List[PendingDecision]:
        """Return unresolved decisions that have exceeded their timeout."""
        return [d for d in self.pending() if d.is_timed_out()]


class ConflictResolutionProtocol:
    """
    Handles user-unavailability scenarios during pipeline execution.

    Usage (by RelayOrchestrator):
        outcome = protocol.handle(
            decision_type="approve_creative_enhancement",
            framed_options=[...],
            context_snapshot={...},
            policy_lookup_fn=...,
        )
        if isinstance(outcome, HaltSignal):
            return outcome  # Surface to caller; do not proceed

    Separation of concerns:
    - This class handles the "what to do" when a decision can't be resolved live.
    - AuthorityModelResolver handles "who CAN decide."
    - PolicyInLoopProtocol handles "how policy execution is logged and bounded."
    """

    def __init__(
        self,
        goal_contract,
        queue: Optional[DecisionQueue] = None,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
        notifier: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._contract = goal_contract
        self._queue = queue or DecisionQueue()
        self._audit = audit_callback
        self._notifier = notifier  # pluggable notification channel

    def handle(
        self,
        decision_type: str,
        framed_options: List[Dict[str, Any]],
        context_snapshot: Dict[str, Any],
        policy_apply_fn: Optional[Callable[..., Any]] = None,
    ) -> Any:
        """
        Attempt to resolve a decision when the user is unavailable.

        Returns:
            A resolved value (from policy) if policy covers the decision.
            A HaltSignal if the decision must be queued for async resolution.
        """
        # Step 1: Check if a pre-authorized policy covers this decision type
        if policy_apply_fn is not None:
            try:
                policy_result = policy_apply_fn(decision_type, framed_options, context_snapshot)
                if policy_result is not None:
                    self._emit_audit("CONFLICT_RESOLVED_BY_POLICY", {
                        "decision_type": decision_type,
                        "outcome": ResolutionOutcome.POLICY_APPLIED.value,
                    })
                    return policy_result
            except Exception as e:
                # Policy application failed — do not silently fall through; log and halt
                self._emit_audit("CONFLICT_POLICY_FAILED", {
                    "decision_type": decision_type,
                    "error": str(e),
                })
                # Fall through to queue

        # Step 2: No policy — queue for async resolution and halt
        timeout_hours = self._contract.conflict_resolution.timeout_hours
        now = datetime.now(timezone.utc)
        timeout_at = (now + timedelta(hours=timeout_hours)).isoformat()

        decision = PendingDecision(
            decision_id=str(uuid.uuid4()),
            decision_type=decision_type,
            context_snapshot=context_snapshot,
            framed_options=framed_options,
            queued_at=now.isoformat(),
            timeout_at=timeout_at,
            contract_id=self._contract.contract_id,
        )
        self._queue.enqueue(decision)

        self._emit_audit("DECISION_QUEUED", {
            "decision_id": decision.decision_id,
            "decision_type": decision_type,
            "timeout_at": timeout_at,
            "contract_id": self._contract.contract_id,
        })

        # Notify via configured channel
        if self._notifier:
            self._notifier("DECISION_AWAITING_USER", {
                "decision_id": decision.decision_id,
                "decision_type": decision_type,
                "timeout_at": timeout_at,
            })

        return HaltSignal(
            reason=f"Decision '{decision_type}' requires user input; user unavailable.",
            pending_decision_id=decision.decision_id,
            contract_id=self._contract.contract_id,
            user_message=(
                f"The pipeline has paused at decision type '{decision_type}'. "
                f"Please review the queued options and resume within {timeout_hours} hours. "
                f"Decision ID: {decision.decision_id}"
            ),
        )

    def resume(
        self,
        decision_id: str,
        resolved_by: str,
        resolution_value: Any,
    ) -> PendingDecision:
        """
        Resume a halted pipeline by resolving a queued decision.

        Called by the user (or operator) after receiving notification.
        Returns the resolved PendingDecision so the orchestrator can continue.
        """
        d = self._queue.resolve(
            decision_id=decision_id,
            resolved_by=resolved_by,
            resolution_value=resolution_value,
            outcome=ResolutionOutcome.RESOLVED_BY_USER,
        )
        self._emit_audit("DECISION_RESOLVED_BY_USER", {
            "decision_id": decision_id,
            "resolved_by": resolved_by,
            "contract_id": d.contract_id,
        })
        return d

    def check_timeouts(self) -> List[PendingDecision]:
        """
        Check for timed-out decisions. Returns the list for the caller to surface.
        Does NOT automatically discard or resolve them. Preserves state.
        """
        timed_out = self._queue.timed_out()
        for d in timed_out:
            self._emit_audit("DECISION_TIMED_OUT", {
                "decision_id": d.decision_id,
                "decision_type": d.decision_type,
                "queued_at": d.queued_at,
                "timeout_at": d.timeout_at,
                "contract_id": d.contract_id,
            })
        return timed_out

    def _emit_audit(self, event_type: str, payload: dict) -> None:
        if self._audit:
            self._audit(event_type, payload)
