"""Tier 1 Quality Gate: Heuristic Checks.

Fast, cheap sanity checks that catch obvious failures before spending
compute on expensive gates. File format, resolution, corruption, etc.
Supports both image and video media types.
"""

import logging
import time
from pathlib import Path
from typing import Optional

from .models import QualityGateResult, GateDecision, GateTier

logger = logging.getLogger(__name__)

# Video file extensions recognized by this gate
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


class HeuristicGate:
    """
    Tier 1: Basic heuristic checks.

    These are fast, free, and catch obvious problems:
    - File exists and is readable
    - File format is correct (JPEG/PNG for images; MP4/WEBM/MOV for video)
    - Resolution meets minimum requirements
    - File size is reasonable (not corrupted/truncated)
    - Image is not completely black/white (generation failure)
    """

    def __init__(
        self,
        min_width: int = 512,
        min_height: int = 512,
        max_width: int = 4096,
        max_height: int = 4096,
        min_file_size_bytes: int = 10_000,      # 10KB minimum
        max_file_size_bytes: int = 50_000_000,  # 50MB maximum (images)
        allowed_formats: tuple = ("JPEG", "PNG", "WEBP"),
        media_type: str = "image",              # "image" or "video"
        max_video_file_size_bytes: int = 500_000_000,  # 500MB for video
        allowed_video_extensions: tuple = (".mp4", ".webm", ".mov"),
    ):
        self.min_width = min_width
        self.min_height = min_height
        self.max_width = max_width
        self.max_height = max_height
        self.min_file_size = min_file_size_bytes
        self.max_file_size = max_file_size_bytes
        self.allowed_formats = allowed_formats
        self.media_type = media_type
        self.max_video_file_size = max_video_file_size_bytes
        self.allowed_video_extensions = allowed_video_extensions

    def evaluate(self, asset_path: str) -> QualityGateResult:
        """
        Run all heuristic checks on an image or video file.

        Args:
            asset_path: Path to image or video file

        Returns:
            QualityGateResult with PASS/FAIL decision
        """
        path = Path(asset_path)

        # Auto-detect media type from extension if needed
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            return self._evaluate_video(asset_path)
        elif self.media_type == "video":
            return self._evaluate_video(asset_path)
        else:
            return self._evaluate_image(asset_path)

    def _evaluate_image(self, image_path: str) -> QualityGateResult:
        """Run heuristic checks on an image file."""
        start_time = time.time()
        path = Path(image_path)

        logger.info("Running Tier 1 heuristic gate (image) on %s", path.name)

        # Check 1: File exists
        if not path.exists():
            return self._fail_result(
                f"File not found: {image_path}",
                execution_time_s=time.time() - start_time
            )

        # Check 2: File size is reasonable
        file_size = path.stat().st_size
        if file_size < self.min_file_size:
            return self._fail_result(
                f"File too small ({file_size} bytes < {self.min_file_size} bytes) - "
                f"likely truncated or corrupted",
                execution_time_s=time.time() - start_time
            )

        if file_size > self.max_file_size:
            return self._fail_result(
                f"File too large ({file_size} bytes > {self.max_file_size} bytes)",
                execution_time_s=time.time() - start_time
            )

        # Check 3: File is valid image and meets resolution requirements
        try:
            from PIL import Image
            img = Image.open(image_path)
        except Exception as e:
            return self._fail_result(
                f"Failed to open image: {e}",
                execution_time_s=time.time() - start_time
            )

        width, height = img.size

        if width < self.min_width or height < self.min_height:
            return self._fail_result(
                f"Image resolution too low ({width}x{height}, "
                f"minimum {self.min_width}x{self.min_height})",
                execution_time_s=time.time() - start_time
            )

        if width > self.max_width or height > self.max_height:
            return self._fail_result(
                f"Image resolution too high ({width}x{height}, "
                f"maximum {self.max_width}x{self.max_height})",
                execution_time_s=time.time() - start_time
            )

        # Check 4: Format is allowed
        if img.format not in self.allowed_formats:
            return self._fail_result(
                f"Invalid image format: {img.format} "
                f"(allowed: {', '.join(self.allowed_formats)})",
                execution_time_s=time.time() - start_time
            )

        # Check 5: Image is not completely black or white
        is_degenerate, reason = self._check_degenerate_image(img)
        if is_degenerate:
            return self._fail_result(
                f"Degenerate image detected: {reason}",
                execution_time_s=time.time() - start_time
            )

        execution_time = time.time() - start_time
        logger.info("Tier 1 heuristic gate: PASS")

        return QualityGateResult(
            tier=GateTier.TIER_1_HEURISTIC,
            decision=GateDecision.PASS,
            reason=f"All heuristic checks passed ({width}x{height}, {file_size} bytes)",
            details={
                "width": width,
                "height": height,
                "format": img.format,
                "mode": img.mode,
                "file_size_bytes": file_size,
                "media_type": "image",
            },
            execution_time_s=execution_time,
        )

    def _evaluate_video(self, video_path: str) -> QualityGateResult:
        """Run heuristic checks on a video file."""
        start_time = time.time()
        path = Path(video_path)

        logger.info("Running Tier 1 heuristic gate (video) on %s", path.name)

        # Check 1: File exists
        if not path.exists():
            return self._fail_result(
                f"File not found: {video_path}",
                execution_time_s=time.time() - start_time
            )

        # Check 2: File extension is allowed
        if path.suffix.lower() not in self.allowed_video_extensions:
            return self._fail_result(
                f"Invalid video format: {path.suffix} "
                f"(allowed: {', '.join(self.allowed_video_extensions)})",
                execution_time_s=time.time() - start_time
            )

        # Check 3: File size
        file_size = path.stat().st_size
        if file_size < self.min_file_size:
            return self._fail_result(
                f"Video file too small ({file_size} bytes) - likely corrupted",
                execution_time_s=time.time() - start_time
            )

        if file_size > self.max_video_file_size:
            return self._fail_result(
                f"Video file too large ({file_size} bytes > "
                f"{self.max_video_file_size} bytes)",
                execution_time_s=time.time() - start_time
            )

        # Check 4: Video metadata via ffprobe
        try:
            from .frame_extractor import FrameExtractor
            extractor = FrameExtractor()
            info = extractor.get_video_info(video_path)
        except Exception as e:
            return self._fail_result(
                f"Failed to read video metadata: {e}",
                execution_time_s=time.time() - start_time
            )

        width = info.get("width", 0)
        height = info.get("height", 0)
        duration = info.get("duration", 0)

        if width < self.min_width or height < self.min_height:
            return self._fail_result(
                f"Video resolution too low ({width}x{height}, "
                f"minimum {self.min_width}x{self.min_height})",
                execution_time_s=time.time() - start_time
            )

        if duration <= 0:
            return self._fail_result(
                f"Video has no duration (possibly corrupted)",
                execution_time_s=time.time() - start_time
            )

        if duration > 300:  # 5 minutes max
            return self._fail_result(
                f"Video too long ({duration:.1f}s > 300s max)",
                execution_time_s=time.time() - start_time
            )

        execution_time = time.time() - start_time
        logger.info("Tier 1 heuristic gate (video): PASS")

        return QualityGateResult(
            tier=GateTier.TIER_1_HEURISTIC,
            decision=GateDecision.PASS,
            reason=(
                f"All video heuristic checks passed "
                f"({width}x{height}, {duration:.1f}s, {file_size} bytes)"
            ),
            details={
                "width": width,
                "height": height,
                "duration": duration,
                "fps": info.get("fps", 0),
                "codec": info.get("codec", ""),
                "has_audio": info.get("has_audio", False),
                "file_size_bytes": file_size,
                "media_type": "video",
            },
            execution_time_s=execution_time,
        )

    def _fail_result(
        self,
        reason: str,
        execution_time_s: float
    ) -> QualityGateResult:
        """Helper to create FAIL result."""
        logger.warning("Tier 1 heuristic gate: FAIL - %s", reason)
        return QualityGateResult(
            tier=GateTier.TIER_1_HEURISTIC,
            decision=GateDecision.FAIL,
            reason=reason,
            execution_time_s=execution_time_s,
        )

    def _check_degenerate_image(self, img) -> tuple[bool, str]:
        """
        Check if image is degenerate (all black, all white, etc.).

        Returns:
            Tuple of (is_degenerate, reason)
        """
        try:
            import numpy as np

            if img.mode != "RGB":
                img = img.convert("RGB")

            img_array = np.array(img)
            h, w, _ = img_array.shape

            sample_size = min(1000, h * w)
            indices = np.random.choice(h * w, sample_size, replace=False)
            rows = indices // w
            cols = indices % w
            samples = img_array[rows, cols]

            mean_val = samples.mean()
            std_val = samples.std()

            if mean_val < 10 and std_val < 5:
                return True, "image is completely black"

            if mean_val > 245 and std_val < 5:
                return True, "image is completely white"

            if std_val < 3:
                return True, f"no variance (std={std_val:.1f}, mean={mean_val:.1f})"

            return False, ""

        except Exception as e:
            logger.warning("Failed to check for degenerate image: %s", e)
            return False, ""

    def evaluate_batch(self, asset_paths: list[str]) -> list[QualityGateResult]:
        """Evaluate multiple images or videos."""
        return [self.evaluate(path) for path in asset_paths]
