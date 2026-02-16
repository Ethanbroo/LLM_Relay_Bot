"""Test Phase 6 scoring determinism.

Phase 6 Invariant: Consensus is numeric and deterministic.
Similarity scores must be reproducible across runs.
"""

import pytest
import numpy as np
from orchestration.scoring import SimilarityScorer


class TestScoringDeterminism:
    """Test deterministic similarity scoring."""

    def test_same_text_produces_same_embedding(self):
        """Test that same text produces identical embedding."""
        scorer = SimilarityScorer(similarity_rounding=3)
        text = "Implement input validation at API boundary"

        embedding1 = scorer.compute_embedding(text)
        embedding2 = scorer.compute_embedding(text)

        assert np.array_equal(embedding1, embedding2), "Embeddings must be deterministic"

    def test_same_texts_produce_same_similarity(self):
        """Test that same pair of texts produces same similarity score."""
        scorer = SimilarityScorer(similarity_rounding=3)
        text1 = "Add authentication middleware"
        text2 = "Implement auth middleware layer"

        similarity1 = scorer.compute_similarity(
            scorer.compute_embedding(text1),
            scorer.compute_embedding(text2)
        )
        similarity2 = scorer.compute_similarity(
            scorer.compute_embedding(text1),
            scorer.compute_embedding(text2)
        )

        assert similarity1 == similarity2, "Similarity must be deterministic"

    def test_similarity_rounding_enforced(self):
        """Test that similarity scores are rounded to specified precision."""
        scorer = SimilarityScorer(similarity_rounding=3)
        text1 = "Proposal A"
        text2 = "Proposal B"

        embedding1 = scorer.compute_embedding(text1)
        embedding2 = scorer.compute_embedding(text2)
        similarity = scorer.compute_similarity(embedding1, embedding2)

        # Check that similarity has at most 3 decimal places
        similarity_str = f"{similarity:.10f}"
        decimal_part = similarity_str.split('.')[1]

        # Count significant digits (ignore trailing zeros)
        significant = decimal_part.rstrip('0')
        assert len(significant) <= 3, f"Similarity must be rounded to 3 decimals, got {similarity}"

    def test_pairwise_similarities_deterministic(self):
        """Test that pairwise similarity matrix is deterministic."""
        scorer = SimilarityScorer(similarity_rounding=3)
        proposals = [
            "Add input validation",
            "Implement rate limiting",
            "Enable audit logging"
        ]

        matrix1 = scorer.compute_pairwise_similarities(proposals)
        matrix2 = scorer.compute_pairwise_similarities(proposals)

        assert np.array_equal(matrix1, matrix2), "Similarity matrix must be deterministic"

    def test_similarity_matrix_is_symmetric(self):
        """Test that similarity matrix is symmetric."""
        scorer = SimilarityScorer(similarity_rounding=3)
        proposals = [
            "Proposal A",
            "Proposal B",
            "Proposal C"
        ]

        matrix = scorer.compute_pairwise_similarities(proposals)

        # Check symmetry
        for i in range(len(proposals)):
            for j in range(len(proposals)):
                assert matrix[i, j] == matrix[j, i], f"Matrix must be symmetric at ({i},{j})"

    def test_similarity_matrix_diagonal_is_one(self):
        """Test that diagonal elements are 1.0 (self-similarity)."""
        scorer = SimilarityScorer(similarity_rounding=3)
        proposals = [
            "Proposal X",
            "Proposal Y"
        ]

        matrix = scorer.compute_pairwise_similarities(proposals)

        # Diagonal should be 1.0 (text is identical to itself)
        for i in range(len(proposals)):
            assert matrix[i, i] == 1.0, f"Self-similarity must be 1.0 at index {i}"

    def test_similarity_bounds(self):
        """Test that similarity scores are in valid range [-1.0, 1.0]."""
        scorer = SimilarityScorer(similarity_rounding=3)
        proposals = [
            "Completely different text A",
            "Unrelated content B",
            "Another distinct proposal C"
        ]

        matrix = scorer.compute_pairwise_similarities(proposals)

        # All similarities must be in [-1.0, 1.0]
        assert np.all(matrix >= -1.0), "Similarity cannot be less than -1.0"
        assert np.all(matrix <= 1.0), "Similarity cannot be greater than 1.0"

    def test_different_rounding_produces_different_precision(self):
        """Test that different rounding values affect precision."""
        text1 = "Proposal alpha"
        text2 = "Proposal beta"

        scorer_3 = SimilarityScorer(similarity_rounding=3)
        scorer_5 = SimilarityScorer(similarity_rounding=5)

        embedding1 = scorer_3.compute_embedding(text1)
        embedding2 = scorer_3.compute_embedding(text2)

        # Same embeddings for both scorers (stub uses hash)
        similarity_3 = scorer_3.compute_similarity(embedding1, embedding2)
        similarity_5 = scorer_5.compute_similarity(embedding1, embedding2)

        # Round to 3 decimals manually
        similarity_5_rounded = round(similarity_5, 3)

        # If original value had more precision, rounding should match
        assert abs(similarity_3 - similarity_5_rounded) < 1e-10

    def test_empty_proposal_list_produces_empty_matrix(self):
        """Test that empty proposal list produces empty matrix."""
        scorer = SimilarityScorer(similarity_rounding=3)
        proposals = []

        matrix = scorer.compute_pairwise_similarities(proposals)

        assert matrix.shape == (0, 0), "Empty proposals should produce 0x0 matrix"

    def test_single_proposal_produces_1x1_matrix(self):
        """Test that single proposal produces 1x1 matrix with value 1.0."""
        scorer = SimilarityScorer(similarity_rounding=3)
        proposals = ["Single proposal"]

        matrix = scorer.compute_pairwise_similarities(proposals)

        assert matrix.shape == (1, 1), "Single proposal should produce 1x1 matrix"
        assert matrix[0, 0] == 1.0, "Self-similarity must be 1.0"

    def test_embedding_dimension_is_fixed(self):
        """Test that embeddings have fixed dimension (384 for all-mpnet-base-v2)."""
        scorer = SimilarityScorer(similarity_rounding=3)

        embedding1 = scorer.compute_embedding("Text A")
        embedding2 = scorer.compute_embedding("Different text B")

        # all-mpnet-base-v2 produces 384-dim embeddings
        assert embedding1.shape == (384,), "Embedding must be 384-dimensional"
        assert embedding2.shape == (384,), "Embedding must be 384-dimensional"

    def test_normalized_embeddings(self):
        """Test that embeddings are L2-normalized (for cosine similarity)."""
        scorer = SimilarityScorer(similarity_rounding=3)
        text = "Test proposal text"

        embedding = scorer.compute_embedding(text)
        norm = np.linalg.norm(embedding)

        # Embedding should be normalized (norm ≈ 1.0)
        assert abs(norm - 1.0) < 1e-6, f"Embedding must be normalized, got norm={norm}"

    def test_identical_texts_have_similarity_one(self):
        """Test that identical texts have similarity 1.0."""
        scorer = SimilarityScorer(similarity_rounding=3)
        text = "Exact same proposal text"

        embedding = scorer.compute_embedding(text)
        similarity = scorer.compute_similarity(embedding, embedding)

        assert similarity == 1.0, "Identical texts must have similarity 1.0"

    def test_stub_determinism_based_on_hash(self):
        """Test that stub implementation uses deterministic hash-based embeddings."""
        scorer = SimilarityScorer(similarity_rounding=3)

        # Same text should produce same embedding (hash-based)
        text = "Deterministic test"
        embedding1 = scorer.compute_embedding(text)
        embedding2 = scorer.compute_embedding(text)

        assert np.array_equal(embedding1, embedding2)

        # Different text should produce different embedding
        text2 = "Different test"
        embedding3 = scorer.compute_embedding(text2)

        assert not np.array_equal(embedding1, embedding3)
