"""Similarity scoring for Phase 6.

Phase 6 Invariant: Scoring is deterministic and reproducible.
"""

import numpy as np
from typing import List, Optional
import hashlib


class SimilarityScorer:
    """Similarity scorer using SBERT embeddings.

    Phase 6 Invariants:
    - Fixed embedding model (all-mpnet-base-v2)
    - CPU inference only
    - No temperature
    - No dropout
    - Deterministic scoring
    """

    def __init__(self, similarity_rounding: int = 3):
        """Initialize similarity scorer.

        Args:
            similarity_rounding: Number of decimal places for rounding
        """
        self.similarity_rounding = similarity_rounding
        self._model = None  # Lazy loaded

    def _load_model(self):
        """Lazy load SBERT model.

        TODO: Install sentence-transformers:
        pip install sentence-transformers

        Then uncomment:
        ```python
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer('all-mpnet-base-v2')
        self._model.eval()  # Evaluation mode (no dropout)
        ```
        """
        if self._model is not None:
            return

        # STUB: Use deterministic hash-based similarity for testing
        # TODO: Replace with real SBERT model loading (see above)
        self._model = "stub"

    def compute_embedding(self, text: str) -> np.ndarray:
        """Compute embedding for text.

        Phase 6 Invariant: Embedding is deterministic.

        Args:
            text: Text to embed

        Returns:
            Embedding vector (normalized)
        """
        self._load_model()

        if self._model == "stub":
            # STUB: Generate deterministic embedding from hash
            # TODO: Replace with: return self._model.encode(text, show_progress_bar=False)
            text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
            # Use first 384 bytes of hash to create 384-dim vector (matches all-mpnet-base-v2)
            embedding = np.array([int(text_hash[i:i+2], 16) for i in range(0, min(len(text_hash), 384*2), 2)])
            # Pad if needed
            if len(embedding) < 384:
                embedding = np.pad(embedding, (0, 384 - len(embedding)))
            # Normalize
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding.astype(np.float32)

        # Real implementation (when uncommented):
        # return self._model.encode(text, show_progress_bar=False)

    def compute_similarity(self, embedding1: np.ndarray, embedding2: np.ndarray) -> float:
        """Compute cosine similarity between embeddings.

        Phase 6 Invariant: Similarity is rounded for determinism.

        Args:
            embedding1: First embedding
            embedding2: Second embedding

        Returns:
            Rounded cosine similarity
        """
        # Cosine similarity
        similarity = float(np.dot(embedding1, embedding2))

        # Round for determinism
        rounded_similarity = round(similarity, self.similarity_rounding)

        return rounded_similarity

    def compute_pairwise_similarities(
        self,
        proposals: List[str]
    ) -> np.ndarray:
        """Compute pairwise similarities between proposals.

        Args:
            proposals: List of proposal texts

        Returns:
            Similarity matrix (N x N)
        """
        n = len(proposals)

        # Compute embeddings
        embeddings = [self.compute_embedding(p) for p in proposals]

        # Compute pairwise similarities
        similarity_matrix = np.zeros((n, n))

        for i in range(n):
            for j in range(n):
                if i == j:
                    similarity_matrix[i, j] = 1.0
                elif i < j:
                    sim = self.compute_similarity(embeddings[i], embeddings[j])
                    similarity_matrix[i, j] = sim
                    similarity_matrix[j, i] = sim  # Symmetric

        return similarity_matrix
