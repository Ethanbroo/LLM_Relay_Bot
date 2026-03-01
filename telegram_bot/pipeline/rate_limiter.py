"""Rate limit detection and backoff for Claude Max subscription."""

import asyncio
import logging
import random
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Max retry attempts before giving up
MAX_RETRIES = 5

# Backoff bounds (seconds)
MIN_BACKOFF = 30
MAX_BACKOFF = 600     # 10 minutes


@dataclass
class RateLimitInfo:
    """Parsed rate limit information from claude -p stderr."""
    is_rate_limited: bool
    retry_after_seconds: int | None = None
    raw_message: str = ""


def parse_rate_limit(stderr: str) -> RateLimitInfo:
    """Detect rate limiting from claude -p stderr output."""
    if not stderr:
        return RateLimitInfo(is_rate_limited=False)

    stderr_lower = stderr.lower()

    # Check for explicit 429
    if "429" in stderr or "rate limit" in stderr_lower or "too many requests" in stderr_lower:
        # Try to extract retry-after value
        match = re.search(r"retry[- ]after[:\s]+(\d+)", stderr_lower)
        retry_after = int(match.group(1)) if match else None

        return RateLimitInfo(
            is_rate_limited=True,
            retry_after_seconds=retry_after,
            raw_message=stderr[:300],
        )

    return RateLimitInfo(is_rate_limited=False)


async def backoff_with_jitter(attempt: int, retry_after: int | None = None) -> float:
    """Calculate and execute exponential backoff with jitter.

    Uses full jitter strategy for maximum decorrelation.
    Returns the actual wait time in seconds.
    """
    if retry_after:
        # Server told us exactly when to retry — respect it + small jitter
        wait = retry_after + random.uniform(1, 10)
    else:
        # Exponential backoff: 30, 60, 120, 240, 480 (capped at MAX_BACKOFF)
        base = min(MIN_BACKOFF * (2 ** attempt), MAX_BACKOFF)
        # Full jitter: random between MIN_BACKOFF and base
        wait = random.uniform(MIN_BACKOFF, base)

    logger.info(f"Rate limit backoff: attempt {attempt + 1}, waiting {wait:.0f}s")
    await asyncio.sleep(wait)
    return wait
