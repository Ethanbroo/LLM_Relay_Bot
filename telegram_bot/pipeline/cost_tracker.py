"""Per-call and per-project cost tracking via Redis."""

import logging
from datetime import date

logger = logging.getLogger(__name__)


class CostTracker:
    """Tracks token usage and cost per day and per project in Redis."""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def record(
        self,
        project_name: str,
        phase_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ):
        """Record cost of a single claude -p call."""
        if not self.redis:
            return

        today = date.today().isoformat()
        total_tokens = input_tokens + output_tokens

        pipe = self.redis.pipeline()

        # Daily totals
        pipe.hincrbyfloat(f"cost:{today}", "total_usd", cost_usd)
        pipe.hincrby(f"cost:{today}", "total_tokens", total_tokens)
        pipe.hincrby(f"cost:{today}", "total_calls", 1)
        pipe.expire(f"cost:{today}", 90 * 86400)  # 90-day TTL

        # Per-project breakdown
        pipe.hincrbyfloat(f"cost:{today}", f"project:{project_name}:usd", cost_usd)
        pipe.hincrby(f"cost:{today}", f"project:{project_name}:tokens", total_tokens)

        # Per-model tracking (for billing analysis)
        pipe.hincrby(f"cost:{today}", f"model:{model}:tokens", total_tokens)

        await pipe.execute()

        logger.info(
            f"Cost recorded: {phase_name} ({model}) — "
            f"{total_tokens:,} tokens, ${cost_usd:.4f}"
        )

    async def check_budget(self, project_name: str, budget_limit: float) -> tuple[bool, float]:
        """Check if project has exceeded its token budget.

        Returns (within_budget: bool, total_spent: float)
        """
        if not self.redis:
            return True, 0.0

        # Sum across all days for this project
        total = 0.0
        async for key in self.redis.scan_iter(match="cost:*", count=100):
            val = await self.redis.hget(key, f"project:{project_name}:usd")
            if val:
                total += float(val)

        return total < budget_limit, total
