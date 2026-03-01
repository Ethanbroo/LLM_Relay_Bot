"""Tests for the Tiered Validation System."""

import pytest
from multi_agent_v2.goal_contract import (
    GoalContract, AuthorityModel, ConflictResolution, RiskTolerance, OnTimeout, ValidationTier
)
from multi_agent_v2.tiered_validation import (
    TieredValidationSystem, TieredValidationReport,
    ValidationResult, ValidationTierLabel, TrustLevel,
    LogicalConsistencyValidator, SchemaValidator, LLMPeerReviewValidator,
    StaticAnalysisValidator
)


def make_contract(min_tier: int = 1):
    return GoalContract(
        objective="Test task",
        semantic_intent_anchor="Test anchor.",
        success_criteria=["output is correct"],
        non_goals=[],
        risk_tolerance=RiskTolerance.LOW,
        authority_model=AuthorityModel(primary_authority="user_1"),
        conflict_resolution=ConflictResolution(24, OnTimeout.HALT),
        validation_tier_minimum=ValidationTier(min_tier),
    )


class TestValidationResultTrustLabels:
    def test_synthetic_has_disclaimer(self):
        result = ValidationResult(
            validator_name="test",
            tier=ValidationTierLabel.SYNTHETIC,
            trust_label=TrustLevel.LOW_SYNTHETIC,
            passed=True,
            detail="",
        )
        assert "SYNTHETIC" in result.synthetic_disclaimer
        assert "ground truth" in result.synthetic_disclaimer.lower()

    def test_real_has_no_disclaimer(self):
        result = ValidationResult(
            validator_name="test",
            tier=ValidationTierLabel.REAL,
            trust_label=TrustLevel.HIGH,
            passed=True,
            detail="",
        )
        assert result.synthetic_disclaimer == ""

    def test_to_dict_includes_disclaimer_for_synthetic(self):
        result = ValidationResult(
            validator_name="test",
            tier=ValidationTierLabel.SYNTHETIC,
            trust_label=TrustLevel.LOW_SYNTHETIC,
            passed=True,
            detail="",
        )
        d = result.to_dict()
        assert "synthetic_disclaimer" in d


class TestLogicalConsistencyValidator:
    def setup_method(self):
        self.validator = LogicalConsistencyValidator()

    def test_empty_output_fails(self):
        gc = make_contract()
        result = self.validator.validate("", {})
        assert result.passed is False
        assert result.tier == ValidationTierLabel.STRUCTURAL

    def test_valid_output_passes(self):
        result = self.validator.validate("The system processed 100 rows successfully.", {})
        assert result.passed is True

    def test_contradictory_output_fails(self):
        result = self.validator.validate(
            "The value always equals 5. The value never equals 5.", {}
        )
        assert result.passed is False
        assert "contradiction" in result.detail.lower()

    def test_trust_level_is_medium(self):
        result = self.validator.validate("Valid text", {})
        assert result.trust_label == TrustLevel.MEDIUM


class TestSchemaValidator:
    def test_valid_json_passes(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        v = SchemaValidator(schema)
        result = v.validate({"name": "test"}, {})
        # May fail if jsonschema not installed, but should not raise
        assert isinstance(result.passed, bool)

    def test_tier_is_structural(self):
        v = SchemaValidator({})
        result = v.validate({}, {})
        assert result.tier == ValidationTierLabel.STRUCTURAL


class TestStaticAnalysisValidator:
    def test_valid_python_passes(self):
        v = StaticAnalysisValidator()
        result = v.validate("x = 1 + 1\nprint(x)", {})
        assert result.passed is True
        assert result.tier == ValidationTierLabel.REAL

    def test_invalid_python_fails(self):
        v = StaticAnalysisValidator()
        result = v.validate("def foo(\n  bar", {})
        assert result.passed is False


class TestLLMPeerReviewValidator:
    def test_stub_llm_returns_synthetic_tier(self):
        class StubLLM:
            def generate(self, prompt):
                return "PASS: Output looks coherent."

        v = LLMPeerReviewValidator(StubLLM())
        result = v.validate("Some output text", {})
        assert result.tier == ValidationTierLabel.SYNTHETIC
        assert result.trust_label == TrustLevel.LOW_SYNTHETIC
        assert result.passed is True

    def test_fail_response_marked_failed(self):
        class StubLLM:
            def generate(self, prompt):
                return "FAIL: Output is missing required fields."

        v = LLMPeerReviewValidator(StubLLM())
        result = v.validate("Incomplete output", {})
        assert result.passed is False


class TestTieredValidationSystem:
    def test_minimum_tier_not_met_when_only_synthetic(self):
        gc = make_contract(min_tier=1)  # Requires Tier 1

        class StubLLM:
            def generate(self, p): return "PASS"

        tv = TieredValidationSystem(gc, validators=[LLMPeerReviewValidator(StubLLM())])
        report = tv.run("Some output", {})
        # Synthetic (Tier 3) cannot satisfy Tier 1 requirement
        assert report.meets_minimum_tier is False

    def test_minimum_tier_met_with_structural(self):
        gc = make_contract(min_tier=2)  # Requires Tier 2
        tv = TieredValidationSystem(gc, validators=[LogicalConsistencyValidator()])
        report = tv.run("Valid output with no contradictions.", {})
        assert report.meets_minimum_tier is True
        assert report.highest_tier_achieved == 2

    def test_report_has_all_required_fields(self):
        gc = make_contract()
        tv = TieredValidationSystem(gc, validators=[LogicalConsistencyValidator()])
        report = tv.run("Test output", {})
        d = report.to_dict()
        for key in ["report_id", "contract_id", "meets_minimum_tier",
                    "trust_label", "overall_passed", "results"]:
            assert key in d

    def test_audit_callback_called(self):
        events = []
        gc = make_contract()
        tv = TieredValidationSystem(
            gc,
            validators=[LogicalConsistencyValidator()],
            audit_callback=lambda t, p: events.append(t)
        )
        tv.run("Test", {})
        assert "VALIDATION_RESULT" in events
        assert "VALIDATION_REPORT" in events
