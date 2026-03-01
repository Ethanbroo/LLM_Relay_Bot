"""Tests for the Failure Mode Taxonomy."""

import pytest
from multi_agent_v2.failure_modes import (
    FailureModeHandler, FailureCondition, ResponseMode,
    RetryTracker, TokenBudgetMonitor, FailureResult,
    FAILURE_RESPONSE_MAP
)


class TestFailureResponseMap:
    def test_all_conditions_have_response_modes(self):
        for condition in FailureCondition:
            assert condition in FAILURE_RESPONSE_MAP

    def test_user_unavailable_pauses_and_escalates(self):
        assert FAILURE_RESPONSE_MAP[FailureCondition.USER_UNAVAILABLE_AT_DECISION] == \
               ResponseMode.PAUSE_AND_ESCALATE

    def test_policy_conflict_halts_and_escalates(self):
        assert FAILURE_RESPONSE_MAP[FailureCondition.POLICY_CONFLICT_DETECTED] == \
               ResponseMode.HALT_AND_ESCALATE

    def test_token_budget_warns_and_halts(self):
        assert FAILURE_RESPONSE_MAP[FailureCondition.TOKEN_BUDGET_EXCEEDED] == \
               ResponseMode.WARN_AND_HALT


class TestRetryTracker:
    def test_not_exhausted_initially(self):
        t = RetryTracker(max_retries=3)
        assert not t.exhausted("agent_a")

    def test_exhausted_after_max(self):
        t = RetryTracker(max_retries=3)
        for _ in range(3):
            t.increment("agent_a")
        assert t.exhausted("agent_a")

    def test_independent_agents(self):
        t = RetryTracker(max_retries=2)
        t.increment("agent_a")
        t.increment("agent_a")
        assert t.exhausted("agent_a")
        assert not t.exhausted("agent_b")


class TestTokenBudgetMonitor:
    def test_no_failure_under_budget(self):
        m = TokenBudgetMonitor(budget=100)
        result = m.consume(50)
        assert result is None

    def test_failure_at_100_percent(self):
        m = TokenBudgetMonitor(budget=100)
        result = m.consume(100)
        assert result is not None
        assert result.condition == FailureCondition.TOKEN_BUDGET_EXCEEDED
        assert result.is_terminal

    def test_warning_emitted_at_80_percent(self):
        events = []
        m = TokenBudgetMonitor(budget=100, audit_callback=lambda t, p: events.append(t))
        m.consume(80)
        assert "TOKEN_BUDGET_WARNING" in events

    def test_remaining_decreases(self):
        m = TokenBudgetMonitor(budget=100)
        m.consume(30)
        assert m.remaining == 70


class TestFailureModeHandler:
    def setup_method(self):
        self.events = []
        self.handler = FailureModeHandler(
            audit_callback=lambda t, p: self.events.append(t)
        )

    def test_malformed_output_retryable_initially(self):
        result = self.handler.handle(
            FailureCondition.AGENT_MALFORMED_OUTPUT,
            agent_name="agent_a",
            detail="Missing key",
        )
        assert result.can_retry is True
        assert result.response_mode == ResponseMode.RETRY_AND_DEGRADE

    def test_malformed_output_not_retryable_after_3(self):
        for _ in range(3):
            result = self.handler.handle(
                FailureCondition.AGENT_MALFORMED_OUTPUT,
                agent_name="agent_a",
                detail="Missing key",
            )
        assert result.can_retry is False

    def test_ground_truth_fail_retryable(self):
        result = self.handler.handle(
            FailureCondition.GROUND_TRUTH_CHECK_FAILED,
            agent_name="validator",
            detail="Schema mismatch",
        )
        assert result.can_retry is True
        assert result.response_mode == ResponseMode.SURFACE_AND_LOOP

    def test_policy_conflict_is_terminal(self):
        result = self.handler.handle(
            FailureCondition.POLICY_CONFLICT_DETECTED,
            detail="Policy A vs Policy B",
        )
        assert result.is_terminal is True

    def test_role_drift_warns_and_constrains(self):
        result = self.handler.handle(
            FailureCondition.AGENT_ROLE_DRIFT_DETECTED,
            agent_name="creative_enhancer",
            detail="Used 'I recommend' phrase",
        )
        assert result.response_mode == ResponseMode.WARN_AND_CONSTRAIN
        assert result.can_retry is True
        assert "AGENT_ROLE_DRIFT_FLAGGED" in self.events

    def test_audit_callback_called_for_all_conditions(self):
        for condition in FailureCondition:
            self.handler.handle(condition, detail="test")
        assert "FAILURE_MODE_TRIGGERED" in self.events
