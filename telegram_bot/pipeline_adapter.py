"""
Bridge between Telegram handlers and the build pipeline.

Supports mock (local dev) and real (VPS) backends. The interface is stable
across Sections 2, 3, and 4 — handlers call run_build/run_edit/run_question
without knowing which backend is active.

Section 3: Real backend uses the 9-phase PipelineOrchestrator.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from telegram_bot.config import BotConfig

logger = logging.getLogger(__name__)

# Type alias for user prompt callback (pipeline pauses for human approval)
# (message_to_user: str) -> user_response_text
UserPromptCallback = Callable[[str], Awaitable[str]]


@dataclass
class PipelineResult:
    """Result returned by any pipeline execution."""
    success: bool
    project_name: str
    session_id: Optional[str] = None
    summary: str = ""
    anchor: str = ""
    documentation: str = ""
    files_created: list[str] = field(default_factory=list)
    file_descriptions: dict[str, str] = field(default_factory=dict)
    lint_status: str = "n/a"
    test_status: str = "n/a"
    test_coverage: float = 0.0
    security_status: str = "n/a"
    total_tokens: int = 0
    cost_usd: float = 0.0
    error_message: str = ""
    phase_results: list = field(default_factory=list)  # list[PhaseResult]
    review_output: str = ""


# Type alias for progress callback
# (phase_name: str, progress_pct: float, detail_message: str) -> None
ProgressCallback = Callable[[str, float, str], Awaitable[None]]


class PipelineAdapter:
    """Selects and delegates to the appropriate pipeline backend."""

    def __init__(self, config: BotConfig, claude_client=None, redis_client=None):
        self.config = config
        self.claude_client = claude_client
        self.redis_client = redis_client

    async def run_build(
        self,
        user_prompt: str,
        project_name: str,
        on_progress: Optional[ProgressCallback] = None,
        semantic_anchor: Optional[str] = None,
        existing_session_id: Optional[str] = None,
        on_user_prompt: Optional[UserPromptCallback] = None,
    ) -> PipelineResult:
        """Run a full build pipeline (new project or feature addition)."""
        if self.config.use_mock_orchestrator:
            return await self._run_mock(user_prompt, project_name, on_progress)
        else:
            return await self._run_real_orchestrated(
                user_prompt, project_name, on_progress,
                existing_session_id, on_user_prompt,
            )

    async def run_edit(
        self,
        edit_prompt: str,
        project_name: str,
        session_id: str,
        on_progress: Optional[ProgressCallback] = None,
    ) -> PipelineResult:
        """Run a quick edit (resume session, targeted change)."""
        if self.config.use_mock_orchestrator:
            return await self._run_mock(edit_prompt, project_name, on_progress)
        else:
            return await self._run_real_edit(
                edit_prompt, project_name, session_id, on_progress,
            )

    async def run_question(
        self,
        question: str,
        project_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Run a Q&A query (single LLM call, no pipeline)."""
        if self.config.use_mock_orchestrator:
            return f"[Mock] Answer to: {question}"
        else:
            response = await self.claude_client.run(
                prompt=question,
                model="sonnet",
                max_turns=1,
                session_id=session_id,
            )
            return response.text if not response.is_error else f"Error: {response.error_message}"

    async def run_research(
        self,
        query: str,
    ) -> str:
        """Run a research query (single LLM call with research-oriented prompt)."""
        if self.config.use_mock_orchestrator:
            return f"[Mock] Research results for: {query}"
        else:
            response = await self.claude_client.run(
                prompt=query,
                model="sonnet",
                max_turns=3,
                system_prompt_append=(
                    "You are a research assistant. Gather, synthesize, and report "
                    "information clearly. Do not produce code unless explicitly asked."
                ),
            )
            return response.text if not response.is_error else f"Error: {response.error_message}"

    async def run_conversational(
        self,
        message: str,
    ) -> str:
        """Run a conversational response (single LLM call, casual)."""
        if self.config.use_mock_orchestrator:
            return (
                "Hey! I'm the LLM Relay Bot. Send me a description of what "
                "you want to build and I'll take care of the rest.\n\n"
                "Type /help for usage examples."
            )
        else:
            response = await self.claude_client.run(
                prompt=message,
                model="sonnet",
                max_turns=1,
                system_prompt_append=(
                    "You are the LLM Relay Bot, a friendly AI assistant that helps "
                    "users build software projects via Telegram. Keep responses concise "
                    "and helpful. If the user seems to want to build something, suggest "
                    "they describe their project and you'll take care of the rest."
                ),
            )
            return response.text if not response.is_error else f"Error: {response.error_message}"

    # ── Mock backend (local dev / testing) ────────────────────

    async def _run_mock(
        self, prompt: str, project_name: str,
        on_progress: Optional[ProgressCallback],
    ) -> PipelineResult:
        """Delegate to mock_orchestrator.py for local development."""
        from telegram_bot.mock_orchestrator import MockOrchestrator
        from telegram_bot.pipeline_adapter_legacy import (
            PipelineRequest,
            PipelineState,
        )

        orchestrator = MockOrchestrator()
        state = PipelineState()

        request = PipelineRequest(
            user_message=prompt,
            intent="NEW_BUILD",
            semantic_anchor=prompt,
            critical_answers={},
            session_id="mock-session",
            workspace_path="/tmp/mock-workspace",
        )

        def progress_callback(event: dict):
            if "phase" in event:
                state.current_phase = event["phase"]
            if "phase_number" in event:
                state.phase_number = event["phase_number"]
            if "total_phases" in event:
                state.total_phases = event["total_phases"]
            return None

        result = await asyncio.to_thread(
            orchestrator.run, request, progress_callback,
        )

        if on_progress:
            await on_progress("Complete", 1.0, "Mock build finished")

        return PipelineResult(
            success=result.get("status") == "success",
            project_name=project_name,
            session_id=result.get("session_id"),
            summary=f"Mock build complete for: {project_name}",
            files_created=result.get("files_created", []),
            total_tokens=0,
            cost_usd=result.get("total_cost_usd", 0.0),
        )

    # ── Real orchestrated (Section 3 — 9-phase pipeline) ──

    async def _run_real_orchestrated(
        self,
        prompt: str, project_name: str,
        on_progress: Optional[ProgressCallback],
        existing_session_id: Optional[str],
        on_user_prompt: Optional[UserPromptCallback],
    ) -> PipelineResult:
        """Real backend: 9-phase orchestrated pipeline via PipelineOrchestrator."""
        from telegram_bot.pipeline.orchestrator import PipelineOrchestrator
        from telegram_bot.pipeline.cost_tracker import CostTracker

        cost_tracker = CostTracker(self.redis_client)

        orchestrator = PipelineOrchestrator(
            claude_client=self.claude_client,
            cost_tracker=cost_tracker,
            on_progress=on_progress,
            on_user_prompt=on_user_prompt,
            budget_limit=self.config.token_budget_default,
            workspace_path=self.config.workspace_path,
        )

        ctx = await orchestrator.run(
            user_prompt=prompt,
            project_name=project_name,
            existing_session_id=existing_session_id,
        )

        # Extract file manifest from architecture JSON if available
        files_created = []
        file_descriptions = {}
        if ctx.architecture_json and "file_manifest" in ctx.architecture_json:
            for f in ctx.architecture_json["file_manifest"]:
                path = f.get("path", "")
                if path:
                    files_created.append(path)
                    file_descriptions[path] = f.get("purpose", "")

        # Determine overall success: check if any phase failed critically
        any_critical_failure = any(
            not r.success and r.error_message
            for r in ctx.phase_results
            if "budget exceeded" in r.error_message.lower()
            or "authentication" in r.error_message.lower()
        )

        # Build summary from documentation or last successful phase
        summary = ctx.documentation or ""
        if not summary:
            # Use last successful phase output as summary
            for r in reversed(ctx.phase_results):
                if r.success and r.raw_output:
                    summary = r.raw_output[:500]
                    break

        # Extract review info
        review_output = ctx.review_output
        review_json = None
        for r in ctx.phase_results:
            if r.phase_number == 7 and r.parsed_json:
                review_json = r.parsed_json
                break

        return PipelineResult(
            success=not any_critical_failure,
            project_name=project_name,
            session_id=ctx.session_id,
            summary=summary,
            anchor=ctx.anchor,
            documentation=ctx.documentation,
            files_created=files_created,
            file_descriptions=file_descriptions,
            lint_status="pass" if review_json and review_json.get("passed") else "n/a",
            test_status="pass" if review_json and not review_json.get("missing_from_plan") else "n/a",
            security_status="pass" if review_json and not review_json.get("security_findings") else "n/a",
            total_tokens=ctx.total_input_tokens + ctx.total_output_tokens,
            cost_usd=ctx.total_cost_usd,
            phase_results=ctx.phase_results,
            review_output=review_output,
            error_message=next(
                (r.error_message for r in ctx.phase_results if not r.success and r.error_message),
                "",
            ),
        )

    async def _run_real_edit(
        self,
        prompt: str, project_name: str,
        session_id: str,
        on_progress: Optional[ProgressCallback],
    ) -> PipelineResult:
        """Real edit: resume existing session with --resume."""
        if on_progress:
            await on_progress("Resuming session", 0.0, f"Session: {session_id[:8]}...")

        response = await self.claude_client.run(
            prompt=prompt,
            model="sonnet",
            max_turns=5,
            session_id=session_id,
        )

        if on_progress:
            await on_progress("Complete", 1.0, "Edit finished")

        return PipelineResult(
            success=not response.is_error,
            project_name=project_name,
            session_id=response.session_id,
            summary=response.text[:500] if not response.is_error else "",
            total_tokens=response.input_tokens + response.output_tokens,
            cost_usd=response.cost_usd,
            error_message=response.error_message,
        )
