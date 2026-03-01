"""
Failure Mode Taxonomy — v2.0

Defines and handles all seven failure conditions identified in the architecture.
Every failure condition has a defined response mode, preventing undefined behavior
(arbitrary halt, loop, or silent degradation).

The seven failure conditions:
1. USER_UNAVAILABLE_AT_DECISION    → Pause + Escalate
2. AGENT_MALFORMED_OUTPUT          → Retry + Degrade
3. GROUND_TRUTH_CHECK_FAILED       → Surface + Loop
4. TOKEN_BUDGET_EXCEEDED           → Warn + Halt
5. POLICY_CONFLICT_DETECTED        → Halt + Escalate
6. GOAL_CONTRACT_AMBIGUITY         → Pause + Clarify
7. AGENT_ROLE_DRIFT_DETECTED       → Warn + Constrain

Design decisions that avoid future problems:
- Each failure mode returns a typed FailureResult, not a bare exception.
  The orchestrator pattern-matches on the result type to decide next steps.
- Retry counts are tracked per-agent per-session. This prevents an agent
  from silently consuming budget on infinite retries.
- Token budget warnings are emitted at 80% usage; halts at 100%.
  Partial results are always preserved on halt — never silently discarded.
- Role drift detection is based on log divergence from the agent's declared role.
  Detection is conservative: flags are not automatic rejections. They trigger
  re-prompting with a stricter constraint, not immediate failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class FailureCondition(str, Enum):
    USER_UNAVAILABLE_AT_DECISION = "user_unavailable_at_decision"
    AGENT_MALFORMED_OUTPUT = "agent_malformed_output"
    GROUND_TRUTH_CHECK_FAILED = "ground_truth_check_failed"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    POLICY_CONFLICT_DETECTED = "policy_conflict_detected"
    GOAL_CONTRACT_AMBIGUITY = "goal_contract_ambiguity"
    AGENT_ROLE_DRIFT_DETECTED = "agent_role_drift_detected"


class ResponseMode(str, Enum):
    PAUSE_AND_ESCALATE = "pause_and_escalate"
    RETRY_AND_DEGRADE = "retry_and_degrade"
    SURFACE_AND_LOOP = "surface_and_loop"
    WARN_AND_HALT = "warn_and_halt"
    HALT_AND_ESCALATE = "halt_and_escalate"
    PAUSE_AND_CLARIFY = "pause_and_clarify"
    WARN_AND_CONSTRAIN = "warn_and_constrain"


# Hard mapping: failure condition → required response mode.
# This cannot be overridden at runtime — it is the policy.
FAILURE_RESPONSE_MAP: Dict[FailureCondition, ResponseMode] = {
    FailureCondition.USER_UNAVAILABLE_AT_DECISION:  ResponseMode.PAUSE_AND_ESCALATE,
    FailureCondition.AGENT_MALFORMED_OUTPUT:         ResponseMode.RETRY_AND_DEGRADE,
    FailureCondition.GROUND_TRUTH_CHECK_FAILED:      ResponseMode.SURFACE_AND_LOOP,
    FailureCondition.TOKEN_BUDGET_EXCEEDED:          ResponseMode.WARN_AND_HALT,
    FailureCondition.POLICY_CONFLICT_DETECTED:       ResponseMode.HALT_AND_ESCALATE,
    FailureCondition.GOAL_CONTRACT_AMBIGUITY:        ResponseMode.PAUSE_AND_CLARIFY,
    FailureCondition.AGENT_ROLE_DRIFT_DETECTED:      ResponseMode.WARN_AND_CONSTRAIN,
}


@dataclass
class FailureResult:
    """
    Structured result for any failure condition.
    The orchestrator uses this to determine next steps.
    Never raises — always returns a typed result.
    """
    failure_id: str
    condition: FailureCondition
    response_mode: ResponseMode
    agent_name: Optional[str]
    detail: str
    partial_results: Any                    # Whatever was produced before failure
    retry_count: int = 0
    max_retries: int = 3
    can_retry: bool = False
    fallback_agent: Optional[str] = None   # For RETRY_AND_DEGRADE
    requires_user_input: bool = False
    surfaced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def is_terminal(self) -> bool:
        """True if this failure cannot be automatically recovered from."""
        return self.response_mode in (
            ResponseMode.WARN_AND_HALT,
            ResponseMode.HALT_AND_ESCALATE,
        )


class RetryTracker:
    """Tracks retry counts per agent per session to enforce maximum retry limits."""

    def __init__(self, max_retries: int = 3) -> None:
        self._max = max_retries
        self._counts: Dict[str, int] = {}

    def increment(self, agent_name: str) -> int:
        self._counts[agent_name] = self._counts.get(agent_name, 0) + 1
        return self._counts[agent_name]

    def exhausted(self, agent_name: str) -> bool:
        return self._counts.get(agent_name, 0) >= self._max

    def count(self, agent_name: str) -> int:
        return self._counts.get(agent_name, 0)


class TokenBudgetMonitor:
    """Monitors token usage and emits warnings at 80%, halts at 100%."""

    WARN_THRESHOLD = 0.80

    def __init__(self, budget: int, audit_callback: Optional[Callable] = None) -> None:
        self._budget = budget
        self._used = 0
        self._warned = False
        self._audit = audit_callback

    def consume(self, tokens: int) -> Optional[FailureResult]:
        """Record token consumption. Returns a FailureResult if budget exceeded."""
        self._used += tokens
        ratio = self._used / self._budget if self._budget > 0 else 1.0

        if ratio >= self.WARN_THRESHOLD and not self._warned:
            self._warned = True
            if self._audit:
                self._audit("TOKEN_BUDGET_WARNING", {
                    "used": self._used,
                    "budget": self._budget,
                    "ratio": ratio,
                })

        if ratio >= 1.0:
            if self._audit:
                self._audit("TOKEN_BUDGET_EXCEEDED", {
                    "used": self._used,
                    "budget": self._budget,
                })
            return FailureResult(
                failure_id=f"token_budget_{self._used}",
                condition=FailureCondition.TOKEN_BUDGET_EXCEEDED,
                response_mode=ResponseMode.WARN_AND_HALT,
                agent_name=None,
                detail=f"Token budget of {self._budget} exceeded at {self._used} tokens.",
                partial_results=None,
                requires_user_input=True,
            )
        return None

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._budget - self._used)


class FailureModeHandler:
    """
    Handles all seven failure conditions according to the defined response modes.

    Usage by RelayOrchestrator:
        result = handler.handle(
            condition=FailureCondition.AGENT_MALFORMED_OUTPUT,
            agent_name="creative_enhancer",
            detail="Output missing required 'proposal' key",
            partial_results=raw_output,
        )
        if result.is_terminal:
            return result  # surface to user
        if result.can_retry:
            # re-invoke agent with result.fallback_agent or same agent
    """

    def __init__(
        self,
        retry_tracker: Optional[RetryTracker] = None,
        token_monitor: Optional[TokenBudgetMonitor] = None,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
        fallback_agents: Optional[Dict[str, str]] = None,
    ) -> None:
        self._retry = retry_tracker or RetryTracker()
        self._token = token_monitor
        self._audit = audit_callback
        # Maps primary agent → fallback agent for RETRY_AND_DEGRADE
        self._fallbacks = fallback_agents or {}

    def handle(
        self,
        condition: FailureCondition,
        agent_name: Optional[str] = None,
        detail: str = "",
        partial_results: Any = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> FailureResult:
        """
        Handle a failure condition.

        Returns a FailureResult with the appropriate response mode and
        recovery instructions. Never raises.
        """
        import uuid as _uuid
        failure_id = str(_uuid.uuid4())
        mode = FAILURE_RESPONSE_MAP[condition]
        extra = extra or {}

        self._emit_audit("FAILURE_MODE_TRIGGERED", {
            "failure_id": failure_id,
            "condition": condition.value,
            "response_mode": mode.value,
            "agent_name": agent_name,
            "detail": detail,
            **extra,
        })

        if condition == FailureCondition.AGENT_MALFORMED_OUTPUT:
            return self._handle_malformed_output(failure_id, agent_name, detail, partial_results)

        if condition == FailureCondition.GROUND_TRUTH_CHECK_FAILED:
            return self._handle_ground_truth_fail(failure_id, agent_name, detail, partial_results)

        if condition == FailureCondition.TOKEN_BUDGET_EXCEEDED:
            return FailureResult(
                failure_id=failure_id,
                condition=condition,
                response_mode=mode,
                agent_name=agent_name,
                detail=detail,
                partial_results=partial_results,
                requires_user_input=True,
            )

        if condition == FailureCondition.POLICY_CONFLICT_DETECTED:
            return FailureResult(
                failure_id=failure_id,
                condition=condition,
                response_mode=mode,
                agent_name=agent_name,
                detail=detail,
                partial_results=partial_results,
                requires_user_input=True,
            )

        if condition == FailureCondition.AGENT_ROLE_DRIFT_DETECTED:
            return self._handle_role_drift(failure_id, agent_name, detail, partial_results)

        # USER_UNAVAILABLE_AT_DECISION and GOAL_CONTRACT_AMBIGUITY
        # are handled by ConflictResolutionProtocol; this just records the condition.
        return FailureResult(
            failure_id=failure_id,
            condition=condition,
            response_mode=mode,
            agent_name=agent_name,
            detail=detail,
            partial_results=partial_results,
            requires_user_input=True,
        )

    def _handle_malformed_output(
        self,
        failure_id: str,
        agent_name: Optional[str],
        detail: str,
        partial_results: Any,
    ) -> FailureResult:
        """RETRY_AND_DEGRADE: retry up to 3 times, then degrade to fallback agent."""
        retry_count = 0
        can_retry = False
        fallback = None

        if agent_name:
            retry_count = self._retry.increment(agent_name)
            can_retry = not self._retry.exhausted(agent_name)
            if not can_retry:
                fallback = self._fallbacks.get(agent_name)

        return FailureResult(
            failure_id=failure_id,
            condition=FailureCondition.AGENT_MALFORMED_OUTPUT,
            response_mode=ResponseMode.RETRY_AND_DEGRADE,
            agent_name=agent_name,
            detail=detail,
            partial_results=partial_results,
            retry_count=retry_count,
            max_retries=self._retry._max,
            can_retry=can_retry,
            fallback_agent=fallback,
        )

    def _handle_ground_truth_fail(
        self,
        failure_id: str,
        agent_name: Optional[str],
        detail: str,
        partial_results: Any,
    ) -> FailureResult:
        """SURFACE_AND_LOOP: trigger refinement loop (max 3), then surface to user."""
        retry_count = 0
        can_retry = False

        loop_key = f"gt_loop_{agent_name or 'pipeline'}"
        retry_count = self._retry.increment(loop_key)
        can_retry = not self._retry.exhausted(loop_key)

        return FailureResult(
            failure_id=failure_id,
            condition=FailureCondition.GROUND_TRUTH_CHECK_FAILED,
            response_mode=ResponseMode.SURFACE_AND_LOOP,
            agent_name=agent_name,
            detail=detail,
            partial_results=partial_results,
            retry_count=retry_count,
            max_retries=self._retry._max,
            can_retry=can_retry,
            requires_user_input=not can_retry,
        )

    def _handle_role_drift(
        self,
        failure_id: str,
        agent_name: Optional[str],
        detail: str,
        partial_results: Any,
    ) -> FailureResult:
        """WARN_AND_CONSTRAIN: flag in audit log; re-prompt with stricter constraint."""
        self._emit_audit("AGENT_ROLE_DRIFT_FLAGGED", {
            "agent_name": agent_name,
            "detail": detail,
            "action": "will_re_prompt_with_stricter_constraint",
        })
        return FailureResult(
            failure_id=failure_id,
            condition=FailureCondition.AGENT_ROLE_DRIFT_DETECTED,
            response_mode=ResponseMode.WARN_AND_CONSTRAIN,
            agent_name=agent_name,
            detail=detail,
            partial_results=partial_results,
            can_retry=True,  # Re-prompt counts as a retry
            retry_count=self._retry.increment(f"drift_{agent_name or 'unknown'}"),
            max_retries=self._retry._max,
        )

    def _emit_audit(self, event_type: str, payload: dict) -> None:
        if self._audit:
            self._audit(event_type, payload)
