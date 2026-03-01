"""Phase definitions for the 9-phase build pipeline."""

from dataclasses import dataclass
from enum import Enum, auto


class PhaseType(Enum):
    LLM = auto()       # Requires claude -p call
    REGEX = auto()     # Pure Python, no LLM
    EXTERNAL = auto()  # GitHub Actions, no LLM


@dataclass(frozen=True)
class PhaseConfig:
    """Immutable configuration for a pipeline phase."""
    phase_number: int
    name: str
    agent_name: str
    phase_type: PhaseType

    # LLM configuration (ignored for REGEX/EXTERNAL phases)
    model: str = "sonnet"
    max_turns: int = 10
    timeout_seconds: int = 300

    # Tool restrictions
    allowed_tools: tuple[str, ...] = ()     # Empty = all tools allowed
    disallowed_tools: tuple[str, ...] = ()

    # Output parsing
    expects_json: bool = False              # If True, parse output as JSON
    required_json_fields: tuple[str, ...] = ()

    # Human interaction
    requires_user_approval: bool = False

    # System prompt suffix specific to this phase
    system_prompt_suffix: str = ""

    # Prompt template — {anchor}, {prior_output}, {user_prompt} are filled at runtime
    prompt_template: str = ""


# ── Phase 1: Critical Thinking ──────────────────────────────

PHASE_1_CRITICAL_THINKING = PhaseConfig(
    phase_number=1,
    name="Critical Thinking",
    agent_name="Critical Thinking Agent",
    phase_type=PhaseType.LLM,
    model="opus",
    max_turns=1,
    timeout_seconds=120,
    allowed_tools=(),           # No tools — pure reasoning
    disallowed_tools=("Edit", "Write", "Bash", "web_search"),
    expects_json=True,
    required_json_fields=("observations", "assumptions", "questions"),
    requires_user_approval=False,
    system_prompt_suffix=(
        "You are a Critical Thinking Agent. Your job is to find what the user "
        "has NOT said. Surface hidden assumptions, identify ambiguities, and "
        "generate 2-4 clarification questions that will prevent misalignment "
        "in later phases. Do NOT suggest solutions. Do NOT write code. "
        "State confidence levels for each observation."
    ),
    prompt_template=(
        "Analyze this build request and identify what's missing or ambiguous.\n\n"
        "User request:\n{user_prompt}\n\n"
        "Respond with ONLY a JSON object containing:\n"
        '{{\n'
        '  "observations": ["list of things you noticed"],\n'
        '  "assumptions": ["assumptions you are making that the user has not confirmed"],\n'
        '  "questions": ["2-4 clarification questions, ordered by importance"]\n'
        '}}'
    ),
)


# ── Phase 2: Semantic Anchor ────────────────────────────────

PHASE_2_SEMANTIC_ANCHOR = PhaseConfig(
    phase_number=2,
    name="Semantic Anchor",
    agent_name="Semantic Anchor Agent",
    phase_type=PhaseType.LLM,
    model="sonnet",
    max_turns=1,
    timeout_seconds=90,
    allowed_tools=(),
    disallowed_tools=("Edit", "Write", "Bash", "web_search"),
    expects_json=False,         # Output is a paragraph, not JSON
    requires_user_approval=True,
    system_prompt_suffix=(
        "You are the Semantic Anchor Agent. Synthesize the user's request and their "
        "answers to clarification questions into ONE locked paragraph that defines:\n"
        "1. The primary goal (what will be built)\n"
        "2. Success criteria (how we know it's done)\n"
        "3. Non-goals (what we are explicitly NOT building)\n\n"
        "This paragraph becomes the immutable reference point for ALL subsequent agents. "
        "Every decision must be traceable back to this anchor. Be precise and unambiguous. "
        "Do NOT include implementation details — only WHAT, not HOW."
    ),
    prompt_template=(
        "Original request:\n{user_prompt}\n\n"
        "Clarification Q&A:\n{prior_output}\n\n"
        "Write the Semantic Anchor paragraph."
    ),
)


# ── Phase 3: Task Classification ────────────────────────────

PHASE_3_TASK_CLASSIFICATION = PhaseConfig(
    phase_number=3,
    name="Task Classification",
    agent_name="Task Classifier",
    phase_type=PhaseType.REGEX,  # No LLM — pure Python regex
    model="",                     # Not used
)


# ── Phase 4: Research ───────────────────────────────────────

PHASE_4_RESEARCH = PhaseConfig(
    phase_number=4,
    name="Research",
    agent_name="Research Agent",
    phase_type=PhaseType.LLM,
    model="haiku",
    max_turns=5,                  # Multiple turns for web search tool use
    timeout_seconds=180,
    allowed_tools=("web_search", "Read"),
    disallowed_tools=("Edit", "Write", "Bash"),
    expects_json=True,
    required_json_fields=("findings", "tradeoffs", "recommendation"),
    system_prompt_suffix=(
        "You are the Research Agent. Gather factual information relevant to the project. "
        "State confidence levels for each finding. Do NOT claim certainty beyond evidence. "
        "When citing sources, include URLs. When comparing options, present tradeoffs honestly — "
        "do not cherry-pick evidence to support a predetermined conclusion.\n\n"
        "EPISTEMIC CONTAINMENT: If you are uncertain about a fact, say 'I am uncertain about...' "
        "Do not present speculation as established knowledge."
    ),
    prompt_template=(
        "SEMANTIC ANCHOR:\n{anchor}\n\n"
        "Task classification: {prior_output}\n\n"
        "Research this project's requirements. Provide:\n"
        '{{\n'
        '  "findings": [{{"topic": "...", "summary": "...", "confidence": "high|medium|low", "source": "..."}}],\n'
        '  "tradeoffs": [{{"option_a": "...", "option_b": "...", "recommendation": "...", "reason": "..."}}],\n'
        '  "recommendation": "one paragraph summary of recommended approach"\n'
        '}}'
    ),
)


# ── Phase 5: Architecture ───────────────────────────────────

PHASE_5_ARCHITECTURE = PhaseConfig(
    phase_number=5,
    name="Architecture",
    agent_name="Architecture Agent",
    phase_type=PhaseType.LLM,
    model="sonnet",
    max_turns=3,
    timeout_seconds=180,
    allowed_tools=("Read",),      # Can read existing project files if Path B
    disallowed_tools=("Edit", "Write", "Bash"),
    expects_json=True,
    required_json_fields=("components", "data_flow", "file_manifest", "risk_register"),
    requires_user_approval=True,
    system_prompt_suffix=(
        "You are the Architecture Agent. Design the technical architecture for this project. "
        "Every component must trace back to the Semantic Anchor. If a component doesn't serve "
        "the anchor's stated goal, it doesn't belong.\n\n"
        "Output a structured architecture plan. The file_manifest is the definitive list of "
        "files the Code Generation Agent will create — if a file isn't in this list, it won't be built."
    ),
    prompt_template=(
        "SEMANTIC ANCHOR:\n{anchor}\n\n"
        "RESEARCH FINDINGS:\n{prior_output}\n\n"
        "Design the architecture. Respond with JSON:\n"
        '{{\n'
        '  "components": [{{"name": "...", "purpose": "...", "depends_on": ["..."]}}],\n'
        '  "data_flow": "paragraph describing how data moves through the system",\n'
        '  "file_manifest": [{{"path": "src/...", "purpose": "...", "estimated_lines": 50}}],\n'
        '  "tech_stack": {{"language": "...", "framework": "...", "dependencies": ["..."]}},\n'
        '  "risk_register": [{{"risk": "...", "impact": "high|medium|low", "mitigation": "..."}}]\n'
        '}}'
    ),
)


# ── Phase 6: Code Generation ────────────────────────────────

PHASE_6_CODE_GENERATION = PhaseConfig(
    phase_number=6,
    name="Code Generation",
    agent_name="Code Generation Agent",
    phase_type=PhaseType.LLM,
    model="sonnet",
    max_turns=30,                 # Code gen needs many turns for multi-file projects
    timeout_seconds=600,          # 10 minutes — large codebases take time
    allowed_tools=("Read", "Edit", "Write", "Bash"),  # Full code tools
    disallowed_tools=("web_search",),   # Don't research mid-generation
    expects_json=False,           # Output is files on disk, not JSON
    system_prompt_suffix=(
        "You are the Code Generation Agent. Implement the architecture plan exactly as specified. "
        "Create every file in the file_manifest. Write comprehensive tests for all core logic.\n\n"
        "RULES:\n"
        "- Follow the architecture plan precisely — do not add features not in the plan\n"
        "- Every file must include a brief header comment explaining its purpose\n"
        "- Write tests that cover: happy path, edge cases, error handling\n"
        "- Use the project's coding standards from CLAUDE.md if it exists\n"
        "- Do NOT install packages that aren't in the architecture's tech_stack\n"
        "- Run tests after generating code to verify they pass"
    ),
    prompt_template=(
        "SEMANTIC ANCHOR:\n{anchor}\n\n"
        "ARCHITECTURE PLAN:\n{prior_output}\n\n"
        "Implement the complete codebase. Create every file in the file_manifest. "
        "Write tests. Run tests to verify they pass. "
        "After implementation, provide a brief summary of what was created."
    ),
)


# ── Phase 7: Code Review ────────────────────────────────────

PHASE_7_CODE_REVIEW = PhaseConfig(
    phase_number=7,
    name="Code Review",
    agent_name="Code Review Agent",
    phase_type=PhaseType.LLM,
    model="haiku",                # Pass 1: fast scan. Escalates to Sonnet on failure.
    max_turns=5,
    timeout_seconds=180,
    allowed_tools=("Read", "Grep"),
    disallowed_tools=("Edit", "Write", "Bash", "web_search"),
    expects_json=True,
    required_json_fields=("passed", "alignment_score", "issues"),
    system_prompt_suffix=(
        "You are the Code Review Agent. Review the generated code against the Semantic Anchor.\n\n"
        "CONFLICT RESOLUTION: If the code does something the anchor doesn't mention, that's a "
        "misalignment — flag it. If alignment_score < 7, the code MUST be regenerated. "
        "Do NOT reinterpret the anchor to justify existing code.\n\n"
        "Check for:\n"
        "1. Anchor alignment — does every file serve the stated goal?\n"
        "2. Missing functionality — is anything from the architecture plan unimplemented?\n"
        "3. Test coverage — are edge cases tested?\n"
        "4. Security — auth, input validation, SQL injection, XSS\n"
        "5. Code quality — naming, structure, documentation"
    ),
    prompt_template=(
        "SEMANTIC ANCHOR:\n{anchor}\n\n"
        "ARCHITECTURE PLAN:\n{architecture}\n\n"
        "Review the codebase in the workspace. Respond with JSON:\n"
        '{{\n'
        '  "passed": true/false,\n'
        '  "alignment_score": 1-10,\n'
        '  "issues": [\n'
        '    {{"severity": "critical|warning|info", "file": "...", "line": 0, "description": "...", "suggestion": "..."}}\n'
        '  ],\n'
        '  "missing_from_plan": ["files or features in architecture but not implemented"],\n'
        '  "security_findings": ["any security concerns"]\n'
        '}}'
    ),
)


# ── Phase 8: CI/CD Gates ────────────────────────────────────

PHASE_8_CICD = PhaseConfig(
    phase_number=8,
    name="CI/CD Gates",
    agent_name="CI/CD Pipeline",
    phase_type=PhaseType.EXTERNAL,  # GitHub Actions, not LLM
    model="",
)


# ── Phase 9: Documentation ──────────────────────────────────

PHASE_9_DOCUMENTATION = PhaseConfig(
    phase_number=9,
    name="Documentation",
    agent_name="Documentation Agent",
    phase_type=PhaseType.LLM,
    model="haiku",
    max_turns=3,
    timeout_seconds=120,
    allowed_tools=("Read",),
    disallowed_tools=("Edit", "Write", "Bash", "web_search"),
    expects_json=False,
    system_prompt_suffix=(
        "You are the Documentation Agent. Generate clear, concise documentation "
        "for the project. Audience: a developer who has never seen this codebase.\n\n"
        "Include: What this project does, How to set it up, How to use it, "
        "File-by-file description, Known limitations, Architecture decisions."
    ),
    prompt_template=(
        "SEMANTIC ANCHOR:\n{anchor}\n\n"
        "ARCHITECTURE:\n{architecture}\n\n"
        "Read the codebase in the workspace and generate a README.md covering: "
        "What, Setup, Usage, File descriptions, Limitations, Architecture decisions."
    ),
)


# ── Ordered phase list ──────────────────────────────────────

ALL_PHASES = [
    PHASE_1_CRITICAL_THINKING,
    PHASE_2_SEMANTIC_ANCHOR,
    PHASE_3_TASK_CLASSIFICATION,
    PHASE_4_RESEARCH,
    PHASE_5_ARCHITECTURE,
    PHASE_6_CODE_GENERATION,
    PHASE_7_CODE_REVIEW,
    PHASE_8_CICD,
    PHASE_9_DOCUMENTATION,
]
