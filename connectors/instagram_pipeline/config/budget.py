"""
Video generation budget configuration.

TO CHANGE MAX BUDGET: Edit the MAX_COST_PER_POST values below.
Example: To reduce narrative_reel max from $6 to $1, change 6.00 to 1.00.
That's it. No other changes needed. The pipeline respects these limits automatically.
"""

import logging

logger = logging.getLogger(__name__)


class BudgetConfig:
    """Per-post budget caps by content format.

    The pipeline tracks cumulative cost across all generation attempts
    (including retries from quality gate failures) and halts when the
    cap is reached, falling back to the next cheaper format or static image.
    """

    # ============================================================
    # MAX COST PER POST (USD) — Edit these values to adjust limits
    # ============================================================
    MAX_COST_PER_POST = {
        "static_image":      0.50,
        "avatar_talking":    5.00,
        "narrative_reel":    6.00,
        "cinematic_clip":    4.00,
        "gameplay_overlay":  2.00,
    }

    # Cost estimates per endpoint (USD per second of video)
    COST_PER_SECOND = {
        # Kling Standard tier
        "fal-ai/kling-video/o3/standard/reference-to-video": 0.224,
        "fal-ai/kling-video/o3/standard/text-to-video":      0.224,
        "fal-ai/kling-video/v3/standard/text-to-video":      0.168,
        "fal-ai/kling-video/v2.6/standard/image-to-video":   0.070,
        "fal-ai/kling-video/v2.6/standard/text-to-video":    0.070,
        "fal-ai/kling-video/ai-avatar/v2/standard":          0.058,
        # Kling O1 (no standard tier)
        "fal-ai/kling-video/o1/video-to-video/edit":         0.168,
        "fal-ai/kling-video/o1/reference-to-video":          0.112,
        # SiliconFlow flat rate (duration doesn't matter)
        "siliconflow/wan2.2-t2v":                            0.000,
        "siliconflow/wan2.2-i2v":                            0.000,
        # FFmpeg (free)
        "ffmpeg_pip_composite":                              0.000,
    }

    # Flat-rate costs for providers that don't charge per-second
    FLAT_RATE_COSTS = {
        "siliconflow/wan2.2-t2v": 0.29,
        "siliconflow/wan2.2-i2v": 0.29,
        "ffmpeg_pip_composite": 0.00,
        # ComfyUI local (Mac) — electricity only
        "comfyui/local": 0.00,
        # ComfyUI cloud (RunPod A100 spot ~$0.44/hr, ~4 min/image)
        "comfyui/runpod": 0.03,
    }

    MAX_RETRIES_PER_TIER = 3

    COST_WARNING_THRESHOLD = 0.80


class CostTracker:
    """Tracks cumulative cost within a single post generation session."""

    def __init__(self, content_format: str, config: BudgetConfig = None):
        self.config = config or BudgetConfig()
        self.content_format = content_format
        self.budget_cap = self.config.MAX_COST_PER_POST.get(content_format, 5.00)
        self.total_spent = 0.0
        self.attempts = []

    def estimate_cost(self, endpoint: str, duration_seconds: int) -> float:
        if endpoint in self.config.FLAT_RATE_COSTS:
            return self.config.FLAT_RATE_COSTS[endpoint]
        rate = self.config.COST_PER_SECOND.get(endpoint, 0.20)
        return rate * duration_seconds

    def record_attempt(self, endpoint: str, duration_seconds: int, success: bool):
        cost = self.estimate_cost(endpoint, duration_seconds)
        self.total_spent += cost
        self.attempts.append({
            "endpoint": endpoint,
            "duration": duration_seconds,
            "cost": cost,
            "success": success,
            "cumulative": self.total_spent,
        })
        if self.over_warning_threshold():
            logger.warning(
                f"Cost warning: ${self.total_spent:.2f} of ${self.budget_cap:.2f} "
                f"budget used for {self.content_format}"
            )

    def can_afford(self, endpoint: str, duration_seconds: int) -> bool:
        estimated = self.estimate_cost(endpoint, duration_seconds)
        return (self.total_spent + estimated) <= self.budget_cap

    def over_warning_threshold(self) -> bool:
        return self.total_spent >= (self.budget_cap * self.config.COST_WARNING_THRESHOLD)

    def summary(self) -> dict:
        return {
            "content_format": self.content_format,
            "budget_cap": self.budget_cap,
            "total_spent": round(self.total_spent, 4),
            "remaining": round(self.budget_cap - self.total_spent, 4),
            "attempts": len(self.attempts),
            "successful": sum(1 for a in self.attempts if a["success"]),
        }
