"""Tests for the Authority Model."""

import pytest
from multi_agent_v2.goal_contract import (
    GoalContract, AuthorityModel, ConflictResolution, RiskTolerance, OnTimeout, ValidationTier
)
from multi_agent_v2.authority_model import (
    AuthorityModelResolver, DecisionType, AuthorityTier, DECISION_AUTHORITY_MATRIX
)


def make_contract(policy_scope=None, pitl=False):
    scope = policy_scope or []
    ack = pitl
    return GoalContract(
        objective="Test task",
        semantic_intent_anchor="Test anchor.",
        success_criteria=["Criterion A"],
        non_goals=["Non-goal A"],
        risk_tolerance=RiskTolerance.LOW,
        authority_model=AuthorityModel(
            primary_authority="user_1",
            policy_owner="policy_owner_1" if scope else None,
            policy_scope=scope,
            policy_in_loop_acknowledged=ack,
        ),
        conflict_resolution=ConflictResolution(24, OnTimeout.HALT),
        validation_tier_minimum=ValidationTier.REAL,
    )


class TestDecisionAuthorityMatrix:
    def test_confirm_goal_contract_requires_primary(self):
        assert DECISION_AUTHORITY_MATRIX[DecisionType.CONFIRM_GOAL_CONTRACT] == AuthorityTier.PRIMARY

    def test_refinement_loop_is_system(self):
        assert DECISION_AUTHORITY_MATRIX[DecisionType.TRIGGER_REFINEMENT_LOOP] == AuthorityTier.SYSTEM

    def test_approve_creative_is_delegatable(self):
        assert DECISION_AUTHORITY_MATRIX[DecisionType.APPROVE_CREATIVE_ENHANCEMENT] == AuthorityTier.DELEGATED


class TestAuthorityModelResolver:
    def test_primary_required_user_available(self):
        gc = make_contract()
        resolver = AuthorityModelResolver(gc)
        res = resolver.resolve(DecisionType.CONFIRM_GOAL_CONTRACT, user_available=True)
        assert res.resolved_tier == AuthorityTier.PRIMARY
        assert res.resolved_by == "user_1"

    def test_primary_required_user_unavailable(self):
        gc = make_contract()
        resolver = AuthorityModelResolver(gc)
        res = resolver.resolve(DecisionType.CONFIRM_GOAL_CONTRACT, user_available=False)
        assert res.resolved_by == "UNAVAILABLE"
        assert "unavailable" in res.notes.lower()

    def test_policy_handles_delegatable_when_in_scope(self):
        gc = make_contract(
            policy_scope=["approve_creative_enhancement"],
            pitl=True
        )
        resolver = AuthorityModelResolver(gc)
        res = resolver.resolve(
            DecisionType.APPROVE_CREATIVE_ENHANCEMENT,
            user_available=False,
            policy_id="policy_abc",
        )
        assert res.resolved_tier == AuthorityTier.DELEGATED
        assert res.policy_applied == "policy_abc"

    def test_policy_cannot_handle_primary_decisions(self):
        gc = make_contract(
            policy_scope=["confirm_goal_contract"],  # even if listed, policy can't override PRIMARY
            pitl=True
        )
        resolver = AuthorityModelResolver(gc)
        # PRIMARY decisions always require live user; policy scope listing doesn't change that
        assert not resolver.can_policy_handle(DecisionType.CONFIRM_GOAL_CONTRACT)

    def test_system_handles_refinement_loop(self):
        gc = make_contract()
        resolver = AuthorityModelResolver(gc)
        res = resolver.resolve(DecisionType.TRIGGER_REFINEMENT_LOOP, user_available=False)
        assert res.resolved_tier == AuthorityTier.SYSTEM
        assert res.within_system_bounds is True

    def test_system_bounds_exhausted_after_3(self):
        gc = make_contract()
        resolver = AuthorityModelResolver(gc)
        for _ in range(3):
            resolver.resolve(DecisionType.TRIGGER_REFINEMENT_LOOP, user_available=False)
        res = resolver.resolve(DecisionType.TRIGGER_REFINEMENT_LOOP, user_available=True)
        assert res.within_system_bounds is False
        assert res.resolved_tier == AuthorityTier.PRIMARY

    def test_audit_callback_called(self):
        events = []
        def cb(event_type, payload):
            events.append(event_type)

        gc = make_contract()
        resolver = AuthorityModelResolver(gc, audit_callback=cb)
        resolver.resolve(DecisionType.CONFIRM_GOAL_CONTRACT, user_available=True)
        assert any("AUTHORITY" in e for e in events)
