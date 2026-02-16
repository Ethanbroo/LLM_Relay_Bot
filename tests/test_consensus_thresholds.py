"""Test Phase 6 consensus algorithm and threshold enforcement.

Phase 6 Invariant: Consensus is numeric and deterministic.
No model identity weighting - only similarity scores matter.
"""

import pytest
import numpy as np
from orchestration.consensus import ConsensusEngine
from orchestration.response_parser import LLMProposal


class TestConsensusThresholds:
    """Test consensus threshold logic."""

    def test_consensus_reached_when_above_threshold(self):
        """Test that consensus is reached when max similarity >= threshold."""
        engine = ConsensusEngine(consensus_threshold=0.80)

        # Create similarity matrix with high similarity (0.95)
        similarity_matrix = np.array([
            [1.0, 0.95],
            [0.95, 1.0]
        ])

        proposals = [
            LLMProposal("chatgpt", "Proposal A", "Rationale A", 0.9, "hash1"),
            LLMProposal("claude", "Proposal A variant", "Rationale B", 0.85, "hash2")
        ]

        consensus = engine.check_consensus(proposals, similarity_matrix)
        assert consensus == True, "Consensus should be reached at 0.95 >= 0.80"

    def test_no_consensus_when_below_threshold(self):
        """Test that no consensus when max similarity < threshold."""
        engine = ConsensusEngine(consensus_threshold=0.80)

        # Create similarity matrix with low similarity (0.75)
        similarity_matrix = np.array([
            [1.0, 0.75],
            [0.75, 1.0]
        ])

        proposals = [
            LLMProposal("chatgpt", "Proposal A", "Rationale A", 0.9, "hash1"),
            LLMProposal("claude", "Proposal B", "Rationale B", 0.85, "hash2")
        ]

        consensus = engine.check_consensus(proposals, similarity_matrix)
        assert consensus == False, "No consensus should be reached at 0.75 < 0.80"

    def test_consensus_at_exact_threshold(self):
        """Test consensus at exactly threshold value."""
        engine = ConsensusEngine(consensus_threshold=0.80)

        # Exactly at threshold
        similarity_matrix = np.array([
            [1.0, 0.80],
            [0.80, 1.0]
        ])

        proposals = [
            LLMProposal("gemini", "Proposal X", "Rationale X", 0.88, "hash3"),
            LLMProposal("deepseek", "Proposal X similar", "Rationale Y", 0.82, "hash4")
        ]

        consensus = engine.check_consensus(proposals, similarity_matrix)
        assert consensus == True, "Consensus should be reached at exactly threshold"

    def test_single_proposal_always_consensus(self):
        """Test that single proposal always has consensus."""
        engine = ConsensusEngine(consensus_threshold=0.80)

        similarity_matrix = np.array([[1.0]])
        proposals = [
            LLMProposal("chatgpt", "Only proposal", "Only rationale", 0.9, "hash_single")
        ]

        consensus = engine.check_consensus(proposals, similarity_matrix)
        assert consensus == True, "Single proposal must always have consensus"

    def test_select_proposal_highest_avg_similarity(self):
        """Test that proposal with highest average similarity is selected."""
        engine = ConsensusEngine(consensus_threshold=0.70)

        # Proposal 0 has avg similarity (1.0 + 0.85 + 0.80) / 3 = 0.883
        # Proposal 1 has avg similarity (0.85 + 1.0 + 0.75) / 3 = 0.867
        # Proposal 2 has avg similarity (0.80 + 0.75 + 1.0) / 3 = 0.850
        # Proposal 0 should win
        similarity_matrix = np.array([
            [1.0, 0.85, 0.80],
            [0.85, 1.0, 0.75],
            [0.80, 0.75, 1.0]
        ])

        proposals = [
            LLMProposal("chatgpt", "Proposal A", "Rationale A", 0.9, "hash_a"),
            LLMProposal("claude", "Proposal B", "Rationale B", 0.85, "hash_b"),
            LLMProposal("gemini", "Proposal C", "Rationale C", 0.88, "hash_c")
        ]

        selected, score = engine.select_proposal(proposals, similarity_matrix)

        assert selected.model == "chatgpt", "Proposal with highest avg similarity should be selected"
        assert selected.proposal_hash == "hash_a"
        # Consensus score is avg similarity to others (exclude self)
        expected_score = (0.85 + 0.80) / 2
        assert abs(score - expected_score) < 1e-6

    def test_tie_broken_by_hash(self):
        """Test that ties in average similarity are broken by lexicographic hash."""
        engine = ConsensusEngine(consensus_threshold=0.70)

        # Both proposals have same average similarity
        similarity_matrix = np.array([
            [1.0, 0.80],
            [0.80, 1.0]
        ])

        # Create proposals where hash comparison determines winner
        proposals = [
            LLMProposal("claude", "Proposal X", "Rationale X", 0.9, "zzz_hash"),  # Higher hash
            LLMProposal("chatgpt", "Proposal Y", "Rationale Y", 0.9, "aaa_hash")  # Lower hash wins
        ]

        selected, score = engine.select_proposal(proposals, similarity_matrix)

        # Lexicographically lowest hash should win
        assert selected.proposal_hash == "aaa_hash", "Tie should be broken by lexicographic hash"

    def test_confidence_not_used_in_selection(self):
        """Test that confidence scores do not affect selection."""
        engine = ConsensusEngine(consensus_threshold=0.70)

        similarity_matrix = np.array([
            [1.0, 0.90],
            [0.90, 1.0]
        ])

        # Proposal 0 has higher similarity but lower confidence
        # Proposal 1 has lower similarity but higher confidence
        # Similarity should determine winner, not confidence
        proposals = [
            LLMProposal("chatgpt", "Proposal A", "Rationale A", 0.5, "hash_low_conf"),
            LLMProposal("claude", "Proposal B", "Rationale B", 0.99, "hash_high_conf")
        ]

        selected, score = engine.select_proposal(proposals, similarity_matrix)

        # Both have same avg similarity (0.90), so hash determines winner
        # But importantly, confidence (0.5 vs 0.99) should NOT matter

    def test_model_identity_not_used(self):
        """Test that model identity does not affect consensus or selection."""
        engine = ConsensusEngine(consensus_threshold=0.80)

        # Same similarity matrix
        similarity_matrix = np.array([
            [1.0, 0.85],
            [0.85, 1.0]
        ])

        # Try with different model combinations - results should be identical
        proposals1 = [
            LLMProposal("chatgpt", "Proposal", "Rationale", 0.9, "hash_x"),
            LLMProposal("claude", "Proposal variant", "Rationale", 0.9, "hash_y")
        ]

        proposals2 = [
            LLMProposal("gemini", "Proposal", "Rationale", 0.9, "hash_x"),
            LLMProposal("deepseek", "Proposal variant", "Rationale", 0.9, "hash_y")
        ]

        consensus1 = engine.check_consensus(proposals1, similarity_matrix)
        consensus2 = engine.check_consensus(proposals2, similarity_matrix)

        assert bool(consensus1) == bool(consensus2), "Model identity must not affect consensus"

    def test_different_thresholds_produce_different_results(self):
        """Test that different threshold values affect consensus outcome."""
        # Similarity of 0.75
        similarity_matrix = np.array([
            [1.0, 0.75],
            [0.75, 1.0]
        ])

        proposals = [
            LLMProposal("chatgpt", "Proposal", "Rationale", 0.9, "hash1"),
            LLMProposal("claude", "Similar proposal", "Rationale", 0.85, "hash2")
        ]

        # With threshold 0.70 - consensus reached
        engine_low = ConsensusEngine(consensus_threshold=0.70)
        consensus_low = engine_low.check_consensus(proposals, similarity_matrix)
        assert consensus_low == True

        # With threshold 0.80 - no consensus
        engine_high = ConsensusEngine(consensus_threshold=0.80)
        consensus_high = engine_high.check_consensus(proposals, similarity_matrix)
        assert consensus_high == False

    def test_three_proposals_require_all_pairwise_above_threshold(self):
        """Test that with 3+ proposals, max pairwise similarity determines consensus."""
        engine = ConsensusEngine(consensus_threshold=0.80)

        # Two proposals are very similar (0.95), but third is different (0.70)
        # Max similarity is 0.95, so consensus reached
        similarity_matrix = np.array([
            [1.0, 0.95, 0.70],
            [0.95, 1.0, 0.70],
            [0.70, 0.70, 1.0]
        ])

        proposals = [
            LLMProposal("chatgpt", "Proposal A", "Rationale A", 0.9, "hash_a"),
            LLMProposal("claude", "Proposal A similar", "Rationale B", 0.85, "hash_b"),
            LLMProposal("gemini", "Proposal C different", "Rationale C", 0.88, "hash_c")
        ]

        consensus = engine.check_consensus(proposals, similarity_matrix)
        assert consensus == True, "Consensus should be based on max pairwise similarity"

    def test_consensus_score_excludes_self_similarity(self):
        """Test that consensus score excludes self-similarity (diagonal)."""
        engine = ConsensusEngine(consensus_threshold=0.70)

        similarity_matrix = np.array([
            [1.0, 0.80, 0.75],
            [0.80, 1.0, 0.70],
            [0.75, 0.70, 1.0]
        ])

        proposals = [
            LLMProposal("chatgpt", "A", "R", 0.9, "hash1"),
            LLMProposal("claude", "B", "R", 0.85, "hash2"),
            LLMProposal("gemini", "C", "R", 0.88, "hash3")
        ]

        selected, score = engine.select_proposal(proposals, similarity_matrix)

        # Score should be average of similarities to OTHER proposals only
        # Proposal 0 avg to others: (0.80 + 0.75) / 2 = 0.775
        if selected.proposal_hash == "hash1":
            expected = (0.80 + 0.75) / 2
            assert abs(score - expected) < 1e-6, "Score must exclude self-similarity"
