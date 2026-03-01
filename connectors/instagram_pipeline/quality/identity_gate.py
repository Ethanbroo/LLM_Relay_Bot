"""Tier 3 Quality Gate: Identity Consistency Check.

This is the MOST CRITICAL quality gate for the entire pipeline.
It ensures generated images actually look like the character by comparing
face embeddings against the reference cluster.

Without this gate, you have no objective measurement of identity consistency.
"""

import logging
import time
from pathlib import Path

from .models import QualityGateResult, GateDecision, GateTier
from ..character.face_embedder import FaceEmbedder
from ..character.models import CharacterProfile

logger = logging.getLogger(__name__)


# Identity similarity thresholds (cosine similarity, range [0, 1])
# These are derived from InsightFace ArcFace literature and empirical testing
IDENTITY_THRESHOLD_STRONG = 0.65         # Strong match — definitely the same person
IDENTITY_THRESHOLD_MARGINAL = 0.50       # Marginal match — flag for human review
IDENTITY_THRESHOLD_FAIL = 0.50           # Below this = identity failure (reject)


class IdentityConsistencyGate:
    """
    Tier 3: Face embedding identity consistency check.

    Compares generated image's face embedding against character's
    reference embedding cluster using cosine similarity.

    This is the KEY differentiator between "AI-generated person" and
    "consistent AI character." Every other pipeline can generate faces.
    This gate ensures they're the SAME face every time.
    """

    def __init__(
        self,
        character: CharacterProfile,
        embedder: FaceEmbedder | None = None,
        strong_threshold: float = IDENTITY_THRESHOLD_STRONG,
        marginal_threshold: float = IDENTITY_THRESHOLD_MARGINAL
    ):
        """
        Initialize identity gate.

        Args:
            character: Character profile with face_embedding_path
            embedder: Optional FaceEmbedder instance (creates new if not provided)
            strong_threshold: Similarity score for PASS (default: 0.65)
            marginal_threshold: Similarity score for MARGINAL (default: 0.50)
        """
        self.character = character
        self.embedder = embedder or FaceEmbedder()
        self.strong_threshold = strong_threshold
        self.marginal_threshold = marginal_threshold

        # Validate character has face embeddings
        if not character.face_embedding_path:
            raise ValueError(
                f"Character '{character.character_id}' has no face_embedding_path. "
                "Run Stage 0 face embedding extraction first."
            )

        if not Path(character.face_embedding_path).exists():
            raise FileNotFoundError(
                f"Face embeddings not found at {character.face_embedding_path}"
            )

    def evaluate(self, image_path: str) -> QualityGateResult:
        """
        Evaluate identity consistency of generated image.

        Args:
            image_path: Path to generated image

        Returns:
            QualityGateResult with PASS/MARGINAL/FAIL decision
        """
        start_time = time.time()

        logger.info(
            "Running Tier 3 identity gate on %s (character: %s)",
            Path(image_path).name,
            self.character.character_id
        )

        # Compute similarity score
        try:
            similarity = self.embedder.compute_identity_similarity(
                generated_image_path=image_path,
                reference_embedding_path=self.character.face_embedding_path
            )
        except Exception as e:
            logger.error("Identity gate failed with error: %s", e)
            return QualityGateResult(
                tier=GateTier.TIER_3_IDENTITY,
                decision=GateDecision.FAIL,
                score=0.0,
                threshold=self.strong_threshold,
                reason=f"Identity check failed: {e}",
                execution_time_s=time.time() - start_time,
            )

        execution_time = time.time() - start_time

        # Determine decision based on thresholds
        if similarity >= self.strong_threshold:
            decision = GateDecision.PASS
            reason = (
                f"Strong identity match (similarity: {similarity:.3f} >= {self.strong_threshold})"
            )
        elif similarity >= self.marginal_threshold:
            decision = GateDecision.MARGINAL
            reason = (
                f"Marginal identity match (similarity: {similarity:.3f} in range "
                f"[{self.marginal_threshold}, {self.strong_threshold})) - flag for review"
            )
        else:
            decision = GateDecision.FAIL
            reason = (
                f"Identity mismatch (similarity: {similarity:.3f} < {self.marginal_threshold})"
            )

        logger.info(
            "Tier 3 identity gate: %s (similarity: %.3f, threshold: %.3f)",
            decision.value.upper(),
            similarity,
            self.strong_threshold
        )

        return QualityGateResult(
            tier=GateTier.TIER_3_IDENTITY,
            decision=decision,
            score=similarity,
            threshold=self.strong_threshold,
            reason=reason,
            details={
                "character_id": self.character.character_id,
                "embedding_model": self.character.embedding_model,
                "reference_embedding_path": self.character.face_embedding_path,
            },
            execution_time_s=execution_time,
        )

    def evaluate_batch(self, image_paths: list[str]) -> list[QualityGateResult]:
        """
        Evaluate multiple images at once.

        More efficient than calling evaluate() in a loop because
        reference embeddings are loaded only once.

        Args:
            image_paths: List of paths to generated images

        Returns:
            List of QualityGateResults (same order as input)
        """
        similarities = self.embedder.batch_compute_similarities(
            generated_image_paths=image_paths,
            reference_embedding_path=self.character.face_embedding_path
        )

        results = []
        for img_path, similarity in zip(image_paths, similarities):
            # Reuse same logic as evaluate()
            if similarity >= self.strong_threshold:
                decision = GateDecision.PASS
                reason = f"Strong identity match (similarity: {similarity:.3f})"
            elif similarity >= self.marginal_threshold:
                decision = GateDecision.MARGINAL
                reason = f"Marginal identity match (similarity: {similarity:.3f})"
            else:
                decision = GateDecision.FAIL
                reason = f"Identity mismatch (similarity: {similarity:.3f})"

            results.append(QualityGateResult(
                tier=GateTier.TIER_3_IDENTITY,
                decision=decision,
                score=similarity,
                threshold=self.strong_threshold,
                reason=reason,
                details={"character_id": self.character.character_id},
            ))

        return results

    def get_rejection_stats(self, results: list[QualityGateResult]) -> dict:
        """
        Analyze rejection statistics across a batch of results.

        Useful for monitoring identity drift over time.

        Args:
            results: List of gate results

        Returns:
            Dict with rejection rate, average similarity, etc.
        """
        total = len(results)
        if total == 0:
            return {}

        passed = sum(1 for r in results if r.decision == GateDecision.PASS)
        marginal = sum(1 for r in results if r.decision == GateDecision.MARGINAL)
        failed = sum(1 for r in results if r.decision == GateDecision.FAIL)

        scores = [r.score for r in results if r.score is not None]
        avg_similarity = sum(scores) / len(scores) if scores else 0.0
        min_similarity = min(scores) if scores else 0.0
        max_similarity = max(scores) if scores else 0.0

        return {
            "total_evaluated": total,
            "passed": passed,
            "marginal": marginal,
            "failed": failed,
            "pass_rate": passed / total,
            "fail_rate": failed / total,
            "marginal_rate": marginal / total,
            "avg_similarity": avg_similarity,
            "min_similarity": min_similarity,
            "max_similarity": max_similarity,
        }
