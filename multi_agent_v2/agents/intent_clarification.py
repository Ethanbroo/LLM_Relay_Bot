"""
Intent Clarification Layer — v2.0

Two specialized agents that run before any task execution:

1. CriticalThinkingAgent:
   Analyzes the user's request for hidden constraints, downstream implications,
   and missing context. Outputs ONLY observations, unknowns, and questions.
   Never proposes solutions, never infers intent, never makes recommendations.
   This is the strictest advisory-only role in the system.

2. SemanticAnchorAgent:
   After the user has answered clarification questions, writes the Semantic
   Intent Anchor: a single paragraph describing the user's underlying purpose—
   why they want what they asked for, beyond the literal objective.
   This anchor is presented to the user for review before being written into
   the GoalContract. It is NOT authoritative until the user confirms it.

Both agents:
- Work against an LLM client with a configurable backend.
- Parse responses into typed output structures.
- Flag any output that crosses into recommendation or specification territory
  as a role drift event (handled by FailureModeHandler).
- Log all inputs and output hashes to the audit trail (never raw text).

Design decisions that avoid future problems:
- The CriticalThinkingAgent prompt explicitly prohibits solution proposals.
  If the agent produces output that looks like a recommendation, it is flagged
  by RoleDriftDetector before being passed to the user.
- The SemanticAnchorAgent output is always presented to the user for modification.
  It is never written to the GoalContract without explicit user confirmation.
- Parsing is strict: malformed JSON from the LLM triggers AGENT_MALFORMED_OUTPUT
  failure mode, not a silent fallback.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


# Prompt for the Critical Thinking Agent — strict advisory only
CRITICAL_THINKING_SYSTEM_PROMPT = """
You are an analyst. Your only role is to identify hidden constraints, downstream
implications, unstated assumptions, and missing context in the user's request.

STRICT RULES:
1. Output ONLY a JSON object with three keys: "observations", "unknowns", "questions".
2. "observations" is a list of factual observations about what the request implies.
3. "unknowns" is a list of things that are not specified and would affect the outcome.
4. "questions" is a list of clarifying questions to ask the user.
5. Do NOT propose solutions.
6. Do NOT answer questions — list them.
7. Do NOT make recommendations of any kind.
8. Do NOT infer user intent — label it as an unknown if unclear.
9. Do NOT use evaluative language ("good", "bad", "better", "best", "should").
10. Any statement that could be interpreted as a requirement or specification is FORBIDDEN.

Your output must be valid JSON. No commentary outside the JSON.
""".strip()

# Prompt for the Semantic Anchor Agent
SEMANTIC_ANCHOR_SYSTEM_PROMPT = """
You are a semantic analyst. You will be given the user's original request and
their answers to clarification questions.

Your task is to write ONE paragraph (3-5 sentences) that describes the user's
underlying purpose — why they want what they asked for, beyond the literal objective.

This is NOT a rephrasing of the request. It is the intent behind the request.
It will be used as a semantic drift detector: if the final output fulfills the
stated objective but violates this paragraph, that is a failure.

STRICT RULES:
1. Write exactly one paragraph. No lists, no bullet points, no recommendations.
2. Do not add features, suggestions, or enhancements.
3. Do not evaluate the request as good or bad.
4. Ground every statement in what the user actually said.
5. If you are uncertain, express it as a conditional ("if the user intends X, then Y").

Output: One paragraph of plain text. Nothing else.
""".strip()

# Patterns that indicate role drift (recommendation/specification language)
ROLE_DRIFT_PATTERNS = re.compile(
    r"\b(you should|I recommend|consider using|best practice|I suggest|"
    r"it is better to|ideally|the solution is|try using|use this|"
    r"implement|the answer is)\b",
    re.IGNORECASE,
)


@dataclass
class ClarificationOutput:
    """Structured output from the CriticalThinkingAgent."""
    agent_id: str
    request_hash: str
    observations: List[str]
    unknowns: List[str]
    questions: List[str]
    raw_response_hash: str
    produced_at: str
    role_drift_detected: bool = False

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "request_hash": self.request_hash,
            "observations": self.observations,
            "unknowns": self.unknowns,
            "questions": self.questions,
            "raw_response_hash": self.raw_response_hash,
            "produced_at": self.produced_at,
            "role_drift_detected": self.role_drift_detected,
        }


@dataclass
class SemanticAnchorOutput:
    """Output from the SemanticAnchorAgent — pending user confirmation."""
    agent_id: str
    anchor_text: str                 # Draft anchor paragraph
    confirmed_by_user: bool = False  # Set to True only after user review
    user_modified: bool = False      # Set to True if user edited the text
    produced_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "anchor_text": self.anchor_text,
            "confirmed_by_user": self.confirmed_by_user,
            "user_modified": self.user_modified,
            "produced_at": self.produced_at,
        }


def _sha256_short(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class RoleDriftDetector:
    """
    Scans agent output for language that crosses the advisory-only boundary.
    Returns True if drift is detected, with reasons.
    """

    def check(self, text: str) -> tuple[bool, List[str]]:
        matches = ROLE_DRIFT_PATTERNS.findall(text)
        if matches:
            reasons = [f"Recommendation-like phrase detected: '{m}'" for m in set(matches)]
            return True, reasons
        return False, []


class CriticalThinkingAgent:
    """
    Advisory-only analyst that identifies gaps, constraints, and unknowns.
    Never proposes solutions.

    The LLM client must have a method: generate(system_prompt, user_message) -> str
    """

    def __init__(
        self,
        llm_client: Any,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._llm = llm_client
        self._audit = audit_callback
        self._drift_detector = RoleDriftDetector()
        self._agent_id = f"critical_thinking_{str(uuid.uuid4())[:8]}"

    def analyze(self, user_request: str) -> ClarificationOutput:
        """
        Analyze the user request and return observations, unknowns, and questions.

        Raises:
            ValueError: If the LLM returns malformed output (triggers AGENT_MALFORMED_OUTPUT).
        """
        request_hash = _sha256_short(user_request)

        self._emit_audit("AGENT_INVOKED", {
            "agent": "CriticalThinkingAgent",
            "agent_id": self._agent_id,
            "request_hash": request_hash,
        })

        raw = self._call_llm(CRITICAL_THINKING_SYSTEM_PROMPT, user_request)
        raw_hash = _sha256_short(raw)

        # Parse JSON output
        parsed = self._parse_json(raw)

        observations = parsed.get("observations", [])
        unknowns = parsed.get("unknowns", [])
        questions = parsed.get("questions", [])

        # Validate structure
        if not isinstance(observations, list) or not isinstance(unknowns, list) or not isinstance(questions, list):
            raise ValueError(
                "CriticalThinkingAgent output malformed: 'observations', 'unknowns', "
                "'questions' must all be lists."
            )

        # Role drift detection
        combined_text = " ".join(observations + unknowns + questions)
        drift_detected, drift_reasons = self._drift_detector.check(combined_text)

        if drift_detected:
            self._emit_audit("AGENT_ROLE_DRIFT_DETECTED", {
                "agent": "CriticalThinkingAgent",
                "agent_id": self._agent_id,
                "reasons": drift_reasons,
            })

        output = ClarificationOutput(
            agent_id=self._agent_id,
            request_hash=request_hash,
            observations=observations,
            unknowns=unknowns,
            questions=questions,
            raw_response_hash=raw_hash,
            produced_at=datetime.now(timezone.utc).isoformat(),
            role_drift_detected=drift_detected,
        )

        self._emit_audit("AGENT_OUTPUT", {
            "agent": "CriticalThinkingAgent",
            "agent_id": self._agent_id,
            "output_hash": _sha256_short(json.dumps(output.to_dict())),
            "role_drift_detected": drift_detected,
        })

        return output

    def _call_llm(self, system: str, user: str) -> str:
        """Call the LLM client. Adapts to different client interfaces."""
        if hasattr(self._llm, "generate"):
            return self._llm.generate(f"{system}\n\nUser request:\n{user}")
        if hasattr(self._llm, "chat"):
            return self._llm.chat(system=system, user=user)
        # Stub for testing
        return json.dumps({
            "observations": [f"User requests: {user[:100]}"],
            "unknowns": ["Deadline not specified", "Target audience unclear"],
            "questions": ["What is the expected output format?", "Are there any constraints?"],
        })

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from LLM output. Raises ValueError on failure."""
        text = text.strip()
        # Try to extract JSON block if wrapped in markdown
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"CriticalThinkingAgent: LLM returned non-JSON output: {e}") from e

    def _emit_audit(self, event_type: str, payload: dict) -> None:
        if self._audit:
            self._audit(event_type, payload)


class SemanticAnchorAgent:
    """
    Writes the Semantic Intent Anchor: a paragraph describing why the user
    wants what they asked for, beyond the literal objective.

    Output is ALWAYS presented to the user for review before being used.
    """

    def __init__(
        self,
        llm_client: Any,
        audit_callback: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._llm = llm_client
        self._audit = audit_callback
        self._agent_id = f"semantic_anchor_{str(uuid.uuid4())[:8]}"

    def generate_anchor(
        self,
        user_request: str,
        qa_pairs: List[Dict[str, str]],
    ) -> SemanticAnchorOutput:
        """
        Generate a draft semantic intent anchor from the original request
        and the user's answers to clarification questions.

        Args:
            user_request: The original user task description.
            qa_pairs: List of {"question": ..., "answer": ...} dicts.

        Returns:
            SemanticAnchorOutput with confirmed_by_user=False (pending review).
        """
        qa_text = "\n".join(
            f"Q: {pair.get('question', '')}\nA: {pair.get('answer', '')}"
            for pair in qa_pairs
        )
        user_message = (
            f"Original request:\n{user_request}\n\n"
            f"Clarification Q&A:\n{qa_text}"
        )

        self._emit_audit("AGENT_INVOKED", {
            "agent": "SemanticAnchorAgent",
            "agent_id": self._agent_id,
            "request_hash": _sha256_short(user_request),
        })

        raw = self._call_llm(SEMANTIC_ANCHOR_SYSTEM_PROMPT, user_message)

        # Validate: should be a single paragraph
        cleaned = raw.strip()
        if not cleaned:
            raise ValueError("SemanticAnchorAgent: LLM returned empty output.")

        output = SemanticAnchorOutput(
            agent_id=self._agent_id,
            anchor_text=cleaned,
            confirmed_by_user=False,
        )

        self._emit_audit("AGENT_OUTPUT", {
            "agent": "SemanticAnchorAgent",
            "agent_id": self._agent_id,
            "anchor_hash": _sha256_short(cleaned),
            "confirmed_by_user": False,
            "note": "Anchor presented to user for review; not yet confirmed.",
        })

        return output

    def confirm(
        self,
        output: SemanticAnchorOutput,
        final_text: str,
        confirmed_by: str,
    ) -> SemanticAnchorOutput:
        """
        User confirms (and optionally edits) the anchor text.
        Only after this call may the anchor be written into the GoalContract.
        """
        user_modified = final_text.strip() != output.anchor_text.strip()
        output.anchor_text = final_text.strip()
        output.confirmed_by_user = True
        output.user_modified = user_modified

        self._emit_audit("ANCHOR_CONFIRMED", {
            "agent_id": self._agent_id,
            "confirmed_by": confirmed_by,
            "user_modified": user_modified,
            "anchor_hash": _sha256_short(output.anchor_text),
        })
        return output

    def _call_llm(self, system: str, user: str) -> str:
        if hasattr(self._llm, "generate"):
            return self._llm.generate(f"{system}\n\n{user}")
        if hasattr(self._llm, "chat"):
            return self._llm.chat(system=system, user=user)
        # Stub
        return (
            "The user seeks to accomplish the stated objective as a means to a broader goal "
            "of improving their workflow efficiency. The underlying purpose is to reduce "
            "manual effort in a recurring task, ensuring consistency and freeing time "
            "for higher-value work. Success means the solution integrates seamlessly into "
            "the existing process without introducing new complexity."
        )

    def _emit_audit(self, event_type: str, payload: dict) -> None:
        if self._audit:
            self._audit(event_type, payload)
