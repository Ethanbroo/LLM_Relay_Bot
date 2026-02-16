"""Orchestration decision builder for Phase 6.

Phase 6 Invariant: Decision is data only and must re-enter Phase 1 validation.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from orchestration.uuid7 import generate_uuid7
from orchestration.escalation import EscalationDecision


@dataclass
class OrchestrationDecision:
    """Orchestration decision object.

    Phase 6 Invariant: This is DATA ONLY - no executable instructions.
    """
    schema_id: str
    schema_version: str
    run_id: str
    decision: str  # "consensus" or "escalated"
    selected_proposal_hash: Optional[str]
    selected_proposal_text: Optional[str]
    models_consulted: List[str]
    similarity_matrix: List[List[float]]
    consensus_score: Optional[float]
    escalation_reason: Optional[str]
    escalation_decision: Optional[str]

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation
        """
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "decision": self.decision,
            "selected_proposal_hash": self.selected_proposal_hash,
            "selected_proposal_text": self.selected_proposal_text,
            "models_consulted": self.models_consulted,
            "similarity_matrix": self.similarity_matrix,
            "consensus_score": self.consensus_score,
            "escalation_reason": self.escalation_reason,
            "escalation_decision": self.escalation_decision
        }


class OrchestrationDecisionBuilder:
    """Builder for orchestration decisions.

    Phase 6 Invariant: Builds data-only decision objects.
    """

    def __init__(self, run_id: Optional[str] = None):
        """Initialize decision builder.

        Args:
            run_id: Run ID (generates UUID v7 if not provided)
        """
        self.run_id = run_id or generate_uuid7()

    def build_consensus_decision(
        self,
        selected_proposal_hash: str,
        selected_proposal_text: str,
        models_consulted: List[str],
        similarity_matrix: List[List[float]],
        consensus_score: float
    ) -> OrchestrationDecision:
        """Build consensus decision.

        Args:
            selected_proposal_hash: Hash of selected proposal
            selected_proposal_text: Text of selected proposal
            models_consulted: List of model IDs consulted
            similarity_matrix: Pairwise similarity matrix
            consensus_score: Consensus score

        Returns:
            Orchestration decision
        """
        return OrchestrationDecision(
            schema_id="relay.orchestration_decision",
            schema_version="1.0.0",
            run_id=self.run_id,
            decision="consensus",
            selected_proposal_hash=selected_proposal_hash,
            selected_proposal_text=selected_proposal_text,
            models_consulted=models_consulted,
            similarity_matrix=similarity_matrix,
            consensus_score=consensus_score,
            escalation_reason=None,
            escalation_decision=None
        )

    def build_escalation_decision(
        self,
        models_consulted: List[str],
        similarity_matrix: List[List[float]],
        escalation_reason: str,
        escalation_decision: EscalationDecision
    ) -> OrchestrationDecision:
        """Build escalation decision.

        Args:
            models_consulted: List of model IDs consulted
            similarity_matrix: Pairwise similarity matrix
            escalation_reason: Reason for escalation
            escalation_decision: Escalation decision

        Returns:
            Orchestration decision
        """
        return OrchestrationDecision(
            schema_id="relay.orchestration_decision",
            schema_version="1.0.0",
            run_id=self.run_id,
            decision="escalated",
            selected_proposal_hash=None,
            selected_proposal_text=None,
            models_consulted=models_consulted,
            similarity_matrix=similarity_matrix,
            consensus_score=None,
            escalation_reason=escalation_reason,
            escalation_decision=escalation_decision.value
        )
