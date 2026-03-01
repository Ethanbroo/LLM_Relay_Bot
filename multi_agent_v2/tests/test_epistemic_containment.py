"""Tests for the Epistemic Containment Layer."""

import pytest
from multi_agent_v2.goal_contract import (
    GoalContract, AuthorityModel, ConflictResolution, RiskTolerance, OnTimeout, ValidationTier
)
from multi_agent_v2.epistemic_containment import (
    EpistemicContainmentLayer, OptionEntry, ContainmentStrictness, OptionFramingAuditTrail
)


def make_contract(success_criteria=None):
    return GoalContract(
        objective="Test task",
        semantic_intent_anchor="Test anchor.",
        success_criteria=success_criteria or ["output is correct", "output is fast"],
        non_goals=["scope creep"],
        risk_tolerance=RiskTolerance.LOW,
        authority_model=AuthorityModel(primary_authority="user_1"),
        conflict_resolution=ConflictResolution(24, OnTimeout.HALT),
        validation_tier_minimum=ValidationTier.STRUCTURAL,
    )


def make_option(content: str, label: str = None, excluded: bool = False, inferred_excl: bool = False):
    return OptionEntry(
        option_id="opt_" + content[:8].replace(" ", "_"),
        content=content,
        proposed_by="test_agent",
        excluded=excluded,
        exclusion_reason="system heuristic" if inferred_excl else None,
        exclusion_is_system_inference=inferred_excl,
        presentation_label=label,
    )


class TestECLSuperlativeStripping:
    def test_strips_best_in_strict_mode(self):
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.STRICT)
        opt = make_option("This is the best approach for the task")
        result = ecl.apply([opt])
        assert "best" not in result.options[0].content
        assert "[criterion not specified]" in result.options[0].content

    def test_retains_term_if_grounded_in_criteria(self):
        gc = make_contract(success_criteria=["output is the best possible quality"])
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.STRICT)
        opt = make_option("This is the best approach.")
        result = ecl.apply([opt])
        # "best" appears in success criteria, so it should be retained
        assert "best" in result.options[0].content

    def test_no_modification_in_minimal_mode(self):
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.MINIMAL)
        opt = make_option("The optimal solution is X.")
        result = ecl.apply([opt])
        # Minimal mode: no modification
        assert "optimal" in result.options[0].content

    def test_flags_superlatives_in_soft_authorship(self):
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.STRICT)
        opt = make_option("The recommended approach is clearly the best.")
        result = ecl.apply([opt])
        assert len(result.soft_authorship_flags) > 0


class TestECLOrdering:
    def test_ungrounded_criterion_randomizes(self):
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.STRICT)
        opts = [make_option(f"Option {i}") for i in range(5)]
        result = ecl.apply(opts, ordering_criterion="cost efficiency")  # not in criteria
        assert result.ordering_rationale is None
        assert any("Randomizing" in f or "not grounded" in f for f in result.soft_authorship_flags)

    def test_grounded_criterion_sets_rationale(self):
        gc = make_contract(success_criteria=["output is correct", "output is fast"])
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.STRICT)
        opts = [make_option("Option A"), make_option("Option B")]
        result = ecl.apply(opts, ordering_criterion="output is fast")
        assert result.ordering_rationale is not None
        assert "output is fast" in result.ordering_rationale


class TestECLExclusionDisclosure:
    def test_system_inferred_exclusion_flagged(self):
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.STRICT)
        excluded = make_option("Excluded option", excluded=True, inferred_excl=True)
        active = make_option("Active option")
        result = ecl.apply([active, excluded])
        assert len(result.disclosed_exclusions) == 1
        assert result.disclosed_exclusions[0].exclusion_is_system_inference is True
        assert any("system inference" in f for f in result.soft_authorship_flags)

    def test_binary_choice_flagged(self):
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc, strictness=ContainmentStrictness.STRICT)
        opts = [make_option("Option A"), make_option("Option B")]
        result = ecl.apply(opts)
        assert any("binary" in f.lower() or "2 options" in f for f in result.soft_authorship_flags)


class TestAuditTrail:
    def test_audit_trail_logged_before_presentation(self):
        logged = []
        trail = OptionFramingAuditTrail(audit_callback=lambda t, p: logged.append(t))
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc, audit_trail=trail)
        ecl.apply([make_option("Option A")])
        assert "OPTION_FRAMING_LOGGED" in logged

    def test_framing_id_in_result(self):
        gc = make_contract()
        ecl = EpistemicContainmentLayer(gc)
        result = ecl.apply([make_option("Option A")])
        assert result.framing_id
        assert len(result.framing_id) > 0
