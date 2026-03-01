# quality/motion_quality_gate.py

from .frame_extractor import FrameExtractor
from .temporal_consistency_gate import GateResult
import numpy as np
from PIL import Image


class MotionQualityGate:
    """Detects frozen frames, excessive jitter, and motion artifacts.

    Tier: 1.5 (runs after heuristic_gate, before CLIP alignment)
    Cost: Free (local pixel comparison)
    """

    def __init__(
        self,
        frozen_threshold: float = 0.995,   # SSIM above this = frozen
        max_frozen_ratio: float = 0.30,     # Max 30% of frames can be frozen
        jitter_threshold: float = 0.15,     # Frame-to-frame diff variance threshold
    ):
        self.frozen_threshold = frozen_threshold
        self.max_frozen_ratio = max_frozen_ratio
        self.jitter_threshold = jitter_threshold
        self.extractor = FrameExtractor()

    async def evaluate(self, video_path: str) -> GateResult:
        # Extract every 5th frame for motion analysis (more granular than identity check)
        frames = self.extractor.extract_at_intervals(video_path, interval_frames=5)

        if len(frames) < 3:
            return GateResult(passed=True, gate_name="motion_quality",
                            metrics={"note": "Too few frames to analyze"})

        # Load frames as numpy arrays
        arrays = []
        for f in frames:
            img = Image.open(f).convert("RGB").resize((256, 256))
            arrays.append(np.array(img, dtype=np.float32) / 255.0)

        # Calculate frame-to-frame differences
        diffs = []
        for i in range(1, len(arrays)):
            diff = np.mean(np.abs(arrays[i] - arrays[i-1]))
            diffs.append(diff)

        frozen_count = sum(1 for d in diffs if d < (1 - self.frozen_threshold))
        frozen_ratio = frozen_count / len(diffs)
        diff_variance = np.var(diffs)
        avg_motion = np.mean(diffs)

        self.extractor.cleanup_frames(frames)

        # Check for frozen video (slideshow detection)
        if frozen_ratio > self.max_frozen_ratio:
            return GateResult(
                passed=False,
                gate_name="motion_quality",
                failure_reason=f"Video appears frozen: {frozen_ratio:.0%} of frame pairs "
                              f"show no motion (max allowed: {self.max_frozen_ratio:.0%})",
                metrics={"frozen_ratio": frozen_ratio, "avg_motion": float(avg_motion)},
            )

        # Check for jitter (erratic motion)
        if diff_variance > self.jitter_threshold:
            return GateResult(
                passed=False,
                gate_name="motion_quality",
                failure_reason=f"Excessive motion jitter detected: variance={diff_variance:.4f} "
                              f"(threshold={self.jitter_threshold})",
                metrics={"diff_variance": float(diff_variance), "avg_motion": float(avg_motion)},
            )

        return GateResult(
            passed=True,
            gate_name="motion_quality",
            metrics={
                "frozen_ratio": round(frozen_ratio, 4),
                "avg_motion": round(float(avg_motion), 4),
                "diff_variance": round(float(diff_variance), 4),
                "frame_pairs_checked": len(diffs),
            },
        )
