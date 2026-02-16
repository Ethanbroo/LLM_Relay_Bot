"""Prompt construction for Phase 6.

Phase 6 Invariants:
- One prompt per model
- Same semantic task, different system preamble
- No adaptive prompting
- No follow-ups
- No retries
"""

import hashlib
from typing import Dict

# Fixed system prompts per model (no runtime mutation)
SYSTEM_PROMPTS = {
    "chatgpt": """You are a precise technical advisor. Provide clear, structured recommendations.
Focus on practical implementation and coordination between components.
Always consider trade-offs and provide confidence levels.""",

    "claude": """You are a thorough analyst. Provide detailed reasoning and consider edge cases.
Analyze problems deeply and explain your thought process clearly.
Consider long-term implications and maintainability.""",

    "gemini": """You are a code-focused engineer. Provide implementation-oriented solutions.
Focus on concrete code patterns and technical specifications.
Prioritize clarity, correctness, and performance.""",

    "deepseek": """You are a creative problem solver. Generate innovative approaches.
Explore multiple solution paths and identify novel patterns.
Consider alternative perspectives and emerging techniques."""
}

# Required output schema (textual instruction)
OUTPUT_SCHEMA_INSTRUCTION = """
You MUST output your response in EXACTLY this format:

PROPOSAL:
<Your proposed solution in 1-3 sentences>

RATIONALE:
<Your reasoning in 2-4 sentences>

CONFIDENCE:
<A number between 0.0 and 1.0>

Do NOT include any other sections or text outside this format.
"""


def construct_prompt(model_id: str, task_description: str, constraints: str) -> str:
    """Construct deterministic prompt for model.

    Phase 6 Invariant: Prompt construction is deterministic and model-specific.

    Args:
        model_id: Model identifier
        task_description: Task to accomplish
        constraints: Constraints and requirements

    Returns:
        Full prompt string (system + user)

    Raises:
        ValueError: If model_id not in SYSTEM_PROMPTS
    """
    if model_id not in SYSTEM_PROMPTS:
        raise ValueError(f"No system prompt for model: {model_id}")

    system_prompt = SYSTEM_PROMPTS[model_id]

    # Construct full prompt
    prompt = f"""SYSTEM:
{system_prompt}

USER:
Task: {task_description}

Constraints:
{constraints}

{OUTPUT_SCHEMA_INSTRUCTION}"""

    return prompt


def compute_prompt_hash(prompt: str) -> str:
    """Compute SHA-256 hash of prompt.

    Args:
        prompt: Full prompt text

    Returns:
        SHA-256 hex digest
    """
    return hashlib.sha256(prompt.encode('utf-8')).hexdigest()


def compute_task_hash(task_description: str, constraints: str) -> str:
    """Compute SHA-256 hash of task.

    Args:
        task_description: Task description
        constraints: Constraints

    Returns:
        SHA-256 hex digest
    """
    task_canonical = f"{task_description}|||{constraints}"
    return hashlib.sha256(task_canonical.encode('utf-8')).hexdigest()


def compute_constraints_hash(constraints: str) -> str:
    """Compute SHA-256 hash of constraints.

    Args:
        constraints: Constraints text

    Returns:
        SHA-256 hex digest
    """
    return hashlib.sha256(constraints.encode('utf-8')).hexdigest()
