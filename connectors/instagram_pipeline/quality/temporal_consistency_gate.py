# quality/temporal_consistency_gate.py

from .frame_extractor import FrameExtractor
from ..character.face_embedder import FaceEmbedder
import numpy as np


class TemporalConsistencyGate:
    """Verify character identity doesn't drift across video frames.

    Samples frames at regular intervals, extracts face embeddings,
    and checks cosine similarity against the character reference.

    Tier: 3.5 (runs after identity_gate equivalent, before LLM vision)
    Cost: Free (local InsightFace inference)
    """

    def __init__(
        self,
        face_embedder: FaceEmbedder,
        reference_embedding: np.ndarray,
        min_threshold: float = 0.65,     # Minimum acceptable for ANY frame
        avg_threshold: float = 0.70,     # Average across all frames
        sample_interval: int = 15,        # Every 15 frames (~2 samples/sec at 30fps)
        min_face_frames_ratio: float = 0.5,  # At least 50% of frames must have a face
    ):
        self.embedder = face_embedder
        self.reference = reference_embedding
        self.min_threshold = min_threshold
        self.avg_threshold = avg_threshold
        self.sample_interval = sample_interval
        self.min_face_ratio = min_face_frames_ratio
        self.extractor = FrameExtractor()

    async def evaluate(self, video_path: str) -> "GateResult":
        frames = self.extractor.extract_at_intervals(
            video_path, interval_frames=self.sample_interval
        )

        if not frames:
            return GateResult(
                passed=False,
                gate_name="temporal_consistency",
                failure_reason="No frames extracted from video",
            )

        scores = []
        faces_found = 0

        for frame_path in frames:
            embedding = self.embedder.extract(frame_path)
            if embedding is not None:
                faces_found += 1
                similarity = float(np.dot(embedding, self.reference) /
                                   (np.linalg.norm(embedding) * np.linalg.norm(self.reference)))
                scores.append(similarity)

        # Cleanup temp files
        self.extractor.cleanup_frames(frames)

        # Check face detection ratio
        face_ratio = faces_found / len(frames) if frames else 0
        if face_ratio < self.min_face_ratio:
            return GateResult(
                passed=False,
                gate_name="temporal_consistency",
                failure_reason=f"Face only detected in {face_ratio:.0%} of frames "
                              f"(need {self.min_face_ratio:.0%})",
                metrics={"face_ratio": face_ratio, "frames_checked": len(frames)},
            )

        if not scores:
            return GateResult(
                passed=False,
                gate_name="temporal_consistency",
                failure_reason="No face embeddings extracted from any frame",
            )

        min_score = min(scores)
        avg_score = sum(scores) / len(scores)
        max_drift = max(scores) - min(scores)

        passed = (min_score >= self.min_threshold) and (avg_score >= self.avg_threshold)

        # Find the worst frame for debugging
        worst_frame_idx = scores.index(min_score)

        return GateResult(
            passed=passed,
            gate_name="temporal_consistency",
            failure_reason=(
                f"Identity drift: min={min_score:.3f} (threshold={self.min_threshold}), "
                f"avg={avg_score:.3f} (threshold={self.avg_threshold}), "
                f"worst frame index={worst_frame_idx}"
                if not passed else None
            ),
            metrics={
                "min_similarity": round(min_score, 4),
                "avg_similarity": round(avg_score, 4),
                "max_drift": round(max_drift, 4),
                "worst_frame_index": worst_frame_idx,
                "frames_checked": len(frames),
                "faces_found": faces_found,
                "all_scores": [round(s, 4) for s in scores],
            },
        )


class GateResult:
    def __init__(self, passed: bool, gate_name: str,
                 failure_reason: str = None, metrics: dict = None):
        self.passed = passed
        self.gate_name = gate_name
        self.failure_reason = failure_reason
        self.metrics = metrics or {}
