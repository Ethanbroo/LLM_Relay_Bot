"""Tests for GoalContract v2.0."""

import pytest
from multi_agent_v2.goal_contract import (
    GoalContract, AuthorityModel, ConflictResolution, GoalContractConstraints,
    RiskTolerance, OnTimeout, ValidationTier
)


def make_contract(**overrides):
    """Factory for a valid GoalContract with sensible defaults."""
    defaults = dict(
        objective="Build a data pipeline",
        semantic_intent_anchor="The user wants to automate data transformation.",
        success_criteria=["All rows converted", "No data loss"],
        non_goals=["Schema inference"],
        risk_tolerance=RiskTolerance.LOW,
        authority_model=AuthorityModel(
            primary_authority="user_1",
            policy_owner=None,
            policy_scope=[],
            policy_in_loop_acknowledged=False,
        ),
        conflict_resolution=ConflictResolution(timeout_hours=24, on_timeout=OnTimeout.HALT),
        validation_tier_minimum=ValidationTier.REAL,
    )
    defaults.update(overrides)
    return GoalContract(**defaults)


class TestGoalContractConstruction:
    def test_valid_contract_creates_id(self):
        gc = make_contract()
        assert len(gc.contract_id) == 64  # SHA-256 hex

    def test_id_is_deterministic(self):
        gc1 = make_contract()
        gc2 = make_contract()
        assert gc1.contract_id == gc2.contract_id

    def test_id_changes_with_objective(self):
        gc1 = make_contract(objective="Task A")
        gc2 = make_contract(objective="Task B")
        assert gc1.contract_id != gc2.contract_id

    def test_not_confirmed_initially(self):
        gc = make_contract()
        assert not gc.is_confirmed
        assert gc.confirmed_at is None
        assert gc.confirmed_by is None

    def test_policy_scope_without_ack_raises(self):
        with pytest.raises(ValueError, match="policy_in_loop_acknowledged"):
            AuthorityModel(
                primary_authority="user_1",
                policy_scope=["approve_creative_enhancement"],
                policy_in_loop_acknowledged=False,
            ).validate()

    def test_ack_without_scope_raises(self):
        with pytest.raises(ValueError, match="policy_scope is empty"):
            AuthorityModel(
                primary_authority="user_1",
                policy_scope=[],
                policy_in_loop_acknowledged=True,
            ).validate()


class TestGoalContractConfirmation:
    def test_confirmation_sets_fields(self):
        gc = make_contract()
        gc.confirm("user_1")
        assert gc.is_confirmed
        assert gc.confirmed_by == "user_1"
        assert gc.confirmed_at is not None

    def test_double_confirmation_raises(self):
        gc = make_contract()
        gc.confirm("user_1")
        with pytest.raises(RuntimeError, match="already confirmed"):
            gc.confirm("user_1")

    def test_serialization_round_trip(self):
        gc = make_contract()
        gc.confirm("user_1")
        d = gc.to_dict()
        gc2 = GoalContract.from_dict(d)
        assert gc2.objective == gc.objective
        assert gc2.contract_id == gc.contract_id
        assert gc2.confirmed_by == "user_1"
        assert gc2.is_confirmed


class TestGoalContractSerialization:
    def test_to_dict_has_required_keys(self):
        gc = make_contract()
        d = gc.to_dict()
        for key in ["contract_id", "objective", "semantic_intent_anchor",
                    "success_criteria", "non_goals", "authority_model",
                    "conflict_resolution", "validation_tier_minimum"]:
            assert key in d

    def test_from_dict_preserves_validation_tier(self):
        gc = make_contract(validation_tier_minimum=ValidationTier.STRUCTURAL)
        d = gc.to_dict()
        gc2 = GoalContract.from_dict(d)
        assert gc2.validation_tier_minimum == ValidationTier.STRUCTURAL
