"""Tests for Intent Clarification Layer agents."""

import json
import pytest
from multi_agent_v2.agents.intent_clarification import (
    CriticalThinkingAgent, SemanticAnchorAgent,
    ClarificationOutput, SemanticAnchorOutput, RoleDriftDetector
)


class PassingStubLLM:
    """Returns valid structured output for both agents."""

    def generate(self, prompt: str) -> str:
        if "observations" in prompt or "analyst" in prompt.lower():
            return json.dumps({
                "observations": ["The request involves file transformation."],
                "unknowns": ["Output format unspecified."],
                "questions": ["What output format is expected?"],
            })
        return (
            "The user seeks to automate a recurring file transformation to reduce manual effort. "
            "The underlying purpose is workflow efficiency and consistency."
        )


class DriftingStubLLM:
    """Returns output with recommendation/drift language."""

    def generate(self, prompt: str) -> str:
        return json.dumps({
            "observations": ["This is a transformation task."],
            "unknowns": [],
            "questions": ["I recommend using pandas for this. Should you use it?"],
        })


class MalformedStubLLM:
    """Returns non-JSON output for the CriticalThinkingAgent."""

    def generate(self, prompt: str) -> str:
        return "Here are my thoughts: observations are X, unknowns are Y."


class TestRoleDriftDetector:
    def test_detects_recommendation_language(self):
        detector = RoleDriftDetector()
        drift, reasons = detector.check("You should use pandas for this.")
        assert drift is True
        assert len(reasons) > 0

    def test_no_drift_in_neutral_text(self):
        detector = RoleDriftDetector()
        drift, reasons = detector.check("The request involves file transformation.")
        assert drift is False

    def test_case_insensitive(self):
        detector = RoleDriftDetector()
        drift, _ = detector.check("I SUGGEST using a different approach.")
        assert drift is True


class TestCriticalThinkingAgent:
    def test_returns_clarification_output(self):
        agent = CriticalThinkingAgent(PassingStubLLM())
        output = agent.analyze("Transform CSV files to JSON.")
        assert isinstance(output, ClarificationOutput)
        assert len(output.observations) > 0
        assert len(output.questions) > 0

    def test_detects_drift_in_output(self):
        agent = CriticalThinkingAgent(DriftingStubLLM())
        output = agent.analyze("Transform CSV files to JSON.")
        assert output.role_drift_detected is True

    def test_malformed_output_raises_value_error(self):
        agent = CriticalThinkingAgent(MalformedStubLLM())
        with pytest.raises(ValueError, match="non-JSON"):
            agent.analyze("Transform CSV files.")

    def test_audit_callback_fired(self):
        events = []
        agent = CriticalThinkingAgent(PassingStubLLM(), audit_callback=lambda t, p: events.append(t))
        agent.analyze("Do something.")
        assert "AGENT_INVOKED" in events
        assert "AGENT_OUTPUT" in events

    def test_no_raw_text_in_audit(self):
        payloads = []
        agent = CriticalThinkingAgent(PassingStubLLM(), audit_callback=lambda t, p: payloads.append(p))
        agent.analyze("Do something.")
        # Audit payloads should never contain raw text — only hashes
        for payload in payloads:
            for key, value in payload.items():
                if "hash" in key.lower():
                    assert isinstance(value, str)
                    assert len(value) <= 32  # Short hash


class TestSemanticAnchorAgent:
    def test_generates_draft_anchor(self):
        agent = SemanticAnchorAgent(PassingStubLLM())
        qa = [{"question": "What format?", "answer": "JSON"}]
        output = agent.generate_anchor("Transform files.", qa)
        assert isinstance(output, SemanticAnchorOutput)
        assert not output.confirmed_by_user
        assert len(output.anchor_text) > 0

    def test_confirm_sets_confirmed_flag(self):
        agent = SemanticAnchorAgent(PassingStubLLM())
        draft = agent.generate_anchor("Task", [])
        confirmed = agent.confirm(draft, draft.anchor_text, "user_1")
        assert confirmed.confirmed_by_user is True
        assert confirmed.user_modified is False

    def test_user_modified_detected(self):
        agent = SemanticAnchorAgent(PassingStubLLM())
        draft = agent.generate_anchor("Task", [])
        confirmed = agent.confirm(draft, "Different text that user wrote.", "user_1")
        assert confirmed.user_modified is True
        assert confirmed.anchor_text == "Different text that user wrote."

    def test_anchor_not_usable_before_confirmation(self):
        agent = SemanticAnchorAgent(PassingStubLLM())
        output = agent.generate_anchor("Task", [])
        assert output.confirmed_by_user is False
        # The caller should check this before writing to GoalContract
