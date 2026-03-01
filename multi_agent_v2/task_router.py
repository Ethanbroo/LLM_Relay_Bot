"""
Task Router for multi_agent_v2.

Determines which agent pipeline to run based on the user's confirmed task.
Returns an ordered list of phase names the session will execute.

Task types:
  RESEARCH      — "find out about X", "what is X", "explain X"
  PLANNING      — "design X", "architect X", "plan X"
  IMPLEMENTATION — "build X", "create X", "write code for X", "implement X"
  HYBRID        — anything that combines research + implementation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class TaskType(str, Enum):
    RESEARCH = "research"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    HYBRID = "hybrid"


@dataclass
class TaskRoute:
    task_type: TaskType
    phases: List[str]
    rationale: str
    requires_google_doc: bool = False

    def __str__(self) -> str:
        return (
            f"[{self.task_type.value.upper()}] "
            f"Phases: {' → '.join(self.phases)}"
        )


# ---------------------------------------------------------------------------
# Keyword patterns for classification
# ---------------------------------------------------------------------------

_RESEARCH_PATTERNS = re.compile(
    r"\b(research|find out|what is|explain|describe|summarize|overview|"
    r"how does|how do|tell me about|compare|survey|review|analyze|analyse)\b",
    re.IGNORECASE,
)

_IMPLEMENTATION_PATTERNS = re.compile(
    r"\b(build|create|write|implement|code|develop|make|add|generate|"
    r"set up|setup|scaffold|deploy|integrate|fix|refactor|migrate)\b",
    re.IGNORECASE,
)

_PLANNING_PATTERNS = re.compile(
    r"\b(design|architect|plan|outline|spec|specification|roadmap|"
    r"strategy|approach|structure|organize|blueprint)\b",
    re.IGNORECASE,
)

_DOC_PATTERNS = re.compile(
    r"\b(google doc|document|summary|report|write up|write-up)\b",
    re.IGNORECASE,
)


def classify_task(user_idea: str, semantic_anchor: str = "") -> TaskRoute:
    """
    Classify the user's task and return the appropriate agent pipeline.

    Args:
        user_idea: The raw idea string from the user.
        semantic_anchor: Optionally the confirmed anchor paragraph.

    Returns:
        TaskRoute with ordered list of phases to run.
    """
    text = f"{user_idea} {semantic_anchor}".strip()

    has_research = bool(_RESEARCH_PATTERNS.search(text))
    has_impl = bool(_IMPLEMENTATION_PATTERNS.search(text))
    has_planning = bool(_PLANNING_PATTERNS.search(text))
    wants_doc = bool(_DOC_PATTERNS.search(text))

    # Determine type
    if has_impl and (has_research or has_planning):
        task_type = TaskType.HYBRID
        phases = [
            "intent_clarification",
            "research",
            "architecture",
            "code_generation",
            "code_review",
            "summary",
        ]
        rationale = (
            "Task requires both research/planning and implementation. "
            "Running full pipeline: clarify → research → architect → code → review → summarize."
        )
        requires_doc = True

    elif has_impl:
        task_type = TaskType.IMPLEMENTATION
        phases = [
            "intent_clarification",
            "architecture",
            "code_generation",
            "code_review",
            "summary",
        ]
        rationale = (
            "Task is primarily implementation-focused. "
            "Running: clarify → architect → code → review → summarize."
        )
        requires_doc = True

    elif has_planning:
        task_type = TaskType.PLANNING
        phases = [
            "intent_clarification",
            "research",
            "architecture",
            "summary",
        ]
        rationale = (
            "Task is design/planning-focused. "
            "Running: clarify → research → architect → summarize."
        )
        requires_doc = wants_doc

    elif has_research:
        task_type = TaskType.RESEARCH
        phases = [
            "intent_clarification",
            "research",
            "summary",
        ]
        rationale = (
            "Task is research-focused. "
            "Running: clarify → research → summarize."
        )
        requires_doc = wants_doc

    else:
        # Default: treat as hybrid if we cannot classify clearly
        task_type = TaskType.HYBRID
        phases = [
            "intent_clarification",
            "research",
            "architecture",
            "code_generation",
            "code_review",
            "summary",
        ]
        rationale = (
            "Could not classify task clearly. Defaulting to full pipeline."
        )
        requires_doc = True

    return TaskRoute(
        task_type=task_type,
        phases=phases,
        rationale=rationale,
        requires_google_doc=requires_doc,
    )


# ---------------------------------------------------------------------------
# Phase labels for display
# ---------------------------------------------------------------------------

PHASE_LABELS = {
    "intent_clarification": "Phase 1 — Intent Clarification",
    "research":             "Phase 2 — Research",
    "architecture":         "Phase 3a — Architecture Planning",
    "code_generation":      "Phase 3b — Code Generation",
    "code_review":          "Phase 3c — Code Review",
    "summary":              "Phase 4 — Summary",
}


def phase_label(phase_key: str) -> str:
    return PHASE_LABELS.get(phase_key, phase_key)
