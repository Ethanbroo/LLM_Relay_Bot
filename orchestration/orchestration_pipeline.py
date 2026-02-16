"""Main orchestration pipeline for Phase 6.

Phase 6 Invariant: Every step is auditable and replayable.
"""

from typing import List, Optional
import numpy as np

from orchestration.models import ModelRegistry, BaseLLMModel
from orchestration.request_builder import LLMRequestBuilder
from orchestration.response_parser import ResponseParser, normalize_proposal
from orchestration.scoring import SimilarityScorer
from orchestration.consensus import ConsensusEngine
from orchestration.escalation import EscalationEngine, should_escalate, EscalationReason
from orchestration.decision import OrchestrationDecisionBuilder, OrchestrationDecision
from orchestration.errors import OrchestrationError, LLMResponseInvalidError


class OrchestrationPipeline:
    """Main orchestration pipeline.

    Phase 6 Invariants:
    - LLMs are stateless and replaceable
    - All model outputs treated as untrusted
    - Consensus is numeric and deterministic
    - No model can see another model's output
    - Final decisions made by code, not text
    - Every step is auditable
    - Phase 6 never emits executable instructions
    - Output must re-enter Phase 1 validation
    """

    def __init__(
        self,
        model_registry: ModelRegistry,
        consensus_threshold: float = 0.80,
        similarity_rounding: int = 3,
        escalation_model_id: str = "chatgpt",
        audit_callback: Optional[callable] = None,
        run_id: Optional[str] = None
    ):
        """Initialize orchestration pipeline.

        Args:
            model_registry: Registry of LLM models
            consensus_threshold: Threshold for consensus (default 0.80)
            similarity_rounding: Decimal places for similarity rounding
            escalation_model_id: Model ID for escalation
            audit_callback: Callback for Phase 3 audit events
            run_id: Run ID (generates UUID v7 if not provided)
        """
        self.model_registry = model_registry
        self.consensus_threshold = consensus_threshold
        self.audit_callback = audit_callback

        # Initialize components
        self.request_builder = LLMRequestBuilder(run_id=run_id)
        self.response_parser = ResponseParser()
        self.scorer = SimilarityScorer(similarity_rounding=similarity_rounding)
        self.consensus_engine = ConsensusEngine(consensus_threshold=consensus_threshold)
        self.decision_builder = OrchestrationDecisionBuilder(run_id=run_id)

        # Initialize escalation engine
        escalation_model = model_registry.get_model(escalation_model_id)
        self.escalation_engine = EscalationEngine(escalation_model)

    def _emit_audit_event(self, event_type: str, metadata: dict) -> None:
        """Emit audit event to Phase 3.

        Args:
            event_type: Event type
            metadata: Event metadata (hashes only, no raw text)
        """
        if self.audit_callback is not None:
            self.audit_callback(event_type, metadata)

    def orchestrate(
        self,
        model_ids: List[str],
        task_description: str,
        constraints: str
    ) -> OrchestrationDecision:
        """Run orchestration pipeline.

        Phase 6 Flow:
        1. Build requests for all models
        2. Generate proposals from each model
        3. Parse and normalize proposals
        4. Compute similarity scores
        5. Check consensus
        6. Select winning proposal OR escalate
        7. Return OrchestrationDecision

        Args:
            model_ids: List of model IDs to consult
            task_description: Task description
            constraints: Constraints

        Returns:
            OrchestrationDecision (data only)

        Raises:
            OrchestrationError: If orchestration fails
        """
        # Emit ORCHESTRATION_STARTED
        self._emit_audit_event("ORCHESTRATION_STARTED", {
            "models": model_ids,
            "num_models": len(model_ids)
        })

        # Step 1: Build requests
        requests = self.request_builder.build_requests_for_models(
            model_ids, task_description, constraints
        )

        # Step 2 & 3: Generate and parse proposals
        proposals = []
        for request in requests:
            try:
                # Emit LLM_REQUEST_SENT
                self._emit_audit_event("LLM_REQUEST_SENT", {
                    "model": request.model,
                    "prompt_hash": request.prompt_hash,
                    "task_hash": request.task_hash
                })

                # Generate proposal
                model = self.model_registry.get_model(request.model)
                response_text = model.generate_proposal(request.prompt, request.task_hash)

                # Parse response
                proposal = self.response_parser.parse_response(request.model, response_text)

                # Emit LLM_RESPONSE_ACCEPTED
                self._emit_audit_event("LLM_RESPONSE_ACCEPTED", {
                    "model": request.model,
                    "proposal_hash": proposal.proposal_hash,
                    "confidence": proposal.confidence
                })

            except LLMResponseInvalidError as e:
                # Emit LLM_RESPONSE_REJECTED
                self._emit_audit_event("LLM_RESPONSE_REJECTED", {
                    "model": request.model,
                    "reason": str(e)
                })
                # Continue with other models (Phase 6: malformed responses discarded)
                continue

        # Check if we have any valid proposals
        if len(proposals) == 0:
            # All proposals invalid - escalate
            self._emit_audit_event("CONSENSUS_FAILED", {
                "reason": "all_proposals_invalid"
            })

            escalation_decision = self.escalation_engine.escalate(
                EscalationReason.ALL_INVALID,
                proposals,
                None
            )

            self._emit_audit_event("ESCALATION_TRIGGERED", {
                "reason": EscalationReason.ALL_INVALID.value,
                "decision": escalation_decision.value
            })

            decision = self.decision_builder.build_escalation_decision(
                models_consulted=model_ids,
                similarity_matrix=[],
                escalation_reason=EscalationReason.ALL_INVALID.value,
                escalation_decision=escalation_decision
            )

            self._emit_audit_event("ORCHESTRATION_DECISION_EMITTED", {
                "decision_type": "escalated"
            })

            return decision

        # Step 4: Compute similarity scores
        proposal_texts = [p.proposal_text for p in proposals]
        similarity_matrix = self.scorer.compute_pairwise_similarities(proposal_texts)

        # Convert to list for serialization
        similarity_list = similarity_matrix.tolist()

        # Step 5: Check consensus
        consensus_exists = self.consensus_engine.check_consensus(proposals, similarity_matrix)

        # Compute max similarity for escalation check
        max_similarity = 0.0
        if len(proposals) > 1:
            for i in range(len(proposals)):
                for j in range(i + 1, len(proposals)):
                    max_similarity = max(max_similarity, similarity_matrix[i, j])

        # Step 6: Consensus or escalation
        needs_escalation, escalation_reason = should_escalate(
            proposals,
            consensus_exists,
            max_similarity,
            self.consensus_threshold
        )

        if needs_escalation:
            # Emit CONSENSUS_FAILED
            self._emit_audit_event("CONSENSUS_FAILED", {
                "reason": escalation_reason.value if escalation_reason else "unknown"
            })

            # Escalate
            escalation_decision = self.escalation_engine.escalate(
                escalation_reason,
                proposals,
                similarity_list
            )

            self._emit_audit_event("ESCALATION_TRIGGERED", {
                "reason": escalation_reason.value if escalation_reason else "unknown",
                "decision": escalation_decision.value
            })

            decision = self.decision_builder.build_escalation_decision(
                models_consulted=model_ids,
                similarity_matrix=similarity_list,
                escalation_reason=escalation_reason.value if escalation_reason else "unknown",
                escalation_decision=escalation_decision
            )

            self._emit_audit_event("ORCHESTRATION_DECISION_EMITTED", {
                "decision_type": "escalated"
            })

            return decision

        # Consensus reached - select proposal
        selected_proposal, consensus_score = self.consensus_engine.select_proposal(
            proposals, similarity_matrix
        )

        # Emit CONSENSUS_REACHED
        self._emit_audit_event("CONSENSUS_REACHED", {
            "selected_proposal_hash": selected_proposal.proposal_hash,
            "consensus_score": consensus_score
        })

        # Build decision
        decision = self.decision_builder.build_consensus_decision(
            selected_proposal_hash=selected_proposal.proposal_hash,
            selected_proposal_text=selected_proposal.proposal_text,
            models_consulted=model_ids,
            similarity_matrix=similarity_list,
            consensus_score=consensus_score
        )

        self._emit_audit_event("ORCHESTRATION_DECISION_EMITTED", {
            "decision_type": "consensus",
            "selected_hash": selected_proposal.proposal_hash
        })

        return decision
