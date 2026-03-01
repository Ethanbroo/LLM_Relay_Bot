# telegram_bot/handlers/edit.py
"""
Edit/fix existing project flow handler (Path A).

Handles states:
  - AWAITING_PROJECT_SELECTION: User selects which project to edit
  - AWAITING_QUICK_FIX_CONFIRM: Quick fix applied, confirm or retry

Section 4 changes:
  - Session resume uses SessionManager to find latest session for project
  - Triple files (CLAUDE.md, HANDOFF.md, AGENTS.md) injected via system prompt
  - Session saved to Redis after successful edit
"""

import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.keyboards.project_select import build_project_select_keyboard
from telegram_bot.message_utils import send_long_message
from telegram_bot.pipeline_adapter import PipelineAdapter
from telegram_bot.progress import ProgressReporter
from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


def _get_registry(context: ContextTypes.DEFAULT_TYPE):
    """Get the project registry from bot_data."""
    if "project_registry" not in context.bot_data:
        from telegram_bot.project_registry import FilesystemProjectRegistry
        config = context.bot_data["config"]
        workspace = Path(config.workspace_path)
        context.bot_data["project_registry"] = FilesystemProjectRegistry(workspace)
    return context.bot_data["project_registry"]


async def start_project_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Entry point for edit/fix flow. Shows project selection keyboard."""
    context.user_data["original_message"] = message_text

    registry = _get_registry(context)
    projects = registry.list_projects(limit=20)

    effective_message = update.effective_message or update.callback_query.message

    if not projects:
        await effective_message.reply_text(
            "\U0001f4ed No existing projects found in the workspace.\n\n"
            "Would you like to start a new build instead? "
            "Just describe what you want to create."
        )
        return BotState.IDLE

    keyboard = build_project_select_keyboard(projects, page=0)
    await send_long_message(
        update.effective_chat.id,
        context,
        text=(
            "\u270f\ufe0f Which project would you like to edit?\n\n"
            "Select from recent projects below, or type the project name:"
        ),
        reply_markup=keyboard,
    )
    return BotState.AWAITING_PROJECT_SELECTION


async def handle_project_selected(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle callback when user selects a project from the list."""
    query = update.callback_query
    await query.answer()

    # Handle pagination
    if query.data.startswith("proj_page_"):
        page_str = query.data.replace("proj_page_", "")
        if page_str == "noop":
            return BotState.AWAITING_PROJECT_SELECTION
        page = int(page_str)
        registry = _get_registry(context)
        projects = registry.list_projects(limit=20)
        keyboard = build_project_select_keyboard(projects, page=page)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return BotState.AWAITING_PROJECT_SELECTION

    # Project selected
    project_name = query.data.replace("proj_", "", 1)
    context.user_data["selected_project"] = project_name

    registry = _get_registry(context)
    project = registry.get_project(project_name)

    if project:
        await query.edit_message_text(
            f"\U0001f4c1 Selected: *{project.display_name}*\n"
            f"({project.file_count} files)\n\n"
            "Analyzing the scope of your requested changes...",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            f"Selected project: {project_name}\n"
            "Analyzing the scope of your requested changes..."
        )

    config = context.bot_data["config"]

    # Section 4: Use SessionManager to find latest session for this project
    session_manager = context.bot_data.get("session_manager")
    session_id = None

    if session_manager:
        session = await session_manager.get_latest_for_project(project_name)
        if session:
            session_id = session.session_id
            context.user_data["last_session_id"] = session_id

    # Fallback to user_data if SessionManager not available
    if not session_id:
        session_id = context.user_data.get("last_session_id")

    # If we have a session to resume and are in VPS mode, run edit directly
    if session_id and config.is_vps_mode:
        return await _start_edit_with_resume(update, context, project_name, session_id)

    # Otherwise, route to critical questions (same as before)
    original_message = context.user_data.get("original_message", "")
    from telegram_bot.handlers.build import start_critical_questions
    return await start_critical_questions(update, context, original_message)


async def _start_edit_with_resume(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    project_name: str,
    session_id: str,
) -> int:
    """Run an edit using PipelineAdapter.run_edit() with --resume."""
    chat_id = update.effective_chat.id

    reporter = ProgressReporter(chat_id=chat_id, context=context)
    await reporter.start(f"Editing: {project_name[:30]}")

    async def on_progress(phase: str, pct: float, detail: str):
        reporter.progress.current_phase = phase
        await reporter.update()

    adapter: PipelineAdapter = context.bot_data["pipeline_adapter"]

    async def _run_and_deliver():
        try:
            result = await adapter.run_edit(
                edit_prompt=context.user_data.get("original_message", ""),
                project_name=project_name,
                session_id=session_id,
                on_progress=on_progress,
            )

            if result.success:
                summary = (
                    f"\u2705 *Edit Complete!*\n\n"
                    f"\U0001f4c1 Project: {result.project_name}\n"
                )
                if result.session_id:
                    summary += f"\U0001f4be Session: {result.session_id[:8]}...\n"
                    context.user_data["last_session_id"] = result.session_id
                    context.user_data["last_project"] = result.project_name

                    # Section 4: Save session to Redis
                    sm = context.bot_data.get("session_manager")
                    if sm:
                        from telegram_bot.session_manager import SessionRecord
                        await sm.save(SessionRecord(
                            session_id=result.session_id,
                            project_name=result.project_name,
                            total_tokens=result.total_tokens,
                            cost_usd=result.cost_usd,
                        ))
                if result.summary:
                    summary += f"\n{result.summary[:300]}"
                await reporter.finish(summary)
            else:
                await reporter.finish(
                    f"\u274c Edit failed: {result.error_message}"
                )
        except Exception as e:
            logger.error("Edit execution failed: %s", e, exc_info=True)
            await reporter.finish(f"\u274c Edit failed: {e}")

    context.user_data["pipeline_task"] = asyncio.create_task(_run_and_deliver())
    return BotState.EXECUTING


async def handle_project_typed(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle freeform text when user types a project name."""
    project_name = update.message.text

    registry = _get_registry(context)
    project = registry.find_project(project_name)

    if project:
        context.user_data["selected_project"] = project.name
        await update.message.reply_text(
            f"\U0001f4c1 Found: *{project.display_name}*\n"
            f"({project.file_count} files)\n\n"
            "Analyzing the scope of your requested changes...",
            parse_mode="Markdown",
        )
        original_message = context.user_data.get("original_message", "")
        from telegram_bot.handlers.build import start_critical_questions
        return await start_critical_questions(update, context, original_message)
    else:
        await update.message.reply_text(
            f"\U0001f50d Couldn't find a project matching \"{project_name}\".\n\n"
            "Try typing a different name, or select from the list above."
        )
        return BotState.AWAITING_PROJECT_SELECTION


async def handle_quick_fix_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle callback from the quick fix confirmation keyboard."""
    query = update.callback_query
    await query.answer()

    if query.data == "qfix_approve":
        await query.edit_message_text(
            "\u2705 Fix approved. Changes committed."
        )
        context.user_data.clear()
        return BotState.IDLE

    elif query.data == "qfix_retry":
        await query.edit_message_text(
            "\U0001f504 Re-running fix with additional context...\n"
            "Type any feedback to guide the retry, or I'll try a different approach."
        )
        return BotState.EXECUTING

    elif query.data == "qfix_revert":
        await query.edit_message_text(
            "\u21a9\ufe0f Reverting changes..."
        )
        context.user_data.clear()
        return BotState.IDLE

    return BotState.AWAITING_QUICK_FIX_CONFIRM
