#!/usr/bin/env python3
"""
LLM Relay Bot — Interactive CLI Entry Point
Multi-Agent v2.0 System

Usage:
    python main.py

Requires:
    ANTHROPIC_API_KEY environment variable set.

Phases:
    Phase 1  — Intent Clarification (Critical Thinking + Semantic Anchor)
    Phase 2  — Research (if applicable)
    Phase 3  — Architecture + Code Generation + Review (if applicable)
    Phase 4  — Summary (always)
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from typing import Optional


def _load_dotenv() -> None:
    """
    Load environment variables from .env file in the project root.
    Simple implementation — no external dependency required.
    Lines starting with # are ignored. Supports KEY=VALUE format.
    """
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WIDTH = 72


def banner(title: str) -> None:
    print()
    print("=" * WIDTH)
    print(f"  {title}")
    print("=" * WIDTH)


def section(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, WIDTH - len(title) - 4))


def calling(agent_name: str) -> None:
    print(f"\n  [calling Claude → {agent_name}]", flush=True)


def wrap(text: str, indent: int = 2) -> str:
    prefix = " " * indent
    return textwrap.fill(text, width=WIDTH - indent, initial_indent=prefix, subsequent_indent=prefix)


def prompt_user(label: str) -> str:
    print()
    return input(f"  {label}: ").strip()


def confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"\n  {question} {hint}: ").strip().lower()
    if not answer:
        return default
    return answer.startswith("y")


def display_block(label: str, content: str) -> None:
    section(label)
    for line in content.strip().splitlines():
        print(wrap(line))


# ---------------------------------------------------------------------------
# Phase 1 — Intent Clarification
# ---------------------------------------------------------------------------

def run_phase_1(llm, user_idea: str) -> tuple[str, str]:
    """
    Returns (semantic_anchor, clarification_qa_string).
    """
    banner("PHASE 1 — INTENT CLARIFICATION")

    # Step 1a: Critical Thinking Agent
    from multi_agent_v2.agents.prompts import critical_thinking_prompt
    system_p, user_p = critical_thinking_prompt(user_idea)

    calling("Critical Thinking Agent")
    raw = llm.generate(system_p, user_p)

    try:
        clarification = json.loads(raw)
        observations = clarification.get("observations", [])
        unknowns = clarification.get("unknowns", [])
        questions = clarification.get("questions", [])
    except json.JSONDecodeError:
        # Graceful fallback if model returns prose
        print(wrap("(Could not parse structured output — showing raw response)"))
        print(wrap(raw))
        observations, unknowns, questions = [], [], [raw]

    if observations:
        section("Observations")
        for obs in observations:
            print(wrap(f"• {obs}"))

    if unknowns:
        section("Unknowns / Gaps")
        for unk in unknowns:
            print(wrap(f"• {unk}"))

    # Step 1b: Ask user the clarifying questions
    answers: list[str] = []
    if questions:
        section("Clarifying Questions")
        print(wrap("Please answer each question. Press Enter to skip any."))
        for i, q in enumerate(questions, 1):
            print()
            print(wrap(f"Q{i}: {q}"))
            ans = input("       Your answer: ").strip()
            answers.append(f"Q{i}: {q}\nA: {ans if ans else '(no answer provided)'}")

    clarification_qa = "\n\n".join(answers) if answers else "(No clarifying questions answered.)"

    # Step 1c: Semantic Anchor Agent
    from multi_agent_v2.agents.prompts import semantic_anchor_prompt
    system_p2, user_p2 = semantic_anchor_prompt(user_idea, clarification_qa)

    calling("Semantic Anchor Agent")
    semantic_anchor = llm.generate(system_p2, user_p2).strip()

    display_block("Semantic Intent Anchor (draft)", semantic_anchor)

    print()
    if not confirm("Does this accurately capture your intent? Confirm to lock it in."):
        print(wrap("Okay — please rephrase your goal and we will restart clarification."))
        new_idea = prompt_user("Revised idea")
        return run_phase_1(llm, new_idea)

    print()
    print(wrap("Intent anchor confirmed and locked. This is the reference for all future phases."))
    return semantic_anchor, clarification_qa


# ---------------------------------------------------------------------------
# Phase 2 — Research
# ---------------------------------------------------------------------------

def run_phase_2(llm, semantic_anchor: str, task_description: str) -> str:
    """Returns research summary string."""
    banner("PHASE 2 — RESEARCH")

    from multi_agent_v2.agents.prompts import research_prompt
    system_p, user_p = research_prompt(semantic_anchor, task_description)

    calling("Research Agent")
    research_output = llm.generate(system_p, user_p).strip()

    display_block("Research Findings", research_output)
    return research_output


# ---------------------------------------------------------------------------
# Phase 3 — Architecture + Code Generation + Review
# ---------------------------------------------------------------------------

def run_phase_3_architecture(llm, semantic_anchor: str, research_summary: str = "") -> str:
    """Returns architecture plan string."""
    banner("PHASE 3a — ARCHITECTURE PLANNING")

    from multi_agent_v2.agents.prompts import architecture_prompt
    system_p, user_p = architecture_prompt(semantic_anchor, research_summary)

    calling("Architecture Agent")
    arch_output = llm.generate(system_p, user_p).strip()

    display_block("Architecture Plan", arch_output)
    return arch_output


def run_phase_3_codegen(llm, semantic_anchor: str, architecture_plan: str) -> str:
    """Returns generated code string."""
    banner("PHASE 3b — CODE GENERATION")

    from multi_agent_v2.agents.prompts import code_generation_prompt
    system_p, user_p = code_generation_prompt(semantic_anchor, architecture_plan)

    calling("Code Generation Agent")
    code_output = llm.generate(system_p, user_p).strip()

    display_block("Generated Code", code_output)
    return code_output


def run_phase_3_review(llm, semantic_anchor: str, code: str) -> dict:
    """Returns review dict."""
    banner("PHASE 3c — CODE REVIEW")

    from multi_agent_v2.agents.prompts import review_prompt
    system_p, user_p = review_prompt(semantic_anchor, code)

    calling("Code Review Agent")
    raw = llm.generate(system_p, user_p).strip()

    try:
        review = json.loads(raw)
    except json.JSONDecodeError:
        review = {"passed": None, "critical_issues": [], "warnings": [], "raw": raw}

    passed = review.get("passed")
    critical = review.get("critical_issues", [])
    warnings = review.get("warnings", [])
    score = review.get("alignment_score")
    notes = review.get("alignment_notes", "")

    section("Review Results")
    status = "PASSED" if passed else ("FAILED" if passed is False else "REVIEW COMPLETE")
    print(wrap(f"Status: {status}"))
    if score is not None:
        print(wrap(f"Alignment score: {score}/10"))
    if notes:
        print(wrap(f"Notes: {notes}"))

    if critical:
        section("Critical Issues (must fix)")
        for issue in critical:
            print(wrap(f"• {issue}"))

    if warnings:
        section("Warnings")
        for w in warnings:
            print(wrap(f"• {w}"))

    return review


# ---------------------------------------------------------------------------
# Phase 4 — Summary
# ---------------------------------------------------------------------------

def run_phase_4(llm, semantic_anchor: str, implementation_output: str) -> str:
    """Returns summary document string."""
    banner("PHASE 4 — SUMMARY")

    from multi_agent_v2.agents.prompts import summary_prompt
    system_p, user_p = summary_prompt(semantic_anchor, implementation_output)

    calling("Documentation Agent")
    summary = llm.generate(system_p, user_p).strip()

    display_block("Session Summary Document", summary)
    return summary


def save_summary(summary: str, task_slug: str) -> tuple[str, Optional[str]]:
    """
    Saves summary to a local .md file and attempts to upload to Google Docs.

    Returns:
        (local_file_path, google_doc_url_or_None)
    """
    import re
    slug = re.sub(r"[^a-z0-9_]", "_", task_slug.lower())[:40]
    filename = f"session_summary_{slug}.md"
    path = os.path.join(os.path.dirname(__file__), filename)
    with open(path, "w") as f:
        f.write(summary)

    # Attempt Google Docs upload
    doc_url = None
    try:
        from connectors.google_docs_real import GoogleDocsRealConnector
        gc = GoogleDocsRealConnector()
        if gc.is_ready:
            section("Uploading to Google Docs")
            print(wrap("  [connecting to Google Docs API]"), flush=True)
            title = f"LLM Relay Session — {task_slug[:60]}"
            result = gc.create_document(title=title, content=summary)
            if result.success:
                doc_url = result.doc_url
                print(wrap(f"Google Doc created: {doc_url}"))
            else:
                print(wrap(f"Google Docs upload failed: {result.error}"))
        else:
            print(wrap(f"Google Docs: {gc.setup_error}"))
    except Exception as e:
        print(wrap(f"Google Docs: skipped ({e})"))

    return path, doc_url


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    print("=" * WIDTH)
    print("  LLM RELAY BOT  —  Multi-Agent v2.0")
    print("  Powered by Claude (Anthropic)")
    print("=" * WIDTH)
    print()
    print(wrap("This system will guide your idea through a multi-agent pipeline:"))
    print(wrap("  intent clarification → research → architecture → code → summary"))
    print()
    print(wrap("Type 'quit' or 'exit' at any prompt to end the session."))

    # --- Boot real Claude client ---
    from multi_agent_v2.real_claude import RealClaudeClient
    llm = RealClaudeClient()   # exits with error if no API key

    # --- Get the user's idea ---
    section("What do you want to build or explore?")
    print(wrap("Describe your idea in a sentence or two. Be as specific or vague as you like."))
    user_idea = prompt_user("Your idea")

    if user_idea.lower() in ("quit", "exit", ""):
        print("\n  Goodbye.\n")
        sys.exit(0)

    # --- Classify the task ---
    from multi_agent_v2.task_router import classify_task, phase_label
    route = classify_task(user_idea)

    section("Task Routing")
    print(wrap(f"Task type detected: {route.task_type.value.upper()}"))
    print(wrap(f"Pipeline: {' → '.join(phase_label(p) for p in route.phases)}"))
    print(wrap(f"Rationale: {route.rationale}"))

    if not confirm("Proceed with this pipeline?"):
        print(wrap("You can type 'hybrid' to run the full pipeline, or 'quit' to exit."))
        override = prompt_user("Override task type (research / planning / implementation / hybrid / quit)")
        if override.lower() in ("quit", "exit"):
            sys.exit(0)
        from multi_agent_v2.task_router import TaskType, TaskRoute, PHASE_LABELS
        override_map = {
            "research":       ["intent_clarification", "research", "summary"],
            "planning":       ["intent_clarification", "research", "architecture", "summary"],
            "implementation": ["intent_clarification", "architecture", "code_generation", "code_review", "summary"],
            "hybrid":         ["intent_clarification", "research", "architecture", "code_generation", "code_review", "summary"],
        }
        phases = override_map.get(override.lower(), route.phases)
        route = TaskRoute(
            task_type=TaskType(override.lower()) if override.lower() in TaskType._value2member_map_ else route.task_type,
            phases=phases,
            rationale="User override.",
            requires_google_doc=route.requires_google_doc,
        )

    # --- Run phases ---
    semantic_anchor = ""
    research_summary = ""
    architecture_plan = ""
    code_output = ""
    implementation_parts: list[str] = []

    for phase in route.phases:

        if phase == "intent_clarification":
            semantic_anchor, _ = run_phase_1(llm, user_idea)

        elif phase == "research":
            research_summary = run_phase_2(llm, semantic_anchor, user_idea)
            implementation_parts.append(f"Research findings:\n{research_summary}")

        elif phase == "architecture":
            architecture_plan = run_phase_3_architecture(llm, semantic_anchor, research_summary)
            implementation_parts.append(f"Architecture plan:\n{architecture_plan}")

        elif phase == "code_generation":
            code_output = run_phase_3_codegen(llm, semantic_anchor, architecture_plan)
            implementation_parts.append(f"Generated code:\n{code_output}")

        elif phase == "code_review":
            review = run_phase_3_review(llm, semantic_anchor, code_output)
            passed = review.get("passed", None)
            if passed is False:
                critical = review.get("critical_issues", [])
                if critical:
                    print()
                    print(wrap("Code review found critical issues. Regenerating..."))
                    # One automatic retry
                    code_output = run_phase_3_codegen(llm, semantic_anchor, architecture_plan)
                    implementation_parts.append(f"Revised code:\n{code_output}")

        elif phase == "summary":
            combined = "\n\n".join(implementation_parts) if implementation_parts else user_idea
            summary_doc = run_phase_4(llm, semantic_anchor or user_idea, combined)

            # Save locally + upload to Google Docs
            summary_path, doc_url = save_summary(summary_doc, user_idea[:40])
            section("Session Complete")
            print(wrap(f"Summary saved locally: {summary_path}"))
            if doc_url:
                print(wrap(f"Google Doc: {doc_url}"))

    # --- Done ---
    print()
    print("=" * WIDTH)
    print("  SESSION COMPLETE")
    print("=" * WIDTH)
    print()


if __name__ == "__main__":
    main()
