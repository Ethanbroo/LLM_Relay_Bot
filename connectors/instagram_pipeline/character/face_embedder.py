"""Face embedding extraction for identity consistency gating.

This module uses InsightFace (ArcFace embeddings) to extract 512-dimensional
face vectors from reference images. These vectors become the ground truth
for Tier 3 identity consistency checks during generation.

IMPORTANT: Requires insightface and onnxruntime packages.
Install with: pip install insightface onnxruntime
"""

import logging
from pathlib import Path
from typing import Optional
import numpy as np

from .models import CharacterProfile

logger = logging.getLogger(__name__)


# InsightFace model — "buffalo_l" is the best accuracy/speed tradeoff
EMBEDDING_MODEL = "buffalo_l"
EMBEDDING_DIM = 512                      # buffalo_l produces 512-dim ArcFace embeddings

# Identity similarity thresholds (cosine similarity, range [0, 1])
IDENTITY_THRESHOLD_STRONG = 0.65         # Strong match — definitely the same person
IDENTITY_THRESHOLD_MARGINAL = 0.50       # Marginal match — flag for human review
IDENTITY_THRESHOLD_FAIL = 0.50           # Below this = identity failure (reject)


class FaceEmbedder:
    """
    Extracts 512-dimensional ArcFace face embeddings from images.

    The key concept: every face generates a unique vector in 512-dimensional space.
    Faces of the same person cluster together; different people are far apart.
    We use cosine similarity to measure how close a generated image is to the
    character's reference embedding cluster.

    Uses InsightFace's buffalo_l model which balances accuracy and speed.
    """

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        """
        Initialize face embedder.

        Args:
            model_name: InsightFace model name (default: "buffalo_l")

        Raises:
            ImportError: If insightface not installed
            RuntimeError: If model download/initialization fails
        """
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            raise ImportError(
                "insightface not installed. Install with: pip install insightface onnxruntime"
            )

        self.model_name = model_name

        logger.info("Initializing InsightFace model: %s", model_name)
        try:
            self.app = FaceAnalysis(name=model_name, providers=["CPUExecutionProvider"])
            self.app.prepare(ctx_id=0, det_size=(640, 640))
            logger.info("InsightFace model loaded successfully")
        except Exception as e:
            logger.error("Failed to initialize InsightFace: %s", e)
            raise RuntimeError(f"InsightFace initialization failed: {e}")

    def extract_embedding(self, image_path: str) -> Optional[np.ndarray]:
        """
        Extract face embedding from a single image.

        Returns a normalized 512-dim embedding vector, or None if no face detected.

        Args:
            image_path: Path to image file

        Returns:
            Normalized 512-dim numpy array, or None if no face found
        """
        try:
            import cv2
        except ImportError:
            raise ImportError("opencv-python required. Install with: pip install opencv-python")

        img = cv2.imread(image_path)
        if img is None:
            logger.warning("Failed to load image: %s", image_path)
            return None

        faces = self.app.get(img)
        if not faces:
            logger.warning("No face detected in: %s", image_path)
            return None

        if len(faces) > 1:
            logger.warning(
                "Multiple faces detected in %s. Using largest face. "
                "Reference images should contain only one person.",
                image_path
            )

        # Use the largest face if multiple detected (shouldn't happen with reference images)
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

        # L2-normalize to unit sphere so cosine_similarity = dot product
        embedding = face.embedding.astype(np.float32)
        norm = np.linalg.norm(embedding)

        if norm == 0:
            logger.warning("Zero-norm embedding for %s", image_path)
            return None

        return embedding / norm

    def build_reference_embeddings(
        self,
        character: CharacterProfile,
        reference_image_dir: str,
    ) -> str:
        """
        Process all reference images, extract embeddings, save to .npy file.

        The saved array shape is (N, 512) where N = number of reference images.
        During gating, we compare a generated image's embedding against
        the MEAN of this array using cosine similarity.

        Args:
            character: Character profile
            reference_image_dir: Directory containing reference PNG images

        Returns:
            Path to saved embeddings file (reference_embeddings.npy)

        Raises:
            ValueError: If fewer than 10 valid embeddings extracted
            FileNotFoundError: If reference directory doesn't exist
        """
        ref_dir = Path(reference_image_dir)
        if not ref_dir.exists():
            raise FileNotFoundError(f"Reference image directory not found: {reference_image_dir}")

        embeddings = []
        failed = []

        logger.info("Extracting face embeddings from reference images in %s", ref_dir)

        for img_path in sorted(ref_dir.glob("*.png")):
            logger.debug("Processing %s", img_path.name)
            emb = self.extract_embedding(str(img_path))
            if emb is not None:
                embeddings.append(emb)
            else:
                failed.append(img_path.name)

        if failed:
            logger.warning(
                "No face detected in %d reference images: %s",
                len(failed),
                failed
            )

        if len(embeddings) < 10:
            raise ValueError(
                f"Only {len(embeddings)} embeddings extracted. "
                "Need at least 10 for a reliable reference cluster. "
                "Check your reference images for face visibility and quality. "
                f"Failed images: {failed}"
            )

        embedding_array = np.array(embeddings, dtype=np.float32)

        # Save to character's data directory
        output_path = Path(f"data/characters/{character.character_id}/face_embeddings")
        output_path.mkdir(parents=True, exist_ok=True)
        save_path = output_path / "reference_embeddings.npy"
        np.save(str(save_path), embedding_array)

        logger.info(
            "Saved %d reference embeddings to %s (shape: %s)",
            len(embeddings),
            save_path,
            embedding_array.shape
        )

        return str(save_path)

    def compute_identity_similarity(
        self,
        generated_image_path: str,
        reference_embedding_path: str,
    ) -> float:
        """
        Compute similarity score between generated image and character reference.

        Returns a similarity score in [0, 1] representing how closely
        the generated image matches the character's reference identity.

        Interpretation:
        - Score >= 0.65 → strong match (definitely the same person)
        - Score 0.50–0.64 → marginal match (flag for review)
        - Score < 0.50 → identity failure (reject)

        We compare against the MEAN of all reference embeddings (centroid of
        the identity cluster), which is more robust to pose variation than
        comparing against individual reference images.

        Args:
            generated_image_path: Path to generated image
            reference_embedding_path: Path to reference_embeddings.npy

        Returns:
            Cosine similarity score [0, 1], or 0.0 if no face detected
        """
        # Extract embedding from generated image
        gen_embedding = self.extract_embedding(generated_image_path)
        if gen_embedding is None:
            logger.warning(
                "No face detected in generated image: %s (auto-reject)",
                generated_image_path
            )
            return 0.0  # No face = automatic identity failure

        # Load reference embeddings
        try:
            reference_embeddings = np.load(reference_embedding_path)
        except Exception as e:
            logger.error("Failed to load reference embeddings from %s: %s", reference_embedding_path, e)
            raise

        # Compute mean reference embedding (centroid of identity cluster)
        ref_mean = reference_embeddings.mean(axis=0)
        ref_mean = ref_mean / np.linalg.norm(ref_mean)  # Re-normalize after averaging

        # Cosine similarity = dot product of unit vectors
        similarity = float(np.dot(gen_embedding, ref_mean))

        # Clamp to [0, 1] range (numerical errors can cause slight overflow)
        similarity = max(0.0, min(1.0, similarity))

        logger.debug(
            "Identity similarity: %.3f (image: %s)",
            similarity,
            Path(generated_image_path).name
        )

        return similarity

    def batch_compute_similarities(
        self,
        generated_image_paths: list[str],
        reference_embedding_path: str,
    ) -> list[float]:
        """
        Compute identity similarities for multiple generated images at once.

        More efficient than calling compute_identity_similarity() in a loop
        because reference embeddings are loaded only once.

        Args:
            generated_image_paths: List of paths to generated images
            reference_embedding_path: Path to reference_embeddings.npy

        Returns:
            List of similarity scores (same order as input paths)
        """
        # Load reference embeddings once
        reference_embeddings = np.load(reference_embedding_path)
        ref_mean = reference_embeddings.mean(axis=0)
        ref_mean = ref_mean / np.linalg.norm(ref_mean)

        similarities = []
        for img_path in generated_image_paths:
            gen_embedding = self.extract_embedding(img_path)
            if gen_embedding is None:
                similarities.append(0.0)
            else:
                similarity = float(np.dot(gen_embedding, ref_mean))
                similarity = max(0.0, min(1.0, similarity))
                similarities.append(similarity)

        return similarities

    def get_embedding_stats(self, embedding_path: str) -> dict:
        """
        Get statistics about a reference embedding set.

        Useful for debugging and quality checks.

        Args:
            embedding_path: Path to reference_embeddings.npy

        Returns:
            Dict with stats: count, mean_norm, std_norm, pairwise_similarity_avg
        """
        embeddings = np.load(embedding_path)
        n = len(embeddings)

        # Compute pairwise similarities (measure cluster tightness)
        pairwise_sims = []
        for i in range(n):
            for j in range(i + 1, n):
                sim = float(np.dot(embeddings[i], embeddings[j]))
                pairwise_sims.append(sim)

        return {
            "count": n,
            "embedding_dim": embeddings.shape[1],
            "mean_norm": float(np.linalg.norm(embeddings, axis=1).mean()),
            "std_norm": float(np.linalg.norm(embeddings, axis=1).std()),
            "pairwise_similarity_avg": float(np.mean(pairwise_sims)) if pairwise_sims else 0.0,
            "pairwise_similarity_min": float(np.min(pairwise_sims)) if pairwise_sims else 0.0,
            "pairwise_similarity_max": float(np.max(pairwise_sims)) if pairwise_sims else 0.0,
        }
