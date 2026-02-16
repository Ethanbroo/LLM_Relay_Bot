"""Consensus algorithm for Phase 6.

Phase 6 Invariants:
- Consensus is numeric and deterministic
- Model identity does NOT change weights
- Confidence scores are logged but not used
"""

import numpy as np
from typing import List, Optional, Tuple
from orchestration.response_parser import LLMProposal
from orchestration.errors import ConsensusFailedError


class ConsensusEngine:
    """Consensus engine using similarity-based voting.

    Phase 6 Invariant: Consensus logic is purely numeric.
    """

    def __init__(self, consensus_threshold: float = 0.80):
        """Initialize consensus engine.

        Args:
            consensus_threshold: Minimum similarity for consensus
        """
        self.consensus_threshold = consensus_threshold

    def check_consensus(
        self,
        proposals: List[LLMProposal],
        similarity_matrix: np.ndarray
    ) -> bool:
        """Check if consensus exists.

        Phase 6 Invariant: Consensus exists iff max_pairwise_similarity ≥ threshold.

        Args:
            proposals: List of proposals
            similarity_matrix: Pairwise similarity matrix

        Returns:
            True if consensus exists, False otherwise
        """
        if len(proposals) == 0:
            return False

        if len(proposals) == 1:
            return True

        # Get max pairwise similarity (excluding diagonal)
        n = len(proposals)
        max_similarity = 0.0

        for i in range(n):
            for j in range(i + 1, n):
                max_similarity = max(max_similarity, similarity_matrix[i, j])

        return bool(max_similarity >= self.consensus_threshold)

    def select_proposal(
        self,
        proposals: List[LLMProposal],
        similarity_matrix: np.ndarray
    ) -> Tuple[LLMProposal, float]:
        """Select winning proposal from consensus.

        Phase 6 Invariant:
        1. Select proposal with highest average similarity to others
        2. Ties broken by lexicographic proposal_hash

        Args:
            proposals: List of proposals
            similarity_matrix: Pairwise similarity matrix

        Returns:
            Tuple of (selected_proposal, consensus_score)

        Raises:
            ConsensusFailedError: If no consensus
        """
        if not self.check_consensus(proposals, similarity_matrix):
            raise ConsensusFailedError("No consensus reached")

        n = len(proposals)

        if n == 1:
            return proposals[0], 1.0

        # Compute average similarity for each proposal
        avg_similarities = []
        for i in range(n):
            # Average similarity to all other proposals
            avg_sim = (similarity_matrix[i, :].sum() - 1.0) / (n - 1)
            avg_similarities.append(avg_sim)

        # Find max average similarity
        max_avg_sim = max(avg_similarities)

        # Get all proposals with max average similarity (for tie-breaking)
        candidates = []
        for i, avg_sim in enumerate(avg_similarities):
            if avg_sim == max_avg_sim:
                candidates.append(proposals[i])

        # Tie-break by lexicographic proposal_hash
        selected = min(candidates, key=lambda p: p.proposal_hash)

        return selected, max_avg_sim

    def compute_consensus_score(
        self,
        proposals: List[LLMProposal],
        similarity_matrix: np.ndarray
    ) -> float:
        """Compute overall consensus score.

        Args:
            proposals: List of proposals
            similarity_matrix: Pairwise similarity matrix

        Returns:
            Consensus score (average of all pairwise similarities)
        """
        n = len(proposals)

        if n == 0:
            return 0.0

        if n == 1:
            return 1.0

        # Average of all pairwise similarities (excluding diagonal)
        total = 0.0
        count = 0

        for i in range(n):
            for j in range(i + 1, n):
                total += similarity_matrix[i, j]
                count += 1

        return total / count if count > 0 else 0.0
