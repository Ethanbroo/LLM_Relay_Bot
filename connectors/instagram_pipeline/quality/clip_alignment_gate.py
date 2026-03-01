"""Tier 2 Quality Gate: CLIP Semantic Alignment.

Measures how well the generated image matches the prompt using CLIP embeddings.
This catches semantic misalignment (e.g., prompt says "beach" but image shows "forest").
"""

import logging
import time
from typing import Optional

from .models import QualityGateResult, GateDecision, GateTier

logger = logging.getLogger(__name__)


# CLIP alignment thresholds (cosine similarity, range [0, 1])
# These are empirically derived from CLIP ViT-L/14 on photorealistic images
CLIP_THRESHOLD_STRONG = 0.30        # Strong alignment (PASS)
CLIP_THRESHOLD_MARGINAL = 0.25      # Marginal alignment (review)
CLIP_THRESHOLD_FAIL = 0.25          # Below this = semantic mismatch (FAIL)


class CLIPAlignmentGate:
    """
    Tier 2: CLIP semantic alignment check.

    Uses OpenAI's CLIP model to compute similarity between:
    - Image embedding (what the model sees in the generated image)
    - Text embedding (what the prompt describes)

    High similarity = image matches prompt semantically.
    Low similarity = generation went off-track.

    Note: CLIP thresholds are MUCH lower than face embedding thresholds
    because CLIP measures semantic similarity, not identity similarity.
    A score of 0.30 for CLIP is considered strong alignment.
    """

    def __init__(
        self,
        strong_threshold: float = CLIP_THRESHOLD_STRONG,
        marginal_threshold: float = CLIP_THRESHOLD_MARGINAL,
        model_name: str = "ViT-L/14"
    ):
        """
        Initialize CLIP alignment gate.

        Args:
            strong_threshold: Similarity score for PASS (default: 0.30)
            marginal_threshold: Similarity score for MARGINAL (default: 0.25)
            model_name: CLIP model variant (default: ViT-L/14, best quality)
        """
        self.strong_threshold = strong_threshold
        self.marginal_threshold = marginal_threshold
        self.model_name = model_name

        # Lazy load CLIP model (heavy dependency)
        self._model = None
        self._preprocess = None

    def _load_model(self):
        """Lazy load CLIP model on first use."""
        if self._model is not None:
            return

        try:
            import clip
            import torch
        except ImportError:
            raise ImportError(
                "CLIP not installed. Install with: pip install git+https://github.com/openai/CLIP.git"
            )

        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading CLIP model %s on %s", self.model_name, device)

        self._model, self._preprocess = clip.load(self.model_name, device=device)
        self._device = device

    def evaluate(self, image_path: str, prompt: str) -> QualityGateResult:
        """
        Evaluate CLIP alignment between image and prompt.

        Args:
            image_path: Path to generated image
            prompt: Generation prompt used

        Returns:
            QualityGateResult with PASS/MARGINAL/FAIL decision
        """
        start_time = time.time()
        self._load_model()

        logger.info(
            "Running Tier 2 CLIP gate on %s (prompt: '%s')",
            Path(image_path).name if 'Path' in dir() else image_path,
            prompt[:50]
        )

        try:
            import clip
            import torch
            from PIL import Image
            from pathlib import Path

            # Load and preprocess image
            image = Image.open(image_path)
            image_input = self._preprocess(image).unsqueeze(0).to(self._device)

            # Tokenize text
            text_input = clip.tokenize([prompt]).to(self._device)

            # Compute embeddings
            with torch.no_grad():
                image_features = self._model.encode_image(image_input)
                text_features = self._model.encode_text(text_input)

                # Normalize features
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)

                # Compute cosine similarity
                similarity = (image_features @ text_features.T).item()

        except Exception as e:
            logger.error("CLIP alignment check failed: %s", e)
            return QualityGateResult(
                tier=GateTier.TIER_2_CLIP,
                decision=GateDecision.FAIL,
                score=0.0,
                threshold=self.strong_threshold,
                reason=f"CLIP evaluation failed: {e}",
                execution_time_s=time.time() - start_time,
            )

        execution_time = time.time() - start_time

        # Determine decision based on thresholds
        if similarity >= self.strong_threshold:
            decision = GateDecision.PASS
            reason = (
                f"Strong CLIP alignment (similarity: {similarity:.3f} >= {self.strong_threshold})"
            )
        elif similarity >= self.marginal_threshold:
            decision = GateDecision.MARGINAL
            reason = (
                f"Marginal CLIP alignment (similarity: {similarity:.3f} in range "
                f"[{self.marginal_threshold}, {self.strong_threshold})) - flag for review"
            )
        else:
            decision = GateDecision.FAIL
            reason = (
                f"Poor CLIP alignment (similarity: {similarity:.3f} < {self.marginal_threshold})"
            )

        logger.info(
            "Tier 2 CLIP gate: %s (similarity: %.3f, threshold: %.3f)",
            decision.value.upper(),
            similarity,
            self.strong_threshold
        )

        return QualityGateResult(
            tier=GateTier.TIER_2_CLIP,
            decision=decision,
            score=similarity,
            threshold=self.strong_threshold,
            reason=reason,
            details={
                "model": self.model_name,
                "prompt_length": len(prompt),
            },
            execution_time_s=execution_time,
        )

    def evaluate_batch(
        self,
        image_paths: list[str],
        prompts: list[str]
    ) -> list[QualityGateResult]:
        """
        Evaluate multiple images at once.

        More efficient than calling evaluate() in a loop because
        CLIP embeddings can be batched.

        Args:
            image_paths: List of image paths
            prompts: List of prompts (same order as images)

        Returns:
            List of QualityGateResults (same order as input)
        """
        if len(image_paths) != len(prompts):
            raise ValueError("image_paths and prompts must have same length")

        self._load_model()

        import clip
        import torch
        from PIL import Image

        results = []

        # Process in batches for efficiency
        batch_size = 8
        for i in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[i:i + batch_size]
            batch_prompts = prompts[i:i + batch_size]

            # Load images
            images = [self._preprocess(Image.open(p)).to(self._device) for p in batch_paths]
            image_batch = torch.stack(images)

            # Tokenize prompts
            text_batch = clip.tokenize(batch_prompts).to(self._device)

            # Compute embeddings
            with torch.no_grad():
                image_features = self._model.encode_image(image_batch)
                text_features = self._model.encode_text(text_batch)

                # Normalize
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)

                # Compute pairwise similarities
                similarities = (image_features * text_features).sum(dim=-1).cpu().numpy()

            # Create results
            for path, prompt, similarity in zip(batch_paths, batch_prompts, similarities):
                if similarity >= self.strong_threshold:
                    decision = GateDecision.PASS
                    reason = f"Strong CLIP alignment (similarity: {similarity:.3f})"
                elif similarity >= self.marginal_threshold:
                    decision = GateDecision.MARGINAL
                    reason = f"Marginal CLIP alignment (similarity: {similarity:.3f})"
                else:
                    decision = GateDecision.FAIL
                    reason = f"Poor CLIP alignment (similarity: {similarity:.3f})"

                results.append(QualityGateResult(
                    tier=GateTier.TIER_2_CLIP,
                    decision=decision,
                    score=float(similarity),
                    threshold=self.strong_threshold,
                    reason=reason,
                    details={"model": self.model_name},
                ))

        return results
