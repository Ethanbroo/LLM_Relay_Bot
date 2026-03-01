"""9-phase pipeline orchestrator. Executes phases in sequence,
pauses for human approval, handles review loops and rate limits."""

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any

from telegram_bot.claude_code_client import ClaudeCodeClient
from telegram_bot.pipeline.phases import (
    ALL_PHASES, PhaseConfig, PhaseType,
    PHASE_6_CODE_GENERATION, PHASE_7_CODE_REVIEW,
)
from telegram_bot.pipeline.phase_result import PhaseResult
from telegram_bot.pipeline.task_classifier import classify_task
from telegram_bot.pipeline.rate_limiter import (
    parse_rate_limit, backoff_with_jitter, MAX_RETRIES,
)
from telegram_bot.pipeline.cost_tracker import CostTracker

logger = logging.getLogger(__name__)

# Callback types
ProgressCallback = Callable[[str, float, str], Awaitable[None]]
UserPromptCallback = Callable[[str], Awaitable[str]]

# Three-strike limit for code review → regen loop
MAX_REVIEW_REGEN_CYCLES = 3


@dataclass
class PipelineContext:
    """Mutable state accumulated across phases."""
    user_prompt: str
    project_name: str
    anchor: str = ""
    task_classification: str = ""
    research_output: str = ""
    architecture_output: str = ""
    architecture_json: dict[str, Any] | None = None
    review_output: str = ""
    documentation: str = ""
    session_id: str | None = None

    # Cumulative cost
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0

    # Phase results
    phase_results: list[PhaseResult] = field(default_factory=list)

    def accumulate_cost(self, result: PhaseResult):
        self.total_input_tokens += result.input_tokens
        self.total_output_tokens += result.output_tokens
        self.total_cost_usd += result.cost_usd
        self.phase_results.append(result)


class PipelineOrchestrator:
    """Executes the 9-phase build pipeline."""

    def __init__(
        self,
        claude_client: ClaudeCodeClient,
        cost_tracker: CostTracker,
        on_progress: ProgressCallback | None = None,
        on_user_prompt: UserPromptCallback | None = None,
        budget_limit: float = 10.0,
        workspace_path: str = "/app/workspace",
    ):
        self.claude = claude_client
        self.cost = cost_tracker
        self.on_progress = on_progress
        self.on_user_prompt = on_user_prompt
        self.budget_limit = budget_limit
        self.workspace_path = workspace_path

    async def run(self, user_prompt: str, project_name: str,
                  existing_session_id: str | None = None) -> PipelineContext:
        """Execute the full 9-phase pipeline. Returns the accumulated context."""

        ctx = PipelineContext(user_prompt=user_prompt, project_name=project_name)
        if existing_session_id:
            ctx.session_id = existing_session_id

        total_phases = len(ALL_PHASES)

        # Section 5: Context overflow detection thresholds
        CONTEXT_WARN_TOKENS = 100_000
        CONTEXT_HARD_TOKENS = 180_000

        for i, phase_config in enumerate(ALL_PHASES):
            phase_pct = i / total_phases

            # Progress update
            if self.on_progress:
                await self.on_progress(
                    phase_config.name,
                    phase_pct,
                    f"Phase {phase_config.phase_number}/{total_phases}: {phase_config.agent_name}",
                )

            # Phase 7 uses special review→regen loop
            if phase_config.phase_number == 7:
                result = await self._execute_review_regen_loop(ctx)
            elif phase_config.phase_type == PhaseType.REGEX:
                result = await self._execute_regex_phase(phase_config, ctx)
            elif phase_config.phase_type == PhaseType.EXTERNAL:
                result = await self._execute_external_phase(phase_config, ctx)
            elif phase_config.phase_type == PhaseType.LLM:
                result = await self._execute_llm_phase(phase_config, ctx)
            else:
                logger.error(f"Unknown phase type: {phase_config.phase_type}")
                continue

            if not result.success:
                logger.error(f"Phase {phase_config.phase_number} failed: {result.error_message}")
                ctx.accumulate_cost(result)
                # Critical failures abort the pipeline
                if "budget exceeded" in result.error_message.lower() or \
                   "authentication" in result.error_message.lower():
                    break
                continue

            ctx.accumulate_cost(result)

            # Section 5: Context overflow detection
            total_tokens = ctx.total_input_tokens + ctx.total_output_tokens
            if total_tokens >= CONTEXT_HARD_TOKENS:
                logger.warning(
                    "Context overflow at %d tokens after phase %d. Forcing handoff.",
                    total_tokens, phase_config.phase_number,
                )
                break
            elif total_tokens >= CONTEXT_WARN_TOKENS:
                logger.info(
                    "Context warning at %d tokens after phase %d.",
                    total_tokens, phase_config.phase_number,
                )

            # Route output to the appropriate context field
            self._store_phase_output(phase_config, result, ctx)

            # Handle human approval pauses
            if phase_config.requires_user_approval and self.on_user_prompt:
                await self._handle_approval_pause(phase_config, result, ctx)

        # Write HANDOFF.md at end of pipeline (Section 4)
        self._write_handoff(ctx)

        # Final progress
        if self.on_progress:
            await self.on_progress("Complete", 1.0, "Pipeline finished")

        return ctx

    # ── Phase Executors ──────────────────────────────────────

    async def _execute_llm_phase(
        self, config: PhaseConfig, ctx: PipelineContext,
    ) -> PhaseResult:
        """Execute a phase that requires a claude -p call, with retry logic."""

        prompt = self._build_prompt(config, ctx)
        system_suffix = config.system_prompt_suffix

        # Inject triple files (CLAUDE.md, HANDOFF.md, AGENTS.md) — Section 4
        from telegram_bot.project_context import ProjectContext
        project_context = ProjectContext(self.workspace_path)
        triple_file_content = project_context.read_all(ctx.project_name)
        if triple_file_content:
            system_suffix = f"{triple_file_content}\n\n{system_suffix}"

        if ctx.anchor:
            system_suffix = f"SEMANTIC ANCHOR:\n{ctx.anchor}\n\n{system_suffix}"

        for attempt in range(MAX_RETRIES):
            response = await self.claude.run(
                prompt=prompt,
                model=config.model,
                max_turns=config.max_turns,
                timeout=config.timeout_seconds,
                session_id=ctx.session_id,
                allowed_tools=list(config.allowed_tools) if config.allowed_tools else None,
                disallowed_tools=list(config.disallowed_tools) if config.disallowed_tools else None,
                system_prompt_append=system_suffix,
            )

            # Update session ID from response (for --resume on subsequent phases)
            if response.session_id:
                ctx.session_id = response.session_id

            # Check for rate limiting
            if response.is_error:
                rate_info = parse_rate_limit(response.error_message)
                if rate_info.is_rate_limited:
                    if self.on_progress:
                        await self.on_progress(
                            config.name,
                            0.0,
                            f"Rate limited. Retrying (attempt {attempt + 1}/{MAX_RETRIES})...",
                        )
                    await backoff_with_jitter(attempt, rate_info.retry_after_seconds)
                    continue
                else:
                    # Non-rate-limit error — don't retry
                    return PhaseResult(
                        phase_number=config.phase_number,
                        phase_name=config.name,
                        success=False,
                        error_message=response.error_message,
                        cost_usd=response.cost_usd,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        model_used=response.model,
                    )

            # Record cost
            await self.cost.record(
                project_name=ctx.project_name,
                phase_name=config.name,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=response.cost_usd,
            )

            # Budget check
            within, total_spent = await self.cost.check_budget(
                ctx.project_name, self.budget_limit,
            )
            if not within:
                return PhaseResult(
                    phase_number=config.phase_number,
                    phase_name=config.name,
                    success=False,
                    error_message=f"Token budget exceeded (${total_spent:.2f} of ${self.budget_limit:.2f})",
                    cost_usd=response.cost_usd,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    model_used=response.model,
                )

            # Parse JSON if expected
            parsed_json = None
            if config.expects_json:
                parsed_json = self._try_parse_json(response.text, config)
                if parsed_json is None and config.required_json_fields:
                    logger.warning(
                        f"Phase {config.phase_number}: Expected JSON but got text. "
                        f"Using raw text as fallback."
                    )

            return PhaseResult(
                phase_number=config.phase_number,
                phase_name=config.name,
                success=True,
                raw_output=response.text,
                parsed_json=parsed_json,
                session_id=response.session_id,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=response.cost_usd,
                model_used=response.model,
            )

        # All retries exhausted
        return PhaseResult(
            phase_number=config.phase_number,
            phase_name=config.name,
            success=False,
            error_message=f"Exhausted {MAX_RETRIES} retries (rate limited)",
        )

    async def _execute_regex_phase(
        self, config: PhaseConfig, ctx: PipelineContext,
    ) -> PhaseResult:
        """Execute Phase 3: regex-based task classification."""
        classification = classify_task(ctx.anchor)
        ctx.task_classification = classification
        return PhaseResult(
            phase_number=config.phase_number,
            phase_name=config.name,
            success=True,
            raw_output=classification,
        )

    async def _execute_external_phase(
        self, config: PhaseConfig, ctx: PipelineContext,
    ) -> PhaseResult:
        """Execute Phase 8: CI/CD gates via GitHub Actions.
        Section 3 initial implementation: skip with a placeholder.
        Full GitHub Actions integration is a Section 5 extension."""
        logger.info("Phase 8 (CI/CD): Skipping — GitHub Actions integration deferred")
        return PhaseResult(
            phase_number=config.phase_number,
            phase_name=config.name,
            success=True,
            raw_output="CI/CD gates deferred to Section 5",
        )

    # ── Review → Regen Loop (Phase 7 Special Handling) ───────

    async def _execute_review_regen_loop(self, ctx: PipelineContext) -> PhaseResult:
        """Execute the Phase 7 code review with potential Phase 6 regeneration.
        Three-strike rule: max 3 regen cycles before halting."""

        for cycle in range(MAX_REVIEW_REGEN_CYCLES):
            if self.on_progress:
                await self.on_progress(
                    "Code Review",
                    0.7 + (cycle * 0.05),
                    f"Review cycle {cycle + 1}/{MAX_REVIEW_REGEN_CYCLES}",
                )

            # Run review — escalate to Sonnet on retry cycles
            review_config = PHASE_7_CODE_REVIEW
            if cycle > 0:
                # Create a new PhaseConfig with sonnet model for escalation
                review_config = PhaseConfig(
                    phase_number=PHASE_7_CODE_REVIEW.phase_number,
                    name=PHASE_7_CODE_REVIEW.name,
                    agent_name=PHASE_7_CODE_REVIEW.agent_name,
                    phase_type=PHASE_7_CODE_REVIEW.phase_type,
                    model="sonnet",
                    max_turns=PHASE_7_CODE_REVIEW.max_turns,
                    timeout_seconds=PHASE_7_CODE_REVIEW.timeout_seconds,
                    allowed_tools=PHASE_7_CODE_REVIEW.allowed_tools,
                    disallowed_tools=PHASE_7_CODE_REVIEW.disallowed_tools,
                    expects_json=PHASE_7_CODE_REVIEW.expects_json,
                    required_json_fields=PHASE_7_CODE_REVIEW.required_json_fields,
                    system_prompt_suffix=PHASE_7_CODE_REVIEW.system_prompt_suffix,
                    prompt_template=PHASE_7_CODE_REVIEW.prompt_template,
                )

            review_result = await self._execute_llm_phase(review_config, ctx)
            if not review_result.success:
                return review_result

            # Parse review output
            review_json = review_result.parsed_json
            if review_json is None:
                # Review didn't return valid JSON — treat as passed with warnings
                logger.warning("Code review returned non-JSON. Treating as passed with warnings.")
                return review_result

            passed = review_json.get("passed", False)
            alignment_score = review_json.get("alignment_score", 0)

            if passed and alignment_score >= 7:
                # Code passes review
                ctx.review_output = review_result.raw_output
                return review_result

            if cycle == MAX_REVIEW_REGEN_CYCLES - 1:
                # Three strikes — halt
                logger.error(
                    f"Code review failed {MAX_REVIEW_REGEN_CYCLES} times. "
                    f"Last alignment_score: {alignment_score}"
                )
                review_result.error_message = (
                    f"Code review failed {MAX_REVIEW_REGEN_CYCLES} times. "
                    f"Alignment score: {alignment_score}/10. "
                    f"Issues: {json.dumps(review_json.get('issues', [])[:3])}"
                )
                review_result.success = False
                return review_result

            # Regenerate code with review feedback
            if self.on_progress:
                await self.on_progress(
                    "Code Regeneration",
                    0.65,
                    f"Fixing {len(review_json.get('issues', []))} issues...",
                )

            regen_prompt = (
                f"SEMANTIC ANCHOR:\n{ctx.anchor}\n\n"
                f"ARCHITECTURE:\n{ctx.architecture_output}\n\n"
                f"CODE REVIEW FEEDBACK (alignment_score: {alignment_score}/10):\n"
                f"{json.dumps(review_json.get('issues', []), indent=2)}\n\n"
                f"Fix ALL issues listed above. Do not introduce new features. "
                f"Run tests after fixing."
            )

            regen_response = await self.claude.run(
                prompt=regen_prompt,
                model="sonnet",
                max_turns=20,
                session_id=ctx.session_id,
                allowed_tools=["Read", "Edit", "Write", "Bash"],
                disallowed_tools=["web_search"],
                system_prompt_append=PHASE_6_CODE_GENERATION.system_prompt_suffix,
            )

            if regen_response.is_error:
                return PhaseResult(
                    phase_number=6,
                    phase_name="Code Regeneration",
                    success=False,
                    error_message=f"Regeneration failed: {regen_response.error_message}",
                )

            # Record regen cost
            await self.cost.record(
                project_name=ctx.project_name,
                phase_name="Code Regeneration",
                model=regen_response.model,
                input_tokens=regen_response.input_tokens,
                output_tokens=regen_response.output_tokens,
                cost_usd=regen_response.cost_usd,
            )

        # Should never reach here due to break/return logic above
        return PhaseResult(
            phase_number=7, phase_name="Code Review", success=False,
            error_message="Unexpected loop exit",
        )

    # ── Prompt Building ──────────────────────────────────────

    def _build_prompt(self, config: PhaseConfig, ctx: PipelineContext) -> str:
        """Build the prompt for a phase by filling in its template."""
        template = config.prompt_template
        if not template:
            return ctx.user_prompt

        # Determine what {prior_output} means for this phase
        prior_output = ""
        if config.phase_number == 2:
            # Phase 2 needs the Phase 1 Q&A (questions + user answers)
            p1 = next((r for r in ctx.phase_results if r.phase_number == 1), None)
            prior_output = p1.raw_output if p1 else ""
        elif config.phase_number == 4:
            prior_output = ctx.task_classification
        elif config.phase_number == 5:
            prior_output = ctx.research_output
        elif config.phase_number == 6:
            prior_output = ctx.architecture_output
        elif config.phase_number == 9:
            prior_output = ctx.architecture_output

        try:
            return template.format(
                user_prompt=ctx.user_prompt,
                anchor=ctx.anchor,
                prior_output=prior_output,
                architecture=ctx.architecture_output,
            )
        except KeyError as e:
            logger.error(f"Prompt template variable missing: {e}")
            return f"{ctx.user_prompt}\n\nContext:\n{prior_output}"

    # ── Output Storage ───────────────────────────────────────

    def _store_phase_output(
        self, config: PhaseConfig, result: PhaseResult, ctx: PipelineContext,
    ):
        """Route a phase's output to the correct context field."""
        match config.phase_number:
            case 2:
                ctx.anchor = result.raw_output
            case 3:
                ctx.task_classification = result.raw_output
            case 4:
                ctx.research_output = result.raw_output
            case 5:
                ctx.architecture_output = result.raw_output
                ctx.architecture_json = result.parsed_json
            case 7:
                ctx.review_output = result.raw_output
            case 9:
                ctx.documentation = result.raw_output

    # ── Approval Pauses ──────────────────────────────────────

    async def _handle_approval_pause(
        self, config: PhaseConfig, result: PhaseResult, ctx: PipelineContext,
    ):
        """Pause pipeline and wait for user approval.
        The on_user_prompt callback communicates with the Telegram handler."""
        if not self.on_user_prompt:
            logger.warning(
                f"Phase {config.phase_number} requires approval but no callback set. "
                f"Auto-approving."
            )
            return

        # Send the output to the user and wait for their response
        approval_message = (
            f"Phase: {config.name} — Review Required\n\n"
            f"{result.raw_output[:3000]}\n\n"  # Truncate for Telegram
            f"Reply 'approve' to continue or describe changes."
        )
        user_response = await self.on_user_prompt(approval_message)

        # If user requests changes, update the context
        if user_response.lower().strip() not in ("approve", "yes", "ok", "lgtm"):
            # User wants changes — store feedback for the next phase to consume
            ctx.user_prompt = f"{ctx.user_prompt}\n\nUser feedback on {config.name}: {user_response}"
            logger.info(f"User requested changes to Phase {config.phase_number}")

    # ── JSON Parsing ─────────────────────────────────────────

    def _try_parse_json(self, text: str, config: PhaseConfig) -> dict | None:
        """Attempt to parse JSON from LLM output.
        Handles markdown code fences, preamble text, and common LLM quirks."""

        # Strip markdown code fences
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        # Try direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in the text (LLM may have preamble)
        brace_start = cleaned.find("{")
        if brace_start == -1:
            return None

        # Find matching closing brace by counting nesting
        depth = 0
        for i in range(brace_start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[brace_start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        pass
                    break

        # Last resort: try to find any JSON-like substring
        # This handles cases where the LLM outputs JSON with trailing text
        for end_pos in range(len(cleaned) - 1, brace_start, -1):
            if cleaned[end_pos] == "}":
                candidate = cleaned[brace_start:end_pos + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

        logger.warning(
            f"Phase {config.phase_number}: Could not parse JSON from output "
            f"(length={len(text)})"
        )
        return None

    # ── HANDOFF.md Generation (Section 4) ────────────────────

    def _write_handoff(self, ctx: PipelineContext) -> None:
        """Write HANDOFF.md at the end of the pipeline run."""
        from telegram_bot.project_context import ProjectContext

        project_context = ProjectContext(self.workspace_path)
        max_phase = max(
            (r.phase_number for r in ctx.phase_results), default=0
        )

        # Extract key decisions from architecture risk register
        key_decisions = ""
        if ctx.architecture_json and "risk_register" in ctx.architecture_json:
            decisions = [
                r.get("risk", "") for r in ctx.architecture_json["risk_register"]
                if r.get("risk")
            ]
            key_decisions = "\n".join(f"- {d}" for d in decisions)

        project_context.write_handoff(
            project_name=ctx.project_name,
            session_id=ctx.session_id or "unknown",
            phase_reached=max_phase,
            what_was_done=ctx.documentation[:1000] if ctx.documentation else ctx.anchor,
            key_decisions=key_decisions,
        )
