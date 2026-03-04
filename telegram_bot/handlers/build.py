# telegram_bot/handlers/build.py
"""
Handlers for the new build pipeline flow.

Manages: Critical Thinking Agent questions -> Semantic Anchor approval ->
Pipeline execution -> Progress monitoring -> Delivery -> Completion.

Section 3 changes:
  - _start_pipeline_execution uses PipelineAdapter with 9-phase orchestrator
  - _handle_pipeline_complete renders per-phase results, review output, docs
  - config accessed as BotConfig object via context.bot_data["config"]
"""

import asyncio
import logging
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from telegram_bot.keyboards.anchor import anchor_approval_keyboard
from telegram_bot.keyboards.clarification import critical_question_keyboard
from telegram_bot.keyboards.delivery import (
    delivery_keyboard, quick_fix_keyboard, build_vscode_url, format_quality_gates,
)
from telegram_bot.keyboards.execution import execution_control_keyboard
from telegram_bot.message_utils import send_long_message
from telegram_bot.pipeline_adapter import PipelineAdapter, PipelineResult
from telegram_bot.progress import ProgressReporter
from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


# --- Phase 1: Critical Thinking Agent ---


async def start_critical_questions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Call the Critical Thinking Agent and present its questions."""
    context.user_data["original_message"] = message_text

    # Phase 1 stub: generate placeholder questions
    questions = [
        {
            "text": "What's the primary goal of this project? Who is the target audience?",
            "default": "General web users, standard responsive design",
        },
        {
            "text": "Are there any specific technologies, frameworks, or design preferences?",
            "default": "Let you decide based on best practices",
        },
        {
            "text": "What's the scope? MVP or full-featured?",
            "default": "MVP with clean architecture for future expansion",
        },
    ]

    context.user_data["clarification_questions"] = questions
    context.user_data["clarification_answers"] = []

    effective_message = update.effective_message or update.callback_query.message

    first_q = questions[0]
    keyboard = critical_question_keyboard(
        question_index=0,
        total_questions=len(questions),
        default_answer=first_q["default"],
    )

    await effective_message.reply_text(
        f"\U0001f528 Analyzing your request...\n\n"
        f"\u2753 Question 1 of {len(questions)}:\n\n"
        f"{first_q['text']}\n\n"
        "Type your answer below, or tap the default:",
        reply_markup=keyboard,
    )
    return BotState.AWAITING_CRITICAL_QUESTIONS


# --- Build vs Edit disambiguation ---


async def handle_build_vs_edit_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle callback from the build-vs-edit disambiguation keyboard."""
    query = update.callback_query
    await query.answer()

    if query.data == "bve_new":
        original_message = context.user_data.get("original_message", "")
        context.user_data["session_id"] = str(uuid.uuid4())
        await query.edit_message_text(
            "\U0001f528 Starting new build pipeline..."
        )
        return await start_critical_questions(update, context, original_message)

    elif query.data == "bve_edit":
        from telegram_bot.handlers.edit import start_project_selection

        original_message = context.user_data.get("original_message", "")
        await query.edit_message_text(
            "Which project would you like to edit?"
        )
        return await start_project_selection(update, context, original_message)

    return BotState.IDLE


# --- Critical Questions ---


async def handle_question_answers(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle text answers to Critical Thinking Agent questions."""
    answer_text = update.message.text

    answers = context.user_data.setdefault("clarification_answers", [])
    questions = context.user_data.get("clarification_questions", [])
    current_idx = len(answers)

    answers.append(answer_text)

    if current_idx + 1 < len(questions):
        next_q = questions[current_idx + 1]
        keyboard = critical_question_keyboard(
            question_index=current_idx + 1,
            total_questions=len(questions),
            default_answer=next_q.get("default", "Let you decide"),
        )
        await update.message.reply_text(
            f"\u2753 Question {current_idx + 2} of {len(questions)}:\n\n"
            f"{next_q['text']}\n\n"
            "Type your answer below, or tap the default:",
            reply_markup=keyboard,
        )
        return BotState.AWAITING_CRITICAL_QUESTIONS

    return await _generate_and_present_anchor(update, context)


async def handle_default_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle callback when user taps a default answer button."""
    query = update.callback_query
    await query.answer()

    questions = context.user_data.get("clarification_questions", [])
    answers = context.user_data.setdefault("clarification_answers", [])

    if query.data == "defans_all":
        for i in range(len(answers), len(questions)):
            answers.append(questions[i].get("default", "Let you decide"))
        await query.edit_message_text("\u2705 Using defaults for remaining questions.")
        return await _generate_and_present_anchor(update, context)

    idx = int(query.data.replace("defans_", ""))
    default = questions[idx].get("default", "Let you decide") if idx < len(questions) else ""
    answers.append(default)

    if idx + 1 < len(questions):
        next_q = questions[idx + 1]
        keyboard = critical_question_keyboard(
            question_index=idx + 1,
            total_questions=len(questions),
            default_answer=next_q.get("default", "Let you decide"),
        )
        await query.edit_message_text(
            f"\u2753 Question {idx + 2} of {len(questions)}:\n\n"
            f"{next_q['text']}\n\n"
            "Type your answer below, or tap the default:",
            reply_markup=keyboard,
        )
        return BotState.AWAITING_CRITICAL_QUESTIONS

    await query.edit_message_text("\u2705 All questions answered.")
    return await _generate_and_present_anchor(update, context)


async def _generate_and_present_anchor(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Generate a semantic anchor from the original message + answers."""
    original_message = context.user_data.get("original_message", "")
    answers = context.user_data.get("clarification_answers", [])
    questions = context.user_data.get("clarification_questions", [])

    qa_summary = ""
    for i, q in enumerate(questions):
        answer = answers[i] if i < len(answers) else q.get("default", "")
        qa_summary += f"\n\u2022 {q['text']}\n  \u2192 {answer}"

    anchor_text = (
        f"Build request: {original_message}\n\n"
        f"Clarifications:{qa_summary}"
    )
    context.user_data["semantic_anchor"] = anchor_text

    effective_chat_id = update.effective_chat.id

    await send_long_message(
        effective_chat_id,
        context,
        text=(
            "\U0001f4cc *Semantic Anchor*\n\n"
            f"_{anchor_text}_\n\n"
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            "This anchor will guide every phase of the build. All agents will "
            "reference this paragraph to stay aligned with your intent.\n\n"
            "Does this capture what you want?"
        ),
        reply_markup=anchor_approval_keyboard(),
        parse_mode="Markdown",
    )
    return BotState.AWAITING_ANCHOR_APPROVAL


# --- Anchor Approval ---


async def handle_anchor_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle callback from the semantic anchor approval keyboard."""
    query = update.callback_query
    await query.answer()

    if query.data == "anchor_approve":
        await query.edit_message_text(
            "\u2705 Anchor approved and locked. Starting pipeline execution..."
        )
        return await _start_pipeline_execution(update, context)

    elif query.data == "anchor_restart":
        context.user_data.pop("clarification_answers", None)
        context.user_data.pop("semantic_anchor", None)
        original_message = context.user_data.get("original_message", "")
        await query.edit_message_text(
            "\U0001f504 Restarting clarification..."
        )
        return await start_critical_questions(update, context, original_message)

    elif query.data == "anchor_edit":
        await query.edit_message_text(
            "\u270f\ufe0f Type your revised anchor text below. "
            "I'll use it directly as the build intent."
        )
        return BotState.AWAITING_ANCHOR_APPROVAL

    return BotState.IDLE


async def handle_anchor_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle freeform text when editing the semantic anchor."""
    edited_text = update.message.text
    context.user_data["semantic_anchor"] = edited_text

    await send_long_message(
        update.effective_chat.id,
        context,
        text=(
            "\U0001f4cc *Revised Semantic Anchor*\n\n"
            f"_{edited_text}_\n\n"
            "Does this capture what you want?"
        ),
        reply_markup=anchor_approval_keyboard(),
        parse_mode="Markdown",
    )
    return BotState.AWAITING_ANCHOR_APPROVAL


# --- Pipeline Execution ---


async def _start_pipeline_execution(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Launch the pipeline via PipelineAdapter from bot_data.

    Creates a ProgressReporter, wires up progress callback,
    and runs the pipeline asynchronously.
    """
    chat_id = update.effective_chat.id
    config = context.bot_data["config"]

    # Create progress reporter
    reporter = ProgressReporter(chat_id=chat_id, context=context)
    project_name = context.user_data.get("original_message", "Build")[:40]
    await reporter.start(project_name)
    context.user_data["progress_reporter"] = reporter

    # Progress callback wired to the reporter
    async def on_progress(phase: str, pct: float, detail: str):
        reporter.progress.current_phase = phase
        reporter.progress.phase_number = int(pct * 9)
        reporter.progress.total_phases = 9
        await reporter.update()

    # Get PipelineAdapter from bot_data (initialized in post_init)
    adapter: PipelineAdapter = context.bot_data["pipeline_adapter"]

    async def _run_and_deliver():
        try:
            result = await adapter.run_build(
                user_prompt=context.user_data.get("original_message", ""),
                project_name=project_name,
                on_progress=on_progress,
                existing_session_id=None,
            )
            await _handle_pipeline_complete(chat_id, context, result, reporter)
        except Exception as e:
            logger.error("Pipeline execution failed: %s", e, exc_info=True)
            await reporter.finish(f"\u274c Build failed: {e}")

    context.user_data["pipeline_task"] = asyncio.create_task(_run_and_deliver())

    return BotState.EXECUTING


async def _handle_pipeline_complete(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    result: PipelineResult,
    reporter: ProgressReporter,
) -> None:
    """Called when the pipeline finishes. Sends structured delivery message."""
    if not result.success:
        # Check if this is a recoverable limit (budget, context overflow, max turns)
        # vs a hard failure (auth error, crash)
        error_lower = (result.error_message or "").lower()
        is_recoverable = any(kw in error_lower for kw in [
            "budget exceeded", "context overflow", "max turns",
            "token limit", "forcing handoff",
        ])

        if is_recoverable and result.session_id:
            # Save session so Continue can resume it
            context.user_data["last_session_id"] = result.session_id
            context.user_data["last_project"] = result.project_name
            context.user_data["continue_reason"] = result.error_message

            session_manager = context.bot_data.get("session_manager")
            if session_manager:
                from telegram_bot.session_manager import SessionRecord
                max_phase = max(
                    (pr.phase_number for pr in result.phase_results), default=0
                )
                await session_manager.save(SessionRecord(
                    session_id=result.session_id,
                    project_name=result.project_name,
                    semantic_anchor=result.anchor,
                    file_manifest=result.files_created,
                    total_tokens=result.total_tokens,
                    cost_usd=result.cost_usd,
                    phase_reached=max_phase,
                    handoff_written=True,
                ))

            # Build progress summary for user
            phases_done = sum(1 for pr in result.phase_results if pr.success)
            total_phases = len(result.phase_results)

            continue_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "\u25b6\ufe0f Continue Build",
                    callback_data="cont_resume",
                ),
                InlineKeyboardButton(
                    "\u23f9 Stop",
                    callback_data="cont_stop",
                ),
            ]])

            await reporter.finish(
                f"\u26a0\ufe0f Build paused: {result.project_name}\n\n"
                f"Reason: {result.error_message}\n\n"
                f"Progress: {phases_done}/{total_phases} phases completed\n"
                f"Tokens used: {result.total_tokens:,}\n"
                f"Cost so far: ${result.cost_usd:.4f}\n"
                f"Session: {result.session_id[:8]}...\n\n"
                "The build can continue from where it left off. "
                "All files and context are preserved.",
                keyboard=continue_kb,
            )
            return

        await reporter.finish(
            f"\u274c Build failed: {result.project_name}\n\n"
            f"{result.error_message}"
        )
        return

    # Store session_id for potential resume
    if result.session_id:
        context.user_data["last_session_id"] = result.session_id
        context.user_data["last_project"] = result.project_name

    # Section 4: Save session record to Redis via SessionManager
    session_manager = context.bot_data.get("session_manager")
    if session_manager and result.session_id:
        from telegram_bot.session_manager import SessionRecord
        # Extract review info
        review_status = ""
        review_score = 0
        for pr in result.phase_results:
            if pr.phase_number == 7 and pr.parsed_json:
                review_status = "passed" if pr.parsed_json.get("passed") else "failed"
                review_score = pr.parsed_json.get("alignment_score", 0)
                break

        max_phase = max(
            (pr.phase_number for pr in result.phase_results), default=0
        )

        await session_manager.save(SessionRecord(
            session_id=result.session_id,
            project_name=result.project_name,
            semantic_anchor=result.anchor,
            file_manifest=result.files_created,
            review_status=review_status,
            review_score=review_score,
            total_tokens=result.total_tokens,
            cost_usd=result.cost_usd,
            phase_reached=max_phase,
            handoff_written=True,
        ))

    config = context.bot_data["config"]

    # Build completion summary
    summary = (
        "\u2705 *Build Complete!*\n\n"
        f"\U0001f4c1 Project: {result.project_name}\n"
    )

    if result.files_created:
        summary += f"\U0001f4c2 Files created: {len(result.files_created)}\n"

    if result.total_tokens > 0:
        summary += f"\U0001f504 Tokens used: {result.total_tokens:,}\n"

    if result.cost_usd > 0:
        summary += f"\U0001f4b0 Cost: ${result.cost_usd:.4f}\n"

    if result.session_id:
        summary += f"\U0001f4be Session: {result.session_id[:8]}...\n"

    # Quality gates line (Section 5)
    review_json = None
    for pr in result.phase_results:
        if pr.phase_number == 7 and pr.parsed_json:
            review_json = pr.parsed_json
            break
    quality_line = format_quality_gates(review_json, cicd_passed=False)
    summary += f"\nQuality: {quality_line}\n"

    # Per-phase breakdown (Section 3)
    if result.phase_results:
        summary += "\n*Phase Summary:*\n"
        for pr in result.phase_results:
            status = "\u2705" if pr.success else "\u274c"
            cost_str = f" (${pr.cost_usd:.4f})" if pr.cost_usd > 0 else ""
            summary += f"{status} {pr.phase_name}{cost_str}\n"

    # Review status
    if result.review_output:
        summary += "\n*Code Review:* "
        for pr in result.phase_results:
            if pr.phase_number == 7 and pr.parsed_json:
                score = pr.parsed_json.get("alignment_score", "?")
                passed = pr.parsed_json.get("passed", False)
                summary += f"{'Passed' if passed else 'Failed'} (score: {score}/10)\n"
                break
        else:
            summary += "Completed\n"

    if result.summary:
        summary += f"\n{result.summary[:300]}"

    # Section 5: Build VS Code deep link
    vscode_url = None
    if config.domain:
        vscode_url = build_vscode_url(config.domain, result.project_name)

    # Determine capabilities for delivery buttons
    can_create_pr = bool(config.github_pat)
    can_preview = bool(config.domain and context.bot_data.get("preview_manager"))

    kb = delivery_keyboard(
        github_pr_url=None,
        preview_url=None,
        vscode_url=vscode_url,
        has_downloadable_files=len(result.files_created) > 0,
        has_documentation=bool(result.documentation),
        is_deployable=False,
        can_create_pr=can_create_pr,
        can_preview=can_preview,
    )

    await reporter.finish(summary, keyboard=kb)

    # Section 5: Increment build count in project registry
    registry = context.bot_data.get("project_registry")
    if registry and hasattr(registry, "increment_build_count"):
        try:
            await registry.increment_build_count(result.project_name)
        except Exception as e:
            logger.warning("Failed to increment build count: %s", e)


# --- Execution Controls ---


async def handle_execution_control(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle execution control callbacks (pause/resume/skip/cancel)."""
    query = update.callback_query
    await query.answer()

    if query.data == "exec_cancel":
        task = context.user_data.get("pipeline_task")
        if task and not task.done():
            task.cancel()
        await query.edit_message_text("\u23f9 Cancelling pipeline...")
        return BotState.IDLE

    elif query.data == "exec_pause":
        await query.edit_message_reply_markup(
            reply_markup=execution_control_keyboard(is_paused=True)
        )
        return BotState.EXECUTING

    elif query.data == "exec_resume":
        await query.edit_message_reply_markup(
            reply_markup=execution_control_keyboard(is_paused=False)
        )
        return BotState.EXECUTING

    elif query.data == "exec_skip":
        await query.answer("Skipping current phase...")
        return BotState.EXECUTING

    return BotState.EXECUTING


async def handle_message_during_execution(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle messages sent while the pipeline is executing."""
    await update.message.reply_text(
        "I'm currently executing your request. "
        "I'll process your message once the current pipeline completes.\n\n"
        "Use the controls above to pause, skip, or cancel."
    )
    return BotState.EXECUTING


# --- Checkpoints ---


async def handle_checkpoint_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle callback at a pipeline checkpoint."""
    query = update.callback_query
    await query.answer()

    if query.data == "ckpt_continue":
        await query.edit_message_text("\u2705 Continuing pipeline execution...")
        return BotState.EXECUTING

    elif query.data == "ckpt_adjust":
        await query.edit_message_text(
            "Type your adjustment below. I'll incorporate it and continue."
        )
        return BotState.AWAITING_CHECKPOINT_APPROVAL

    return BotState.EXECUTING


async def handle_checkpoint_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle text input for checkpoint adjustments."""
    adjustment = update.message.text
    context.user_data["checkpoint_adjustment"] = adjustment
    await update.message.reply_text(
        f"\u2705 Adjustment noted. Resuming with your feedback..."
    )
    return BotState.EXECUTING


# --- Human Decisions ---


async def handle_human_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle callback for AuthorityModelResolver human decisions."""
    query = update.callback_query
    await query.answer()

    decision_id = query.data.replace("hdec_", "", 1)
    context.user_data["human_decision"] = decision_id

    await query.edit_message_text(
        f"\u2705 Decision recorded. Resuming pipeline..."
    )
    return BotState.EXECUTING


async def handle_human_decision_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle freeform text for human decisions."""
    decision_text = update.message.text
    context.user_data["human_decision"] = decision_text

    await update.message.reply_text(
        "\u2705 Decision recorded. Resuming pipeline..."
    )
    return BotState.EXECUTING


# --- Delivery ---


async def handle_delivery_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle delivery action callbacks (Section 5 enhanced)."""
    query = update.callback_query
    await query.answer()

    config = context.bot_data["config"]
    project_name = context.user_data.get("last_project", "")

    if query.data == "dlvr_download":
        workspace_path = Path(config.workspace_path) / project_name
        if not workspace_path.exists():
            # Fallback to session-based path
            session_id = context.user_data.get("session_id", "")
            workspace_path = Path(config.workspace_path) / session_id
        if workspace_path.exists():
            from telegram_bot.media import send_project_files
            await send_project_files(
                query.message.chat_id, workspace_path, context,
                caption="\U0001f4e6 Your project files",
            )
        else:
            await query.message.reply_text(
                "\U0001f4ed No project files found for this session."
            )
        return BotState.AWAITING_DELIVERY_ACTION

    elif query.data == "dlvr_docs":
        doc_path = Path(config.workspace_path) / project_name / "README.md"
        if not doc_path.exists():
            session_id = context.user_data.get("session_id", "")
            doc_path = Path(config.workspace_path) / session_id / "README.md"
        if doc_path.exists():
            doc_text = doc_path.read_text()
            await send_long_message(
                query.message.chat_id, context, text=doc_text,
            )
        else:
            await query.message.reply_text(
                "\U0001f4c4 No documentation found for this session."
            )
        return BotState.AWAITING_DELIVERY_ACTION

    elif query.data == "dlvr_pr":
        # Section 5: Create PR via GitManager
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        git_mgr = context.bot_data.get("git_manager")
        if not git_mgr or not config.github_pat:
            await query.message.reply_text("GitHub integration not configured.")
            return BotState.AWAITING_DELIVERY_ACTION

        session_short = context.user_data.get("last_session_id", "unknown")[:8]
        anchor = context.user_data.get("semantic_anchor", "Automated build")
        branch = f"bot/{project_name}-{session_short}"

        await query.message.reply_text("\U0001f4c4 Creating PR...")

        await git_mgr.create_feature_branch(project_name, branch)
        await git_mgr.commit_all(project_name, f"feat: {anchor[:72]}")
        pushed = await git_mgr.push(project_name, branch)

        if not pushed:
            await query.message.reply_text("\u274c Failed to push to GitHub. Check GITHUB_PAT.")
            return BotState.AWAITING_DELIVERY_ACTION

        repo_info = await git_mgr.get_repo_info(project_name)
        if repo_info:
            pr_url = await git_mgr.create_pull_request(
                repo_owner=repo_info[0],
                repo_name=repo_info[1],
                branch_name=branch,
                title=f"feat: {anchor[:72]}",
                body=f"## Semantic Anchor\n\n{anchor}\n\n*Auto-generated by LLM Relay Bot*",
            )
            if pr_url:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("\U0001f4c4 View PR", url=pr_url),
                ]])
                await query.message.reply_text("\u2705 PR created!", reply_markup=keyboard)
            else:
                await query.message.reply_text(
                    "\u26a0\ufe0f Branch pushed but PR creation failed. Create manually on GitHub."
                )
        else:
            await query.message.reply_text(
                "\u26a0\ufe0f Branch pushed but no GitHub remote found. Create PR manually."
            )
        return BotState.AWAITING_DELIVERY_ACTION

    elif query.data == "dlvr_preview":
        # Section 5: Start live preview via PreviewManager
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        preview_mgr = context.bot_data.get("preview_manager")
        if not preview_mgr:
            await query.message.reply_text("Preview not available.")
            return BotState.AWAITING_DELIVERY_ACTION

        await query.message.reply_text("\U0001f680 Starting preview...")

        result = await preview_mgr.start(project_name)
        if "error" in result:
            await query.message.reply_text(f"\u274c {result['error']}")
        else:
            timeout_min = config.preview_timeout_seconds // 60
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("\U0001f310 Open Preview", url=result["url"]),
            ]])
            await query.message.reply_text(
                f"\u2705 Preview running!\nAuto-stops in {timeout_min} minutes.",
                reply_markup=keyboard,
            )
        return BotState.AWAITING_DELIVERY_ACTION

    elif query.data == "dlvr_deploy":
        await query.edit_message_text(
            "\U0001f680 Deploy to production is not available yet."
        )
        return BotState.AWAITING_DELIVERY_ACTION

    return BotState.AWAITING_DELIVERY_ACTION


# --- Continue Build (resume after hitting limits) ---


async def handle_continue_build(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle the Continue Build / Stop buttons after a build hits a limit."""
    query = update.callback_query
    await query.answer()

    if query.data == "cont_stop":
        await query.edit_message_text(
            "\u23f9 Build stopped. You can resume later by sending an edit request "
            "for this project."
        )
        return BotState.IDLE

    if query.data == "cont_resume":
        session_id = context.user_data.get("last_session_id")
        project_name = context.user_data.get("last_project", "Build")

        if not session_id:
            await query.edit_message_text(
                "\u274c No session to resume. Please start a new build."
            )
            return BotState.IDLE

        await query.edit_message_text(
            f"\u25b6\ufe0f Continuing build: {project_name}\n"
            f"Resuming session {session_id[:8]}..."
        )

        chat_id = update.effective_chat.id
        reporter = ProgressReporter(chat_id=chat_id, context=context)
        await reporter.start(f"Continuing: {project_name[:30]}")

        async def on_progress(phase: str, pct: float, detail: str):
            reporter.progress.current_phase = phase
            reporter.progress.phase_number = int(pct * 9)
            reporter.progress.total_phases = 9
            await reporter.update()

        adapter: PipelineAdapter = context.bot_data["pipeline_adapter"]

        async def _run_continuation():
            try:
                # Use run_build with existing_session_id to resume.
                # The HANDOFF.md written by the previous run gives the CLI
                # full context of what was done and what remains.
                result = await adapter.run_build(
                    user_prompt=(
                        "Continue the build from where it left off. "
                        "Read HANDOFF.md for context on what was completed "
                        "and what still needs to be done. Complete all "
                        "remaining phases."
                    ),
                    project_name=project_name,
                    on_progress=on_progress,
                    existing_session_id=session_id,
                )
                await _handle_pipeline_complete(chat_id, context, result, reporter)
            except Exception as e:
                logger.error("Continue build failed: %s", e, exc_info=True)
                await reporter.finish(f"\u274c Continue failed: {e}")

        context.user_data["pipeline_task"] = asyncio.create_task(_run_continuation())
        return BotState.EXECUTING

    return BotState.AWAITING_CONTINUE_BUILD
