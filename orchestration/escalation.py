import hashlib
"""Escalation logic for Phase 6.

Phase 6 Invariant: Escalation paths are closed and finite.
"""

from enum import Enum
from typing import List, Optional
from orchestration.response_parser import LLMProposal
from orchestration.models import BaseLLMModel
from orchestration.errors import EscalationRequiredError


class EscalationReason(str, Enum):
    """Escalation reasons (closed enum)."""
    NO_CONSENSUS = "no_consensus"
    ALL_INVALID = "all_proposals_invalid"
    LOW_SIMILARITY = "all_similarities_below_threshold"


class EscalationDecision(str, Enum):
    """Escalation decisions (closed enum)."""
    ABORT = "ESCALATE_ABORT"
    REQUEST_HUMAN = "ESCALATE_REQUEST_HUMAN"
    REPROMPT_LATER = "ESCALATE_REPROMPT_LATER"


class EscalationEngine:
    """Escalation engine for failed consensus.

    Phase 6 Invariants:
    - Always uses chatgpt as escalation model
    - Receives only hashes + anonymized summaries
    - Does NOT see original model names
    - Produces one of three fixed decisions
    """

    ESCALATION_MODEL = "chatgpt"

    def __init__(self, escalation_model: BaseLLMModel):
        """Initialize escalation engine.

        Args:
            escalation_model: Model to use for escalation (must be chatgpt)
        """
        if escalation_model.model_id != self.ESCALATION_MODEL:
            raise ValueError(f"Escalation model must be {self.ESCALATION_MODEL}")

        self.model = escalation_model

    def escalate(
        self,
        reason: EscalationReason,
        proposals: List[LLMProposal],
        similarity_matrix: Optional[List[List[float]]] = None
    ) -> EscalationDecision:
        """Perform escalation.

        Phase 6 Invariant: Escalation receives anonymized data only.

        Args:
            reason: Escalation reason
            proposals: List of proposals (may be empty)
            similarity_matrix: Similarity matrix (may be None)

        Returns:
            Escalation decision (one of three enum values)
        """
        # Build anonymized prompt for escalation model
        prompt = self._build_escalation_prompt(reason, proposals, similarity_matrix)

        # Get task hash for deterministic stub response
        task_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        # Generate escalation decision (stub for now)
        response = self.model.generate_proposal(prompt, task_hash)

        # Parse decision from response
        decision = self._parse_escalation_decision(response)

        return decision

    def _build_escalation_prompt(
        self,
        reason: EscalationReason,
        proposals: List[LLMProposal],
        similarity_matrix: Optional[List[List[float]]]
    ) -> str:
        """Build anonymized escalation prompt."""

        anonymized_proposals = []

        for i, proposal in enumerate(proposals):
            anonymized_proposals.append({
                "id": f"proposal_{i}",
                "hash": hashlib.sha256(
                    proposal.proposal_hash.encode("utf-8")
                ).hexdigest()[:8],
                "confidence": proposal.confidence
            })

        prompt = f"""ESCALATION REQUIRED

    Reason: {reason.value}

    Proposals received: {len(proposals)}
    Anonymized data: {anonymized_proposals}

    You must decide ONE of the following:
    1. ESCALATE_ABORT - Abort this orchestration round
    2. ESCALATE_REQUEST_HUMAN - Request human intervention
    3. ESCALATE_REPROMPT_LATER - Retry with different prompts later

    Respond with EXACTLY ONE of these three options."""


        return prompt

    def _parse_escalation_decision(self, response: str) -> EscalationDecision:
        """Parse escalation decision from response.

        Args:
            response: Raw response from escalation model

        Returns:
            Escalation decision

        Raises:
            EscalationRequiredError: If response doesn't match any decision
        """
        response_upper = response.upper().strip()

        if "ESCALATE_ABORT" in response_upper:
            return EscalationDecision.ABORT
        elif "ESCALATE_REQUEST_HUMAN" in response_upper:
            return EscalationDecision.REQUEST_HUMAN
        elif "ESCALATE_REPROMPT_LATER" in response_upper:
            return EscalationDecision.REPROMPT_LATER
        else:
            # Default to request human if unclear
            return EscalationDecision.REQUEST_HUMAN


def should_escalate(
    proposals: List[LLMProposal],
    consensus_exists: bool,
    max_similarity: float,
    consensus_threshold: float
) -> tuple[bool, Optional[EscalationReason]]:
    """Check if escalation is required.

    Phase 6 Invariant: Escalation is rule-based, not heuristic.

    Args:
        proposals: List of proposals
        consensus_exists: Whether consensus was reached
        max_similarity: Maximum pairwise similarity
        consensus_threshold: Consensus threshold

    Returns:
        Tuple of (should_escalate, reason)
    """
    # No proposals
    if len(proposals) == 0:
        return True, EscalationReason.ALL_INVALID

    # No consensus
    if not consensus_exists:
        # Check if it's due to low similarity
        if max_similarity < consensus_threshold:
            return True, EscalationReason.LOW_SIMILARITY
        else:
            return True, EscalationReason.NO_CONSENSUS

    # Consensus exists - no escalation
    return False, None
