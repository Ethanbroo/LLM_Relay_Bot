"""Retry policy for failed task executions.

Implements retry matrix with:
1. Error-specific retry rules
2. Max attempts limit (default: 3)
3. Rollback-before-retry (mandatory)
4. Exponential backoff (Phase 3 - not implemented in Phase 2)

Non-negotiable:
- Rollback failure → task becomes dead (no retry)
- Max retries exceeded → task becomes dead
- Non-retryable errors → task becomes dead immediately
"""

from typing import Literal


# Error codes that are retryable
RETRYABLE_ERRORS = {
    "HANDLER_TIMEOUT",          # Handler took too long → retry
    "HANDLER_EXCEPTION",        # Transient exception → retry
    "RESOURCE_EXHAUSTED",       # Temporary resource issue → retry
    "SANDBOX_DESTROYED_UNEXPECTEDLY",  # Sandbox crashed → retry
}

# Error codes that are NOT retryable (terminal failures)
NON_RETRYABLE_ERRORS = {
    "HANDLER_NOT_FOUND",        # No handler for action → dead
    "SANDBOX_CREATION_FAILED",  # Can't create sandbox → dead
    "ROLLBACK_FAILED",          # Can't rollback → dead (terminal)
    "MAX_RETRIES_EXCEEDED",     # Too many attempts → dead
    "ARTIFACT_VALIDATION_FAILED",  # Output invalid → dead
    "SNAPSHOT_FAILED",          # Can't snapshot → dead
    "PERMISSION_DENIED",        # RBAC violation → dead
}


class RetryPolicy:
    """Determines if and how to retry failed tasks."""

    def __init__(self, max_attempts: int = 3):
        """Initialize retry policy.

        Args:
            max_attempts: Maximum number of execution attempts (default: 3)
                         Attempts are 1-indexed: 1 (first), 2 (retry 1), 3 (retry 2)
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        self.max_attempts = max_attempts

    def should_retry(
        self,
        error_code: str,
        attempt: int,
        rollback_success: bool
    ) -> tuple[bool, str]:
        """Determine if task should be retried.

        Args:
            error_code: Error code from execution failure
            attempt: Current attempt number (1-indexed)
            rollback_success: True if rollback succeeded, False if rollback failed

        Returns:
            Tuple of (should_retry: bool, reason: str)

        Invariants:
        - If rollback_success is False → (False, "Rollback failed")
        - If attempt >= max_attempts → (False, "Max retries exceeded")
        - If error_code in NON_RETRYABLE_ERRORS → (False, "Non-retryable error")
        - If error_code in RETRYABLE_ERRORS → (True, "Retryable error")
        - Otherwise → (False, "Unknown error type")
        """
        # Invariant 1: Rollback failure is terminal
        if not rollback_success:
            return (False, "Rollback failed - task is dead")

        # Invariant 2: Max attempts exceeded
        if attempt >= self.max_attempts:
            return (False, f"Max retries exceeded ({self.max_attempts} attempts)")

        # Invariant 3: Non-retryable error
        if error_code in NON_RETRYABLE_ERRORS:
            return (False, f"Non-retryable error: {error_code}")

        # Invariant 4: Retryable error
        if error_code in RETRYABLE_ERRORS:
            return (True, f"Retryable error: {error_code}")

        # Unknown error type → fail-closed (don't retry)
        return (False, f"Unknown error type: {error_code}")

    def get_next_attempt(self, current_attempt: int) -> int:
        """Get next attempt number.

        Args:
            current_attempt: Current attempt number (1-indexed)

        Returns:
            Next attempt number (current + 1)
        """
        return current_attempt + 1

    def compute_backoff_ms(self, attempt: int) -> int:
        """Compute backoff delay for retry (milliseconds).

        Phase 2: Returns 0 (no delay - immediate retry)
        Phase 3: Will implement exponential backoff

        Args:
            attempt: Attempt number (1-indexed)

        Returns:
            Delay in milliseconds before retry
        """
        # Phase 2: No backoff (immediate retry)
        return 0

    def is_error_retryable(self, error_code: str) -> bool:
        """Check if error code is retryable (ignoring attempt count).

        Args:
            error_code: Error code from execution

        Returns:
            True if error is retryable, False otherwise
        """
        return error_code in RETRYABLE_ERRORS

    def compute_status(
        self,
        error_code: str,
        attempt: int,
        rollback_success: bool
    ) -> Literal["failure", "dead"]:
        """Compute task status after failure.

        Args:
            error_code: Error code from execution
            attempt: Current attempt number
            rollback_success: True if rollback succeeded

        Returns:
            "failure" if retryable, "dead" if terminal
        """
        should_retry, _ = self.should_retry(error_code, attempt, rollback_success)

        if should_retry:
            return "failure"  # Retryable failure
        else:
            return "dead"     # Terminal failure
