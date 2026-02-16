"""Tests for retry policy."""

import pytest
from executor.retry_policy import (
    RetryPolicy,
    RETRYABLE_ERRORS,
    NON_RETRYABLE_ERRORS
)


@pytest.fixture
def policy():
    """Create retry policy with default max_attempts=3."""
    return RetryPolicy(max_attempts=3)


def test_should_retry_retryable_error(policy):
    """Test retryable error returns True."""
    should_retry, reason = policy.should_retry(
        error_code="HANDLER_TIMEOUT",
        attempt=1,
        rollback_success=True
    )

    assert should_retry is True
    assert "Retryable" in reason


def test_should_retry_non_retryable_error(policy):
    """Test non-retryable error returns False."""
    should_retry, reason = policy.should_retry(
        error_code="HANDLER_NOT_FOUND",
        attempt=1,
        rollback_success=True
    )

    assert should_retry is False
    assert "Non-retryable" in reason


def test_should_retry_rollback_failed(policy):
    """Test rollback failure makes task non-retryable."""
    should_retry, reason = policy.should_retry(
        error_code="HANDLER_TIMEOUT",  # Normally retryable
        attempt=1,
        rollback_success=False  # But rollback failed
    )

    assert should_retry is False
    assert "Rollback failed" in reason


def test_should_retry_max_attempts_exceeded(policy):
    """Test max attempts exceeded makes task non-retryable."""
    should_retry, reason = policy.should_retry(
        error_code="HANDLER_TIMEOUT",
        attempt=3,  # Max attempts reached
        rollback_success=True
    )

    assert should_retry is False
    assert "Max retries exceeded" in reason


def test_should_retry_unknown_error(policy):
    """Test unknown error is non-retryable (fail-closed)."""
    should_retry, reason = policy.should_retry(
        error_code="UNKNOWN_ERROR",
        attempt=1,
        rollback_success=True
    )

    assert should_retry is False
    assert "Unknown error type" in reason


def test_get_next_attempt(policy):
    """Test get_next_attempt increments attempt."""
    assert policy.get_next_attempt(1) == 2
    assert policy.get_next_attempt(2) == 3


def test_compute_backoff_phase2_returns_zero(policy):
    """Test Phase 2 backoff is 0ms (immediate retry)."""
    assert policy.compute_backoff_ms(1) == 0
    assert policy.compute_backoff_ms(2) == 0


def test_is_error_retryable(policy):
    """Test is_error_retryable for all error codes."""
    # Retryable errors
    assert policy.is_error_retryable("HANDLER_TIMEOUT") is True
    assert policy.is_error_retryable("HANDLER_EXCEPTION") is True
    assert policy.is_error_retryable("RESOURCE_EXHAUSTED") is True

    # Non-retryable errors
    assert policy.is_error_retryable("HANDLER_NOT_FOUND") is False
    assert policy.is_error_retryable("ROLLBACK_FAILED") is False

    # Unknown errors
    assert policy.is_error_retryable("UNKNOWN") is False


def test_compute_status_success_returns_failure(policy):
    """Test successful retry returns 'failure' status."""
    status = policy.compute_status(
        error_code="HANDLER_TIMEOUT",
        attempt=1,
        rollback_success=True
    )

    assert status == "failure"


def test_compute_status_terminal_returns_dead(policy):
    """Test terminal failure returns 'dead' status."""
    status = policy.compute_status(
        error_code="HANDLER_NOT_FOUND",
        attempt=1,
        rollback_success=True
    )

    assert status == "dead"


def test_compute_status_rollback_failed_returns_dead(policy):
    """Test rollback failure returns 'dead' status."""
    status = policy.compute_status(
        error_code="HANDLER_TIMEOUT",
        attempt=1,
        rollback_success=False
    )

    assert status == "dead"


def test_max_attempts_configurable():
    """Test max_attempts can be configured."""
    policy = RetryPolicy(max_attempts=5)

    assert policy.max_attempts == 5

    # Should allow retry up to attempt 4
    should_retry, _ = policy.should_retry("HANDLER_TIMEOUT", 4, True)
    assert should_retry is True

    # Attempt 5 should not retry
    should_retry, _ = policy.should_retry("HANDLER_TIMEOUT", 5, True)
    assert should_retry is False


def test_max_attempts_must_be_positive():
    """Test max_attempts must be >= 1."""
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        RetryPolicy(max_attempts=0)


def test_all_retryable_errors_covered():
    """Test all RETRYABLE_ERRORS are handled correctly."""
    policy = RetryPolicy(max_attempts=3)

    for error_code in RETRYABLE_ERRORS:
        should_retry, reason = policy.should_retry(error_code, 1, True)
        assert should_retry is True, f"{error_code} should be retryable"
        assert "Retryable" in reason


def test_all_non_retryable_errors_covered():
    """Test all NON_RETRYABLE_ERRORS are handled correctly."""
    policy = RetryPolicy(max_attempts=3)

    for error_code in NON_RETRYABLE_ERRORS:
        should_retry, reason = policy.should_retry(error_code, 1, True)
        assert should_retry is False, f"{error_code} should not be retryable"
        assert "Non-retryable" in reason
