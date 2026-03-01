"""
Relay Orchestrator v2.0 — Router-Only Orchestrator

The orchestrator is a pure router. It:
- Invokes specialized agents in sequence
- Routes between agents based on GoalContract + task type
- Surfaces all decision points to the user or resolves via scoped policy
- Enforces the Epistemic Containment Layer before any option presentation
- Manages the Option Framing Audit Trail
- Applies the Failure Mode Taxonomy to all failure conditions
- Coordinates authority resolution via the AuthorityModelResolver

What the orchestrator is NOT:
- It never makes semantic decisions
- It never selects between options (presents them; user or policy selects)
- It never infers GoalContract gaps (halts and clarifies instead)
- It never bypasses the Epistemic Containment Layer

The pipeline sequence:
1. Intent Clarification Layer (CriticalThinkingAgent)
2. User Q&A round
3. Semantic Anchor generation + user confirmation
4. GoalContract construction + user confirmation
5. Authority Model initialization
6. ECL initialization
7. Agent relay (task-specific agents)
8. Tiered Validation
9. Final output with full audit trail

The relay loop per agent:
    output = call_agent(agent, context)
    output = check_role_drift(output, agent)
    if agent.requires_decision:
        options = extract_options(output)
        framed = ecl.apply(options)
        decision = resolve_decision(framed, decision_type)
        if isinstance(decision, HaltSignal):
            return decision
    context.append(output)

All decisions, influence events, and policy executions are in the audit trail.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from multi_agent_v2.goal_contract import GoalContract, ValidationTier
from multi_agent_v2.authority_model import (
    AuthorityModelResolver, DecisionType, AuthorityTier
)
from multi_agent_v2.conflict_resolution import (
    ConflictResolutionProtocol, HaltSignal, DecisionQueue
)
from multi_agent_v2.epistemic_containment import (
    EpistemicContainmentLayer, OptionEntry, OptionFramingAuditTrail, ContainmentStrictness
)
from multi_agent_v2.policy_in_loop import PolicyInLoopProtocol, PolicyDefinition
from multi_agent_v2.failure_modes import (
    FailureModeHandler, FailureCondition, FailureResult, RetryTracker, TokenBudgetMonitor
)
from multi_agent_v2.tiered_validation import TieredValidationSystem, TieredValidationReport
from multi_agent_v2.agents.intent_clarification import (
    CriticalThinkingAgent, SemanticAnchorAgent, ClarificationOutput, SemanticAnchorOutput
)


@dataclass
class AgentSpec:
    """Specification for an agent in the relay sequence."""
    name: str
    role: str                          # Advisory label; enforced by role drift detection
    requires_decision: bool = False    # Whether this agent produces a decision point
    decision_type: Optional[str] = None  # The DecisionType value if requires_decision
    fallback_agent: Optional[str] = None  # For RETRY_AND_DEGRADE


@dataclass
class RelayContext:
    """Mutable context that flows through the relay pipeline."""
    goal_contract: GoalContract
    task_type: str
    session_id: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    option_framing_log: List[Dict[str, Any]] = field(default_factory=list)
    policy_log: List[Dict[str, Any]] = field(default_factory=list)
    decision_log: List[Dict[str, Any]] = field(default_factory=list)
    failure_log: List[FailureResult] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def snapshot(self) -> dict:
        """Return a serializable snapshot for async queue persistence."""
        return {
            "session_id": self.session_id,
            "task_type": self.task_type,
            "contract_id": self.goal_contract.contract_id,
            "history_length": len(self.history),
            "started_at": self.started_at,
        }


@dataclass
class RelayResult:
    """Final output of the relay pipeline."""
    session_id: str
    contract_id: str
    output: Any
    validation_report: Optional[TieredValidationReport]
    audit: Dict[str, Any]             # Full audit trail
    halted: bool = False
    halt_signal: Optional[HaltSignal] = None
    failure: Optional[FailureResult] = None
    completed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "contract_id": self.contract_id,
            "output": self.output,
            "halted": self.halted,
            "halt_reason": self.halt_signal.reason if self.halt_signal else None,
            "failure": {
                "condition": self.failure.condition.value,
                "detail": self.failure.detail,
            } if self.failure else None,
            "validation": self.validation_report.to_dict() if self.validation_report else None,
            "audit": self.audit,
            "completed_at": self.completed_at,
        }


class RelayOrchestrator:
    """
    Multi-agent relay orchestrator implementing the v2.0 architecture.

    Instantiate once per task session. Each call to run() creates a new
    RelayContext and executes the full pipeline.
    """

    def __init__(
        self,
        llm_client: Any,
        policies: Optional[List[PolicyDefinition]] = None,
        decision_queue: Optional[DecisionQueue] = None,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
        notifier: Optional[Callable[[str, dict], None]] = None,
        token_budget: int = 50000,
        ecl_strictness: ContainmentStrictness = ContainmentStrictness.STRICT,
    ) -> None:
        self._llm = llm_client
        self._policies = policies or []
        self._queue = decision_queue or DecisionQueue()
        self._audit_cb = audit_callback
        self._notifier = notifier
        self._token_budget = token_budget
        self._ecl_strictness = ecl_strictness

    def run_clarification_phase(
        self, user_request: str
    ) -> ClarificationOutput:
        """
        Phase 1 of pipeline: Run the Intent Clarification Layer.
        Returns observations, unknowns, and questions for the user to answer.
        Does not modify state; user answers are collected externally.
        """
        agent = CriticalThinkingAgent(self._llm, audit_callback=self._audit_cb)
        try:
            output = agent.analyze(user_request)
        except ValueError as e:
            # AGENT_MALFORMED_OUTPUT failure
            handler = FailureModeHandler(audit_callback=self._audit_cb)
            handler.handle(
                FailureCondition.AGENT_MALFORMED_OUTPUT,
                agent_name="CriticalThinkingAgent",
                detail=str(e),
            )
            raise

        if output.role_drift_detected:
            handler = FailureModeHandler(audit_callback=self._audit_cb)
            handler.handle(
                FailureCondition.AGENT_ROLE_DRIFT_DETECTED,
                agent_name="CriticalThinkingAgent",
                detail="Recommendation-like language detected in clarification output.",
                partial_results=output.to_dict(),
            )
            # Still return output — drift is flagged, not blocking

        return output

    def run_anchor_phase(
        self,
        user_request: str,
        qa_pairs: List[Dict[str, str]],
        confirmed_by: str,
        user_anchor_text: Optional[str] = None,
    ) -> SemanticAnchorOutput:
        """
        Phase 2: Generate and confirm the Semantic Intent Anchor.
        The anchor is presented to the user for review; user_anchor_text is the
        (possibly modified) text they confirm.
        """
        agent = SemanticAnchorAgent(self._llm, audit_callback=self._audit_cb)
        draft = agent.generate_anchor(user_request, qa_pairs)
        final_text = user_anchor_text if user_anchor_text else draft.anchor_text
        return agent.confirm(draft, final_text, confirmed_by)

    def relay(
        self,
        goal_contract: GoalContract,
        task_type: str,
        agent_specs: List[AgentSpec],
        agent_invoke_fn: Callable[[str, RelayContext], Any],
        user_available_fn: Optional[Callable[[], bool]] = None,
        validators: Optional[List[Any]] = None,
    ) -> RelayResult:
        """
        Execute the full relay pipeline for a confirmed GoalContract.

        Args:
            goal_contract: Confirmed GoalContract (must have confirmed_at set).
            task_type: Task category label (e.g., "code_generation", "document_creation").
            agent_specs: Ordered list of agents to invoke.
            agent_invoke_fn: fn(agent_name, context) -> Any — invokes the named agent.
            user_available_fn: fn() -> bool — checks live user availability.
                               Defaults to always-available (for testing).
            validators: List of BaseValidator instances for tiered validation.

        Returns:
            RelayResult with full output and audit trail.
        """
        if not goal_contract.is_confirmed:
            raise ValueError(
                "GoalContract must be confirmed by the user before relay execution."
            )

        session_id = str(uuid.uuid4())
        context = RelayContext(
            goal_contract=goal_contract,
            task_type=task_type,
            session_id=session_id,
        )

        # Initialize all subsystems for this session
        authority = AuthorityModelResolver(goal_contract, audit_callback=self._audit_cb)
        ecl = EpistemicContainmentLayer(
            goal_contract,
            strictness=self._ecl_strictness,
            audit_callback=self._audit_cb,
        )
        framing_trail = OptionFramingAuditTrail(audit_callback=self._audit_cb)
        pitl = PolicyInLoopProtocol(
            goal_contract,
            policies=self._policies,
            audit_callback=self._audit_cb,
            session_id=session_id,
        )
        conflict = ConflictResolutionProtocol(
            goal_contract,
            queue=self._queue,
            audit_callback=self._audit_cb,
            notifier=self._notifier,
        )
        retry_tracker = RetryTracker(max_retries=3)
        token_monitor = TokenBudgetMonitor(self._token_budget, audit_callback=self._audit_cb)
        failure_handler = FailureModeHandler(
            retry_tracker=retry_tracker,
            token_monitor=token_monitor,
            audit_callback=self._audit_cb,
            fallback_agents={s.name: s.fallback_agent for s in agent_specs if s.fallback_agent},
        )
        user_available = user_available_fn or (lambda: True)

        self._emit_audit("RELAY_STARTED", {
            "session_id": session_id,
            "contract_id": goal_contract.contract_id,
            "task_type": task_type,
            "agent_count": len(agent_specs),
        })

        # ── Main relay loop ───────────────────────────────────────────────────
        for spec in agent_specs:
            output, halt_or_failure = self._invoke_agent(
                spec, context, agent_invoke_fn,
                authority, ecl, framing_trail, pitl, conflict,
                failure_handler, token_monitor, user_available,
            )
            if isinstance(halt_or_failure, HaltSignal):
                return RelayResult(
                    session_id=session_id,
                    contract_id=goal_contract.contract_id,
                    output=None,
                    validation_report=None,
                    audit=self._build_audit(context, framing_trail, pitl),
                    halted=True,
                    halt_signal=halt_or_failure,
                )
            if isinstance(halt_or_failure, FailureResult) and halt_or_failure.is_terminal:
                return RelayResult(
                    session_id=session_id,
                    contract_id=goal_contract.contract_id,
                    output=halt_or_failure.partial_results,
                    validation_report=None,
                    audit=self._build_audit(context, framing_trail, pitl),
                    halted=True,
                    failure=halt_or_failure,
                )
            context.history.append({"agent": spec.name, "output": output})

        # ── Tiered Validation ─────────────────────────────────────────────────
        final_output = self._compile_output(context)
        validation_report = None

        if validators:
            tv = TieredValidationSystem(
                goal_contract, validators=validators, audit_callback=self._audit_cb
            )
            validation_report = tv.run(final_output, context.snapshot())

            if not validation_report.meets_minimum_tier:
                # Ground truth check failed → SURFACE_AND_LOOP
                failure = failure_handler.handle(
                    FailureCondition.GROUND_TRUTH_CHECK_FAILED,
                    detail=(
                        f"Minimum validation tier {goal_contract.validation_tier_minimum.value} "
                        f"not met. Highest achieved: {validation_report.highest_tier_achieved}."
                    ),
                    partial_results=final_output,
                )
                if failure.is_terminal:
                    return RelayResult(
                        session_id=session_id,
                        contract_id=goal_contract.contract_id,
                        output=final_output,
                        validation_report=validation_report,
                        audit=self._build_audit(context, framing_trail, pitl),
                        halted=True,
                        failure=failure,
                    )

        # Generate PITL session summary if any policy executions occurred
        if context.policy_log:
            pitl.generate_session_summary()

        self._emit_audit("RELAY_COMPLETED", {
            "session_id": session_id,
            "contract_id": goal_contract.contract_id,
            "validation_passed": validation_report.overall_passed if validation_report else None,
        })

        return RelayResult(
            session_id=session_id,
            contract_id=goal_contract.contract_id,
            output=final_output,
            validation_report=validation_report,
            audit=self._build_audit(context, framing_trail, pitl),
        )

    def _invoke_agent(
        self,
        spec: AgentSpec,
        context: RelayContext,
        agent_invoke_fn: Callable,
        authority: AuthorityModelResolver,
        ecl: EpistemicContainmentLayer,
        framing_trail: OptionFramingAuditTrail,
        pitl: PolicyInLoopProtocol,
        conflict: ConflictResolutionProtocol,
        failure_handler: FailureModeHandler,
        token_monitor: TokenBudgetMonitor,
        user_available: Callable[[], bool],
    ) -> Tuple[Any, Optional[Any]]:
        """
        Invoke a single agent with full failure handling, ECL, and authority resolution.
        Returns (output, None) on success or (partial, HaltSignal|FailureResult) on failure.
        """
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                raw_output = agent_invoke_fn(spec.name, context)
            except Exception as e:
                failure = failure_handler.handle(
                    FailureCondition.AGENT_MALFORMED_OUTPUT,
                    agent_name=spec.name,
                    detail=str(e),
                    partial_results=None,
                )
                if failure.can_retry and attempt < max_attempts - 1:
                    continue
                return None, failure

            # Check for role drift
            output_text = raw_output if isinstance(raw_output, str) else json.dumps(raw_output)
            from multi_agent_v2.agents.intent_clarification import RoleDriftDetector
            drift_detected, drift_reasons = RoleDriftDetector().check(output_text)
            if drift_detected:
                failure = failure_handler.handle(
                    FailureCondition.AGENT_ROLE_DRIFT_DETECTED,
                    agent_name=spec.name,
                    detail="; ".join(drift_reasons),
                    partial_results=raw_output,
                )
                if failure.can_retry and attempt < max_attempts - 1:
                    continue  # Re-prompt with stricter constraint (next attempt)

            # Token budget check
            token_count = len(output_text.split()) * 2  # rough estimate
            budget_failure = token_monitor.consume(token_count)
            if budget_failure:
                return raw_output, budget_failure

            # If no decision required, return output
            if not spec.requires_decision or not spec.decision_type:
                return raw_output, None

            # ── Decision point ────────────────────────────────────────────────
            # Extract options from output (expect list or wrap scalar)
            if isinstance(raw_output, list):
                raw_options = raw_output
            else:
                raw_options = [raw_output]

            option_entries = [
                OptionEntry(
                    option_id=str(uuid.uuid4()),
                    content=opt,
                    proposed_by=spec.name,
                )
                for opt in raw_options
            ]

            # Apply Epistemic Containment Layer
            framed = ecl.apply(option_entries)
            context.option_framing_log.append(framed.to_dict())
            framing_trail.log(framed)

            # Resolve authority for this decision
            try:
                decision_type_enum = DecisionType(spec.decision_type)
            except ValueError:
                decision_type_enum = DecisionType.APPROVE_CREATIVE_ENHANCEMENT

            is_user_available = user_available()
            resolution = authority.resolve(
                decision_type=decision_type_enum,
                user_available=is_user_available,
            )

            if resolution.resolved_tier == AuthorityTier.SYSTEM:
                # System makes the procedural decision
                decision_value = framed.options[0].content if framed.options else None
                context.decision_log.append({
                    "decision_type": spec.decision_type,
                    "resolved_by": "system",
                    "value": decision_value,
                })
                return decision_value, None

            if resolution.resolved_tier == AuthorityTier.DELEGATED:
                # Policy-in-the-Loop
                try:
                    policy_result = pitl.apply(
                        spec.decision_type,
                        [o.content for o in framed.options],
                        context.snapshot(),
                    )
                    if policy_result is not None:
                        context.policy_log.append({
                            "decision_type": spec.decision_type,
                            "policy_result": policy_result,
                        })
                        return policy_result, None
                except Exception as e:
                    self._emit_audit("PITL_ERROR", {"error": str(e)})

            # User must decide — check availability
            if not is_user_available:
                halt = conflict.handle(
                    decision_type=spec.decision_type,
                    framed_options=[o.to_dict() if hasattr(o, 'to_dict') else {"content": o.content} for o in framed.options],
                    context_snapshot=context.snapshot(),
                )
                if isinstance(halt, HaltSignal):
                    return raw_output, halt

            # User is available — present framed options (surface to caller)
            # In a real system this would return to the UI layer.
            # Here we return the framed set and let the calling layer handle it.
            context.decision_log.append({
                "decision_type": spec.decision_type,
                "resolved_by": resolution.resolved_by,
                "awaiting_user": True,
                "framing_id": framed.framing_id,
            })
            return framed, None

        return None, failure_handler.handle(
            FailureCondition.AGENT_MALFORMED_OUTPUT,
            agent_name=spec.name,
            detail="Max retry attempts exhausted.",
        )

    def _compile_output(self, context: RelayContext) -> Any:
        """Compile the final output from the relay history."""
        if not context.history:
            return None
        last = context.history[-1]
        return last.get("output")

    def _build_audit(
        self,
        context: RelayContext,
        framing_trail: OptionFramingAuditTrail,
        pitl: PolicyInLoopProtocol,
    ) -> dict:
        """Build the full audit trail for the relay session."""
        return {
            "session_id": context.session_id,
            "contract_id": context.goal_contract.contract_id,
            "started_at": context.started_at,
            "history": [
                {"agent": h["agent"], "output_type": type(h["output"]).__name__}
                for h in context.history
            ],
            "option_framing_events": len(context.option_framing_log),
            "framing_log_entries": framing_trail.all_entries(),
            "policy_executions": len(context.policy_log),
            "decision_log": context.decision_log,
            "failure_count": len(context.failure_log),
        }

    def _emit_audit(self, event_type: str, payload: dict) -> None:
        if self._audit_cb:
            self._audit_cb(event_type, payload)
