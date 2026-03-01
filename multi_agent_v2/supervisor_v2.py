"""
Multi-Agent Relay Supervisor v2.0

Integrates with the existing LLMRelaySupervisor (v1.0) by wrapping it and
adding the v2.0 GoalContract-driven pipeline on top.

Architecture:
- The existing supervisor handles all infrastructure (audit logging, Phase 1-8).
- This supervisor adds the v2.0 components: GoalContract, authority model,
  ECL, PITL, conflict resolution, tiered validation, and relay orchestration.
- The two supervisors share the same audit log daemon via callback injection.

Workflow:
1. User submits a task description.
2. run_clarification() invokes CriticalThinkingAgent → returns questions.
3. User provides answers.
4. run_anchor() invokes SemanticAnchorAgent → user confirms anchor.
5. build_goal_contract() constructs and presents the GoalContract for user confirmation.
6. execute() runs the full relay pipeline with all v2.0 safeguards.

The existing v1.0 supervisor remains functional. The v2.0 supervisor layers
on top without modifying v1.0 code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from multi_agent_v2.goal_contract import (
    GoalContract, AuthorityModel, ConflictResolution, GoalContractConstraints,
    RiskTolerance, OnTimeout, ValidationTier
)
from multi_agent_v2.authority_model import AuthorityModelResolver
from multi_agent_v2.conflict_resolution import DecisionQueue, ConflictResolutionProtocol
from multi_agent_v2.epistemic_containment import ContainmentStrictness, OptionFramingAuditTrail
from multi_agent_v2.policy_in_loop import PolicyInLoopProtocol, PolicyDefinition
from multi_agent_v2.failure_modes import FailureModeHandler, FailureCondition
from multi_agent_v2.tiered_validation import (
    TieredValidationSystem, BaseValidator,
    StaticAnalysisValidator, LogicalConsistencyValidator, LLMPeerReviewValidator
)
from multi_agent_v2.agents.intent_clarification import (
    CriticalThinkingAgent, SemanticAnchorAgent,
    ClarificationOutput, SemanticAnchorOutput
)
from multi_agent_v2.relay_orchestrator import (
    RelayOrchestrator, RelayResult, AgentSpec
)


class RelayV2Error(Exception):
    """Base exception for v2.0 relay errors."""
    pass


class LLMRelayV2Supervisor:
    """
    v2.0 supervisor that adds multi-agent relay capabilities to the existing system.

    This class does not inherit from LLMRelaySupervisor to keep the boundary clean.
    Instead it receives an optional audit_callback to bridge to the v1.0 LogDaemon.

    For standalone use (without v1.0), set base_supervisor=None and provide
    an optional audit_callback for logging.
    """

    def __init__(
        self,
        llm_client: Any,
        base_supervisor: Optional[Any] = None,  # LLMRelaySupervisor from v1.0
        policies: Optional[List[PolicyDefinition]] = None,
        ecl_strictness: ContainmentStrictness = ContainmentStrictness.STRICT,
        token_budget: int = 50000,
        queue_file: str = "multi_agent_v2/decision_queue.jsonl",
        notifier: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        """
        Args:
            llm_client: LLM client with a generate(prompt) -> str method.
                        Can be the existing ClaudeClient or any compatible client.
            base_supervisor: Optional v1.0 supervisor for infrastructure integration.
                             If provided, uses its LogDaemon for audit events.
            policies: Pre-authorized PolicyDefinition instances.
            ecl_strictness: Epistemic Containment Layer strictness level.
            token_budget: Maximum token budget per relay session.
            queue_file: Path to the persistent decision queue file.
            notifier: Optional notification callback for async decisions.
        """
        self._llm = llm_client
        self._base = base_supervisor
        self._policies = policies or []
        self._ecl_strictness = ecl_strictness
        self._token_budget = token_budget
        self._queue = DecisionQueue(queue_file)
        self._notifier = notifier

        # Build audit callback: bridge to v1.0 LogDaemon if available
        if base_supervisor and hasattr(base_supervisor, "log_daemon"):
            def audit_callback(event_type: str, payload: dict) -> None:
                try:
                    base_supervisor.log_daemon.ingest_event(
                        event_type=f"RELAY_V2_{event_type}",
                        actor="relay_v2",
                        correlation={"session_id": payload.get("session_id"),
                                     "message_id": None, "task_id": None},
                        payload=payload,
                    )
                except Exception:
                    pass  # Never let audit failure crash the pipeline
        else:
            def audit_callback(event_type: str, payload: dict) -> None:
                # Minimal stdout logging for standalone mode
                pass

        self._audit_cb = audit_callback

    # ─── Phase 1: Clarification ───────────────────────────────────────────────

    def run_clarification(self, user_request: str) -> ClarificationOutput:
        """
        Analyze the user request with the Intent Clarification Layer.
        Returns observations, unknowns, and questions for the user to answer.

        Does not modify any state. Safe to call multiple times.
        """
        agent = CriticalThinkingAgent(self._llm, audit_callback=self._audit_cb)
        output = agent.analyze(user_request)

        if output.role_drift_detected:
            handler = FailureModeHandler(audit_callback=self._audit_cb)
            handler.handle(
                FailureCondition.AGENT_ROLE_DRIFT_DETECTED,
                agent_name="CriticalThinkingAgent",
                detail="Role drift detected in clarification output.",
                partial_results=output.to_dict(),
            )

        return output

    # ─── Phase 2: Semantic Anchor ─────────────────────────────────────────────

    def run_anchor(
        self,
        user_request: str,
        qa_pairs: List[Dict[str, str]],
        confirmed_by: str,
        user_anchor_text: Optional[str] = None,
    ) -> SemanticAnchorOutput:
        """
        Generate and confirm the Semantic Intent Anchor.

        Args:
            user_request: Original task description.
            qa_pairs: List of {"question": ..., "answer": ...} from clarification phase.
            confirmed_by: User identifier confirming the anchor.
            user_anchor_text: User's final anchor text (may differ from LLM draft).
                              If None, the LLM draft is confirmed as-is.

        Returns:
            Confirmed SemanticAnchorOutput (confirmed_by_user=True).
        """
        agent = SemanticAnchorAgent(self._llm, audit_callback=self._audit_cb)
        draft = agent.generate_anchor(user_request, qa_pairs)
        final_text = user_anchor_text if user_anchor_text else draft.anchor_text
        return agent.confirm(draft, final_text, confirmed_by)

    # ─── Phase 3: GoalContract ────────────────────────────────────────────────

    def build_goal_contract(
        self,
        objective: str,
        semantic_intent_anchor: str,
        success_criteria: List[str],
        non_goals: List[str],
        primary_authority: str,
        risk_tolerance: str = "medium",
        validation_tier_minimum: int = 1,
        policy_owner: Optional[str] = None,
        policy_scope: Optional[List[str]] = None,
        policy_in_loop_acknowledged: bool = False,
        timeout_hours: int = 24,
        on_timeout: str = "halt",
        constraints: Optional[Dict[str, Any]] = None,
    ) -> GoalContract:
        """
        Construct a GoalContract from user-provided parameters.
        The contract is NOT confirmed — call confirm_goal_contract() separately.

        This separation ensures the user always sees the full GoalContract
        before any processing begins.
        """
        authority = AuthorityModel(
            primary_authority=primary_authority,
            policy_owner=policy_owner,
            policy_scope=policy_scope or [],
            policy_in_loop_acknowledged=policy_in_loop_acknowledged,
        )
        conflict = ConflictResolution(
            timeout_hours=timeout_hours,
            on_timeout=OnTimeout(on_timeout),
        )
        raw_constraints = constraints or {}
        gc_constraints = GoalContractConstraints(
            time=raw_constraints.get("time"),
            budget=raw_constraints.get("budget"),
            technical=raw_constraints.get("technical", []),
        )

        return GoalContract(
            objective=objective,
            semantic_intent_anchor=semantic_intent_anchor,
            success_criteria=success_criteria,
            non_goals=non_goals,
            risk_tolerance=RiskTolerance(risk_tolerance),
            authority_model=authority,
            conflict_resolution=conflict,
            validation_tier_minimum=ValidationTier(validation_tier_minimum),
            constraints=gc_constraints,
        )

    def confirm_goal_contract(
        self,
        goal_contract: GoalContract,
        confirmed_by: str,
    ) -> GoalContract:
        """
        Confirm the GoalContract. After this call the contract is immutable
        and the contract_id is final.
        """
        goal_contract.confirm(confirmed_by)
        self._audit_cb("GOAL_CONTRACT_CONFIRMED", {
            "contract_id": goal_contract.contract_id,
            "confirmed_by": confirmed_by,
            "objective_hash": _sha256_short(goal_contract.objective),
        })
        return goal_contract

    # ─── Phase 4: Execute ─────────────────────────────────────────────────────

    def execute(
        self,
        goal_contract: GoalContract,
        task_type: str,
        agent_specs: List[AgentSpec],
        agent_invoke_fn: Callable[[str, Any], Any],
        user_available_fn: Optional[Callable[[], bool]] = None,
        extra_validators: Optional[List[BaseValidator]] = None,
    ) -> RelayResult:
        """
        Execute the full v2.0 relay pipeline.

        Args:
            goal_contract: Confirmed GoalContract.
            task_type: Task category (e.g., "code_generation").
            agent_specs: Ordered list of agents to invoke.
            agent_invoke_fn: fn(agent_name, context) -> Any.
                             Calls the named agent with the current relay context.
            user_available_fn: fn() -> bool. Defaults to always True.
            extra_validators: Additional validators beyond defaults.

        Returns:
            RelayResult with output, validation report, and full audit trail.
        """
        if not goal_contract.is_confirmed:
            raise RelayV2Error(
                "GoalContract must be confirmed before execution. "
                "Call confirm_goal_contract() first."
            )

        # Build default validators based on validation tier minimum
        validators = self._build_validators(goal_contract, extra_validators)

        orchestrator = RelayOrchestrator(
            llm_client=self._llm,
            policies=self._policies,
            decision_queue=self._queue,
            audit_callback=self._audit_cb,
            notifier=self._notifier,
            token_budget=self._token_budget,
            ecl_strictness=self._ecl_strictness,
        )

        return orchestrator.relay(
            goal_contract=goal_contract,
            task_type=task_type,
            agent_specs=agent_specs,
            agent_invoke_fn=agent_invoke_fn,
            user_available_fn=user_available_fn,
            validators=validators,
        )

    def resume_halted_session(
        self,
        decision_id: str,
        resolved_by: str,
        resolution_value: Any,
    ) -> None:
        """
        Resume a halted pipeline by resolving a queued decision.
        After this call the caller must re-invoke execute() with the same GoalContract.
        The resolved decision will be retrieved from the queue on next run.
        """
        protocol = ConflictResolutionProtocol(
            # Minimal contract needed for logging; full contract passed on re-execute
            goal_contract=_MinimalContractProxy(decision_id),
            queue=self._queue,
            audit_callback=self._audit_cb,
        )
        # Direct queue resolution without full protocol context
        self._queue.resolve(decision_id, resolved_by, resolution_value, _import_outcome())

        self._audit_cb("SESSION_RESUMED", {
            "decision_id": decision_id,
            "resolved_by": resolved_by,
        })

    def _build_validators(
        self,
        goal_contract: GoalContract,
        extra: Optional[List[BaseValidator]],
    ) -> List[BaseValidator]:
        """Build the default validator set based on the GoalContract's minimum tier."""
        validators: List[BaseValidator] = []
        min_tier = goal_contract.validation_tier_minimum.value

        # Always include at least structural validation
        validators.append(LogicalConsistencyValidator())

        if min_tier <= 1:
            # Tier 1 required: add real validators
            validators.append(StaticAnalysisValidator())

        if min_tier <= 3:
            # Tier 3 always available but labeled SYNTHETIC
            validators.append(LLMPeerReviewValidator(self._llm))

        if extra:
            validators.extend(extra)

        return validators


def _sha256_short(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _import_outcome():
    from multi_agent_v2.conflict_resolution import ResolutionOutcome
    return ResolutionOutcome.RESOLVED_BY_USER


class _MinimalContractProxy:
    """Minimal proxy to satisfy ConflictResolutionProtocol without a full GoalContract."""
    def __init__(self, decision_id: str) -> None:
        self.contract_id = f"proxy_{decision_id}"

    class conflict_resolution:
        timeout_hours = 24

    class authority_model:
        policy_in_loop_acknowledged = False


# ─── Demo / Entry point ───────────────────────────────────────────────────────

def demo_standalone():
    """
    Demonstrate the v2.0 relay supervisor in standalone mode (no v1.0 infrastructure).
    Uses stub LLM responses.
    """

    class StubLLM:
        """Minimal stub that returns canned responses for demonstration."""
        def generate(self, prompt: str) -> str:
            import json as _json
            # SemanticAnchorAgent prompt: contains "one paragraph" and does NOT ask for JSON
            if "one paragraph" in prompt or ("underlying purpose" in prompt and "JSON" not in prompt[:200]):
                return (
                    "The user seeks to automate a data transformation task that currently "
                    "requires manual intervention. The underlying purpose is to reduce "
                    "the time spent on repetitive operations and ensure consistency across "
                    "runs. Success means the output is reproducible, verifiable, and integrates "
                    "into the user's existing workflow without additional tooling."
                )
            # CriticalThinkingAgent prompt: always asks for JSON with observations/unknowns/questions
            if '"observations"' in prompt or "observations" in prompt.lower():
                return _json.dumps({
                    "observations": [
                        "The request involves generating structured output.",
                        "No file format was specified.",
                    ],
                    "unknowns": [
                        "Target audience is unspecified.",
                        "Acceptable error rate is undefined.",
                    ],
                    "questions": [
                        "What output format is expected (JSON, CSV, plain text)?",
                        "Should the output be saved to disk or returned in memory?",
                    ],
                })
            return "This is a stub response from the LLM."

    llm = StubLLM()
    supervisor = LLMRelayV2Supervisor(llm, token_budget=10000)

    # Phase 1: Clarification
    user_request = "Transform the CSV files in /data/input into normalized JSON for our API."
    print("=== Phase 1: Intent Clarification ===")
    clarification = supervisor.run_clarification(user_request)
    print(f"Observations: {clarification.observations}")
    print(f"Questions for user: {clarification.questions}")

    # Simulate user answering questions
    qa_pairs = [
        {"question": clarification.questions[0], "answer": "JSON, one object per row"},
        {"question": clarification.questions[1], "answer": "Return in memory; also save to /data/output"},
    ]

    # Phase 2: Semantic Anchor
    print("\n=== Phase 2: Semantic Anchor ===")
    anchor = supervisor.run_anchor(user_request, qa_pairs, confirmed_by="user_123")
    print(f"Anchor: {anchor.anchor_text}")

    # Phase 3: GoalContract
    print("\n=== Phase 3: GoalContract ===")
    gc = supervisor.build_goal_contract(
        objective="Transform CSV files in /data/input into normalized JSON",
        semantic_intent_anchor=anchor.anchor_text,
        success_criteria=[
            "All CSV rows produce valid JSON objects",
            "Output written to /data/output/normalized.json",
            "Static analysis passes on generated code",
        ],
        non_goals=["Schema inference", "Data validation beyond type normalization"],
        primary_authority="user_123",
        risk_tolerance="low",
        validation_tier_minimum=2,
    )
    gc = supervisor.confirm_goal_contract(gc, confirmed_by="user_123")
    print(f"GoalContract confirmed: {gc.contract_id}")

    # Phase 4: Execute relay
    print("\n=== Phase 4: Relay Execution ===")
    agent_specs = [
        AgentSpec(name="code_generator", role="advisory_code_producer"),
        AgentSpec(name="reviewer", role="advisory_reviewer"),
    ]

    def agent_invoke(agent_name: str, context: Any) -> str:
        """Stub agent invocation."""
        if agent_name == "code_generator":
            return (
                "import csv, json\n"
                "with open('/data/input/data.csv') as f:\n"
                "    rows = list(csv.DictReader(f))\n"
                "with open('/data/output/normalized.json', 'w') as f:\n"
                "    json.dump(rows, f, indent=2)\n"
            )
        return f"Review passed for {agent_name}."

    result = supervisor.execute(
        goal_contract=gc,
        task_type="code_generation",
        agent_specs=agent_specs,
        agent_invoke_fn=agent_invoke,
    )

    print(f"Relay completed. Halted: {result.halted}")
    if result.validation_report:
        print(f"Validation passed: {result.validation_report.overall_passed}")
        print(f"Trust level: {result.validation_report.trust_label.value}")
    print(f"Audit events captured: {result.audit.get('option_framing_events', 0)} framing events")
    print(f"Output preview: {str(result.output)[:100]}")


if __name__ == "__main__":
    demo_standalone()
