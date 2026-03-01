"""Tests for the Policy-in-the-Loop Transition Protocol."""

import pytest
from datetime import datetime, timezone, timedelta
from multi_agent_v2.goal_contract import (
    GoalContract, AuthorityModel, ConflictResolution, RiskTolerance, OnTimeout, ValidationTier
)
from multi_agent_v2.policy_in_loop import (
    PolicyInLoopProtocol, PolicyDefinition, PolicyStatus,
    PolicyConflictError, PolicyOutOfScopeError
)


def make_contract(scope=None, ack=False):
    return GoalContract(
        objective="Test task",
        semantic_intent_anchor="Test anchor.",
        success_criteria=["Criterion A"],
        non_goals=[],
        risk_tolerance=RiskTolerance.LOW,
        authority_model=AuthorityModel(
            primary_authority="user_1",
            policy_owner="po_1" if scope else None,
            policy_scope=scope or [],
            policy_in_loop_acknowledged=ack,
        ),
        conflict_resolution=ConflictResolution(24, OnTimeout.HALT),
        validation_tier_minimum=ValidationTier.REAL,
    )


def make_policy(decision_types, action="select_first", expiry_offset_hours=None):
    expiry = None
    if expiry_offset_hours is not None:
        delta = timedelta(hours=expiry_offset_hours)
        expiry = (datetime.now(timezone.utc) + delta).isoformat()
    return PolicyDefinition(
        policy_id=f"policy_{decision_types[0]}",
        version="1.0",
        decision_types_covered=decision_types,
        conditions={},
        action=action,
        defined_by="policy_owner_1",
        defined_at=datetime.now(timezone.utc).isoformat(),
        expiry_at=expiry,
    )


class TestPolicyRegistration:
    def test_register_emits_audit(self):
        events = []
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc, audit_callback=lambda t, p: events.append(t))
        policy = make_policy(["approve_creative_enhancement"])
        pitl.register(policy)
        assert "POLICY_REGISTERED" in events

    def test_supersede_existing_policy(self):
        events = []
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc, audit_callback=lambda t, p: events.append(t))
        p1 = make_policy(["approve_creative_enhancement"])
        p2 = make_policy(["approve_creative_enhancement"])
        p2.version = "2.0"
        pitl.register(p1)
        pitl.register(p2)
        assert "POLICY_SUPERSEDED" in events


class TestPolicyApplication:
    def test_apply_returns_none_without_pitl_ack(self):
        gc = make_contract()  # No PITL ack
        pitl = PolicyInLoopProtocol(gc)
        result = pitl.apply("approve_creative_enhancement", [], {})
        assert result is None

    def test_apply_selects_first_option(self):
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc)
        policy = make_policy(["approve_creative_enhancement"], action="select_first")
        pitl.register(policy)
        options = ["Option A", "Option B"]
        result = pitl.apply("approve_creative_enhancement", options, {})
        assert result == "Option A"

    def test_apply_returns_none_for_no_applicable_policy(self):
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc)
        # No policy registered
        result = pitl.apply("approve_creative_enhancement", ["A"], {})
        assert result is None

    def test_apply_logs_execution(self):
        events = []
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc, audit_callback=lambda t, p: events.append(t))
        pitl.register(make_policy(["approve_creative_enhancement"]))
        pitl.apply("approve_creative_enhancement", ["Option"], {})
        assert "POLICY_EXECUTED" in events

    def test_audit_log_marks_pitl_mode(self):
        payloads = []
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc, audit_callback=lambda t, p: payloads.append(p))
        pitl.register(make_policy(["approve_creative_enhancement"]))
        pitl.apply("approve_creative_enhancement", ["Option"], {})
        pitl_events = [p for p in payloads if p.get("pitl_mode")]
        assert len(pitl_events) > 0

    def test_expired_policy_not_applied(self):
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc)
        expired = make_policy(["approve_creative_enhancement"], expiry_offset_hours=-1)
        pitl.register(expired)
        result = pitl.apply("approve_creative_enhancement", ["Option"], {})
        assert result is None


class TestPolicyConflicts:
    def test_two_conflicting_policies_raises(self):
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc)
        p1 = make_policy(["approve_creative_enhancement"], action="select_first")
        p2 = make_policy(["approve_creative_enhancement"], action="select_none")
        p2.policy_id = "policy_conflict_b"
        pitl.register(p1)
        pitl.register(p2)
        with pytest.raises(PolicyConflictError):
            pitl.apply("approve_creative_enhancement", ["Option"], {})


class TestSessionSummary:
    def test_summary_generated_after_executions(self):
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc)
        pitl.register(make_policy(["approve_creative_enhancement"]))
        pitl.apply("approve_creative_enhancement", ["Option"], {})
        summary = pitl.generate_session_summary()
        assert summary["total_policy_executions"] == 1
        assert summary["pitl_acknowledged"] is True

    def test_summary_flags_no_reviewer_action_needed_normally(self):
        gc = make_contract(scope=["approve_creative_enhancement"], ack=True)
        pitl = PolicyInLoopProtocol(gc)
        pitl.register(make_policy(["approve_creative_enhancement"]))
        pitl.apply("approve_creative_enhancement", ["Option"], {})
        summary = pitl.generate_session_summary()
        assert summary["reviewer_action_required"] is False
