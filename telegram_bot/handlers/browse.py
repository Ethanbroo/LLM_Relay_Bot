"""
Browser agent Telegram command handlers — Phase 4 + Phase 5.

Commands:
  - /task <description>: Start a browser automation task
  - /browse <url>: Quick navigate + screenshot
  - /status: Show active browser tasks
  - /resume: Resume a paused task
  - /sessions: Show active browser sessions
  - /history: Show recently completed tasks
  - /allowlist: View/manage domain allowlist (Phase 5)

The /cancel command is handled in browser.py for the BROWSER_TASK_RUNNING state.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.approval_manager import ApprovalManager
from telegram_bot.browser_agent import BrowserAgent, TaskResult
from telegram_bot.browser_client import BrowserClient, BrowserError
from telegram_bot.message_utils import send_long_message
from telegram_bot.states import BotState
from telegram_bot.task_manager import TaskManager, TaskState

logger = logging.getLogger(__name__)

# Heuristic patterns for detecting whether a task needs web research first
_URL_PATTERN = re.compile(r"https?://\S+|(?:www\.)\S+\.\S+")
_DOMAIN_PATTERN = re.compile(
    r"\b(?:go\s+to|visit|open|navigate\s+to|check)\s+"
    r"([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})\b",
    re.IGNORECASE,
)


def _task_needs_research(task: str) -> bool:
    """Heuristic: does this task need web research before browser action?

    Returns False if the task contains a URL or references a specific website
    by name (e.g., "go to notion.so"). Returns True for vague tasks like
    "find the best plumbing suppliers in Ontario".
    """
    if _URL_PATTERN.search(task):
        return False
    if _DOMAIN_PATTERN.search(task):
        return False
    return True


def _get_task_manager(context: ContextTypes.DEFAULT_TYPE) -> TaskManager:
    """Get or create the TaskManager from bot_data."""
    if "task_manager" not in context.bot_data:
        redis_client = context.bot_data.get("redis")
        context.bot_data["task_manager"] = TaskManager(redis_client)
    return context.bot_data["task_manager"]


def _get_approval_manager(context: ContextTypes.DEFAULT_TYPE) -> ApprovalManager:
    """Get or create the ApprovalManager from bot_data."""
    if "approval_manager" not in context.bot_data:
        context.bot_data["approval_manager"] = ApprovalManager()
    return context.bot_data["approval_manager"]


async def handle_task_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Entry point for /task command. Starts a browser automation task."""
    if not update.message or not update.message.text:
        return BotState.IDLE

    text = update.message.text
    if text.startswith("/task"):
        task_description = text[5:].strip()
    else:
        task_description = text.strip()

    if not task_description:
        await update.message.reply_text(
            "Please describe the browser task.\n\n"
            "Example: /task search Google for plumbing backflow preventer specs\n"
            "Example: /task check my Gmail for emails from Next Supply"
        )
        return BotState.IDLE

    task_manager = _get_task_manager(context)
    approval_manager = _get_approval_manager(context)
    allowlist = context.bot_data.get("domain_allowlist")
    audit = context.bot_data.get("audit_logger")

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    task_state = task_manager.create_task(task_description, chat_id, user_id)

    await update.message.reply_text(
        f"Starting browser task: \"{task_description[:200]}\"\n"
        f"Task ID: {task_state.task_id}\n"
        f"You'll see approval requests for sensitive actions."
    )

    async def _run_browser_task():
        try:
            # --- Pre-task research (if needed) ---
            research_context = None
            web_researcher = context.bot_data.get("web_researcher")
            if (
                web_researcher
                and web_researcher.available
                and _task_needs_research(task_description)
            ):
                await context.bot.send_message(
                    chat_id, "Researching before browsing..."
                )
                try:
                    research_result = await web_researcher.research(
                        query=task_description,
                        max_search_results=5,
                        max_read_urls=2,
                    )
                    if research_result.summary:
                        research_context = research_result.summary
                        cost_tracker = context.bot_data.get("cost_tracker")
                        if cost_tracker and task_state:
                            try:
                                await cost_tracker.record(
                                    project_name=f"browser:{task_state.task_id}",
                                    phase_name="web_research",
                                    model="tavily+jina",
                                    input_tokens=0,
                                    output_tokens=0,
                                    cost_usd=research_result.cost_usd,
                                )
                            except Exception:
                                logger.debug("Failed to record research cost", exc_info=True)
                except Exception as e:
                    logger.warning("Pre-task research failed: %s", e)

            client = BrowserClient()
            try:
                cost_tracker = context.bot_data.get("cost_tracker")
                redis_client = context.bot_data.get("redis")

                from telegram_bot.user_credential_vault import UserCredentialVault
                credential_vault = UserCredentialVault(redis_client) if redis_client else None
                credential_request_manager = context.bot_data.get("credential_request_manager")

                agent = BrowserAgent(
                    browser_client=client,
                    task_state=task_state,
                    approval_manager=approval_manager,
                    bot=context.bot,
                    on_progress=lambda step, msg, _: _send_progress(
                        chat_id, context, step, msg
                    ),
                    allowlist=allowlist,
                    audit=audit,
                    cost_tracker=cost_tracker,
                    credential_vault=credential_vault,
                    credential_request_manager=credential_request_manager,
                )
                context.user_data["browser_agent"] = agent

                result = await agent.run(
                    task_description,
                    research_context=research_context,
                )
                await _deliver_result(chat_id, context, result)

                status = "completed" if result.success else "failed"
                await task_manager.complete_task(task_state.task_id, status=status)
            finally:
                await client.close()
        except Exception as e:
            logger.error("Browser task failed: %s", e, exc_info=True)
            await send_long_message(
                chat_id, context, text=f"Browser task failed: {e}"
            )
            await task_manager.complete_task(task_state.task_id, status="failed")

    context.user_data["browser_task_handle"] = asyncio.create_task(
        _run_browser_task()
    )
    context.user_data["browser_task_id"] = task_state.task_id
    return BotState.BROWSER_TASK_RUNNING


async def handle_browse_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Quick navigation: /browse <url> — open a URL, return screenshot + summary."""
    if not update.message or not update.message.text:
        return BotState.IDLE

    text = update.message.text
    if text.startswith("/browse"):
        url = text[7:].strip()
    else:
        url = text.strip()

    if not url:
        await update.message.reply_text(
            "Please provide a URL.\n\n"
            "Example: /browse https://example.com"
        )
        return BotState.IDLE

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Phase 5: Check allowlist before navigating
    allowlist = context.bot_data.get("domain_allowlist")
    if allowlist:
        from telegram_bot.action_classifier import extract_domain
        domain = extract_domain(url)
        if domain:
            result = allowlist.check(domain)
            if result == "blocked":
                await update.message.reply_text(
                    f"Navigation blocked: {domain} is on the blocked domain list."
                )
                return BotState.IDLE
            if result == "unlisted" and allowlist.default_action == "block":
                await update.message.reply_text(
                    f"Navigation blocked: {domain} is not on the domain allowlist.\n"
                    f"Use /allowlist add {domain} to allow it."
                )
                return BotState.IDLE

    await update.message.reply_text(f"Opening {url}...")

    try:
        client = BrowserClient()
        try:
            session_id = await client.create_session()
            nav_result = await client.navigate(session_id, url)
            ss = await client.screenshot(session_id, quality=70)
            snap = await client.snapshot(session_id)
            await client.destroy_session(session_id)

            title = nav_result.get("title", "")
            final_url = nav_result.get("url", url)

            await update.message.reply_text(
                f"Page: {title}\nURL: {final_url}"
            )

            if ss.get("image"):
                img_bytes = base64.b64decode(ss["image"])
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=img_bytes
                )
        finally:
            await client.close()
    except BrowserError as e:
        await update.message.reply_text(f"Browser error: {e}")
    except Exception as e:
        logger.error("Browse command failed: %s", e, exc_info=True)
        await update.message.reply_text(f"Failed: {e}")

    return BotState.IDLE


async def handle_browser_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show the status of all active browser tasks."""
    task_manager = _get_task_manager(context)
    user_id = update.effective_user.id
    tasks = task_manager.get_tasks_for_user(user_id)

    if not tasks:
        await update.message.reply_text("No active browser tasks.")
        return BotState.IDLE

    lines = ["Active Browser Tasks:\n"]
    for i, task in enumerate(tasks, 1):
        desc = task.user_task[:60]
        lines.append(
            f"{i}. \"{desc}\" -- Step {task.step_count}, {task.status}"
        )

    await update.message.reply_text("\n".join(lines))
    return BotState.IDLE


async def handle_browser_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle cancel during a running browser task."""
    task_manager = _get_task_manager(context)
    approval_manager = _get_approval_manager(context)

    task_id = context.user_data.get("browser_task_id")

    # Check if a specific task ID was given
    if update.message and update.message.text:
        parts = update.message.text.split()
        if len(parts) > 1:
            task_id = parts[1]

    if task_id:
        task_state = task_manager.get_task(task_id)
        if task_state:
            approval_manager.cancel_all_for_task(task_id)

        credential_request_manager = context.bot_data.get("credential_request_manager")
        if credential_request_manager:
            credential_request_manager.cancel_all_for_task(task_id)

    agent: BrowserAgent | None = context.user_data.get("browser_agent")
    if agent:
        agent.cancel()

    task_handle = context.user_data.get("browser_task_handle")
    if task_handle and not task_handle.done():
        await update.effective_message.reply_text("Cancelling browser task...")
    else:
        await update.effective_message.reply_text(
            "No active browser task to cancel."
        )

    if task_id:
        await task_manager.complete_task(task_id, status="cancelled")

    return BotState.BROWSER_TASK_RUNNING


async def handle_resume_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Resume a paused browser task."""
    task_manager = _get_task_manager(context)
    user_id = update.effective_user.id

    # Find paused tasks for this user
    paused = [
        t for t in task_manager.get_tasks_for_user(user_id)
        if t.status in ("paused", "expired")
    ]

    if not paused:
        await update.message.reply_text(
            "No paused browser tasks to resume.\n"
            "Use /task to start a new task."
        )
        return BotState.IDLE

    # Resume the most recent paused task
    task_state = paused[0]
    task_state.status = "running"
    task_state.touch()

    await update.message.reply_text(
        f"Resuming task: \"{task_state.user_task[:100]}\"\n"
        f"Task ID: {task_state.task_id}\n"
        f"Note: Browser session may need to re-navigate to restore state."
    )

    approval_manager = _get_approval_manager(context)
    allowlist = context.bot_data.get("domain_allowlist")
    audit = context.bot_data.get("audit_logger")
    chat_id = update.effective_chat.id

    async def _resume_task():
        try:
            client = BrowserClient()
            try:
                cost_tracker = context.bot_data.get("cost_tracker")
                redis_client = context.bot_data.get("redis")

                from telegram_bot.user_credential_vault import UserCredentialVault
                credential_vault = UserCredentialVault(redis_client) if redis_client else None
                credential_request_manager = context.bot_data.get("credential_request_manager")

                agent = BrowserAgent(
                    browser_client=client,
                    task_state=task_state,
                    approval_manager=approval_manager,
                    bot=context.bot,
                    on_progress=lambda step, msg, _: _send_progress(
                        chat_id, context, step, msg
                    ),
                    allowlist=allowlist,
                    audit=audit,
                    cost_tracker=cost_tracker,
                    credential_vault=credential_vault,
                    credential_request_manager=credential_request_manager,
                )
                context.user_data["browser_agent"] = agent

                result = await agent.run(task_state.user_task)
                await _deliver_result(chat_id, context, result)

                status = "completed" if result.success else "failed"
                await task_manager.complete_task(task_state.task_id, status=status)
            finally:
                await client.close()
        except Exception as e:
            logger.error("Resumed browser task failed: %s", e, exc_info=True)
            await send_long_message(
                chat_id, context, text=f"Resumed task failed: {e}"
            )
            await task_manager.complete_task(task_state.task_id, status="failed")

    context.user_data["browser_task_handle"] = asyncio.create_task(
        _resume_task()
    )
    context.user_data["browser_task_id"] = task_state.task_id
    return BotState.BROWSER_TASK_RUNNING


async def handle_sessions_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show all active browser sessions."""
    task_manager = _get_task_manager(context)
    tasks = task_manager.get_active_tasks()

    active_sessions = [t for t in tasks if t.session_id]

    if not active_sessions:
        await update.message.reply_text("No active browser sessions.")
        return BotState.IDLE

    lines = ["Active Sessions:\n"]
    now = datetime.now(timezone.utc)
    for t in active_sessions:
        uptime = int((now - t.created_at).total_seconds())
        mins, secs = divmod(uptime, 60)
        desc = t.user_task[:40]
        lines.append(
            f"Session {t.task_id}: \"{desc}\", {mins}m{secs}s uptime, {t.status}"
        )

    lines.append(f"\nTotal: {len(active_sessions)} session(s)")
    await update.message.reply_text("\n".join(lines))
    return BotState.IDLE


async def handle_history_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show recently completed browser tasks."""
    task_manager = _get_task_manager(context)
    user_id = update.effective_user.id

    history = await task_manager.get_history(user_id, limit=10)

    if not history:
        await update.message.reply_text("No browser task history.")
        return BotState.IDLE

    status_icons = {
        "completed": "OK",
        "failed": "FAIL",
        "cancelled": "CANCELLED",
        "expired": "EXPIRED",
    }

    lines = ["Recent Tasks:\n"]
    for i, entry in enumerate(history, 1):
        status = entry.get("status", "?")
        icon = status_icons.get(status, "?")
        desc = entry.get("user_task", "?")[:50]
        steps = entry.get("step_count", 0)
        lines.append(f"{i}. [{icon}] \"{desc}\" ({steps} steps)")

    await update.message.reply_text("\n".join(lines))
    return BotState.IDLE


# ── Phase 5: /allowlist command handlers ──────────────────────────────


async def handle_allowlist_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /allowlist commands for viewing and managing the domain allowlist."""
    if not update.message or not update.message.text:
        return BotState.IDLE

    text = update.message.text
    if text.startswith("/allowlist"):
        args = text[10:].strip()
    else:
        args = text.strip()

    allowlist = context.bot_data.get("domain_allowlist")
    if not allowlist:
        await update.message.reply_text("Domain allowlist is not configured.")
        return BotState.IDLE

    # Parse subcommand
    parts = args.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else ""
    argument = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "add" and argument:
        return await _allowlist_add(update, context, allowlist, argument)
    elif subcommand == "remove" and argument:
        return await _allowlist_remove(update, context, allowlist, argument)
    elif subcommand == "check" and argument:
        return await _allowlist_check(update, allowlist, argument)
    elif subcommand == "reload":
        return await _allowlist_reload(update, allowlist)
    else:
        return await _allowlist_summary(update, allowlist)


async def _allowlist_summary(update: Update, allowlist) -> int:
    """Show allowlist summary."""
    dynamic = allowlist.get_dynamic_list()

    lines = [
        "Domain Allowlist Summary:\n",
        f"Always allowed: {len(allowlist.always_allowed)} domains",
        f"Allowed (with approval): {len(allowlist.allowed)} domains",
        f"Blocked: {len(allowlist.blocked)} domains",
        f"Dynamic additions: {len(dynamic)} domains",
        f"Default action for unlisted: {allowlist.default_action}",
    ]

    if dynamic:
        lines.append("\nDynamic domains:")
        for d in dynamic:
            lines.append(f"  - {d}")

    lines.append(
        "\nCommands:\n"
        "  /allowlist add <domain>\n"
        "  /allowlist remove <domain>\n"
        "  /allowlist check <domain>\n"
        "  /allowlist reload"
    )

    await update.message.reply_text("\n".join(lines))
    return BotState.IDLE


async def _allowlist_add(update: Update, context, allowlist, domain: str) -> int:
    """Add a domain to the dynamic allowlist."""
    domain = domain.lower().strip()

    # Check if already in static lists
    static_result = allowlist.check(domain)
    if static_result in ("always_allowed", "allowed"):
        await update.message.reply_text(
            f"{domain} is already in the static allowlist ({static_result})."
        )
        return BotState.IDLE

    if static_result == "blocked":
        await update.message.reply_text(
            f"{domain} is on the blocked list. "
            f"Edit config/domain-allowlist.yaml to unblock it."
        )
        return BotState.IDLE

    success = allowlist.add_dynamic(domain)
    if not success:
        max_d = allowlist.settings.get("max_dynamic_domains", 50)
        await update.message.reply_text(
            f"Maximum dynamic domain limit reached ({max_d}). "
            f"Remove some domains first with /allowlist remove <domain>."
        )
        return BotState.IDLE

    # Persist to Redis
    redis_client = context.bot_data.get("redis")
    await allowlist.save_dynamic_to_redis(redis_client)

    await update.message.reply_text(
        f"Added {domain} to the allowlist. "
        f"The browser agent can now visit this domain.\n"
        f"Use /allowlist remove {domain} to revoke."
    )
    return BotState.IDLE


async def _allowlist_remove(update: Update, context, allowlist, domain: str) -> int:
    """Remove a domain from the dynamic allowlist."""
    domain = domain.lower().strip()

    removed = allowlist.remove_dynamic(domain)
    if not removed:
        await update.message.reply_text(
            f"{domain} was not in the dynamic allowlist.\n"
            f"Static entries can only be changed by editing "
            f"config/domain-allowlist.yaml on the server."
        )
        return BotState.IDLE

    # Persist to Redis
    redis_client = context.bot_data.get("redis")
    await allowlist.save_dynamic_to_redis(redis_client)

    await update.message.reply_text(f"Removed {domain} from the dynamic allowlist.")
    return BotState.IDLE


async def _allowlist_check(update: Update, allowlist, domain: str) -> int:
    """Check if a domain is allowed."""
    domain = domain.lower().strip()
    result = allowlist.check(domain)

    status_map = {
        "always_allowed": f"{domain} is ALWAYS ALLOWED (auto-approve navigation)",
        "allowed": f"{domain} is ALLOWED (approval rules still apply for sensitive actions)",
        "blocked": f"{domain} is BLOCKED (navigation denied regardless of approval)",
        "unlisted": (
            f"{domain} is NOT LISTED. "
            f"Default action: {allowlist.default_action}. "
            f"Use /allowlist add {domain} to allow it."
        ),
    }

    await update.message.reply_text(status_map.get(result, f"Unknown status: {result}"))
    return BotState.IDLE


async def _allowlist_reload(update: Update, allowlist) -> int:
    """Force reload the allowlist from disk."""
    allowlist.load()
    await update.message.reply_text(
        f"Allowlist reloaded.\n"
        f"Always allowed: {len(allowlist.always_allowed)}\n"
        f"Allowed: {len(allowlist.allowed)}\n"
        f"Blocked: {len(allowlist.blocked)}"
    )
    return BotState.IDLE


# ── Phase 2: /credentials command handlers ────────────────────────────


async def handle_credentials_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /credentials commands for viewing and managing stored credentials.

    Sub-commands:
      /credentials list     — Show all configured domains
      /credentials add      — Start the credential addition flow
    """
    if not update.message or not update.message.text:
        return BotState.IDLE

    text = update.message.text
    if text.startswith("/credentials"):
        args = text[12:].strip()
    else:
        args = text.strip()

    parts = args.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else ""

    if subcommand == "list":
        return await _credentials_list(update)
    elif subcommand == "add":
        return await _credentials_add_start(update, context)
    else:
        await update.message.reply_text(
            "Credential Management:\n\n"
            "/credentials list — Show all configured domains\n"
            "/credentials add — Add credentials for a new domain\n\n"
            "Note: Credentials are stored as SOPS-encrypted files on the server.\n"
            "The add command creates an encrypted credential file via the CLI."
        )
        return BotState.IDLE


async def _credentials_list(update: Update) -> int:
    """List all domains with configured credentials."""
    from telegram_bot.credential_vault import list_configured_domains

    domains = list_configured_domains()

    if not domains:
        await update.message.reply_text(
            "No credentials configured.\n\n"
            "Use /credentials add to add credentials for a domain,\n"
            "or create SOPS-encrypted files in the secrets/ directory."
        )
        return BotState.IDLE

    lines = ["Configured credential domains:\n"]
    for i, domain in enumerate(domains, 1):
        lines.append(f"  {i}. {domain}")

    lines.append(f"\nTotal: {len(domains)} domain(s)")
    lines.append(
        "\nNote: Credentials are encrypted at rest. "
        "The browser agent can use fill_credentials for these domains."
    )

    await update.message.reply_text("\n".join(lines))
    return BotState.IDLE


async def _credentials_add_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Start the credential addition conversational flow."""
    await update.message.reply_text(
        "To add credentials, you'll need SSH access to the server.\n\n"
        "Steps:\n"
        "1. Create a YAML file in secrets/ with this format:\n"
        "   username: your_username\n"
        "   password: your_password\n"
        "   domains:\n"
        "     - example.com\n"
        "     - www.example.com\n"
        "   totp_seed: BASE32SECRET  # optional, for 2FA\n\n"
        "2. Encrypt it with SOPS:\n"
        "   sops --encrypt --age $(cat /run/secrets/age-key | grep public | "
        "awk '{print $NF}') secrets/example.yaml\n\n"
        "3. Add the domain mapping in secrets/domain-map.yaml:\n"
        "   example.com: example\n\n"
        "4. Re-encrypt the domain map:\n"
        "   sops secrets/domain-map.yaml\n\n"
        "The browser agent will pick up the new credentials immediately."
    )
    return BotState.IDLE


# ── Shared helpers ────────────────────────────────────────────────────


async def handle_message_during_browser_task(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle text messages while a browser task is running."""
    await update.message.reply_text(
        "A browser task is currently running. "
        "Send /cancel to stop it, or wait for it to complete."
    )
    return BotState.BROWSER_TASK_RUNNING


async def _send_progress(
    chat_id: int, context: ContextTypes.DEFAULT_TYPE, step: int, message: str
):
    """Send a progress update to the user (throttled)."""
    if step % 5 == 1 or step == 1:
        try:
            await context.bot.send_message(chat_id, f"Browser: {message}")
        except Exception:
            pass


async def _deliver_result(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    result: TaskResult,
):
    """Send the task result to the user via Telegram."""
    if result.success:
        text = f"Browser task complete ({result.steps_taken} steps)\n\n"
        if result.summary:
            text += f"{result.summary}\n"
        if result.data:
            text += f"\n{result.data[:3000]}"
    else:
        text = f"Browser task failed ({result.steps_taken} steps)\n\n"
        if result.reason:
            text += f"Reason: {result.reason}\n"
        if result.suggestion:
            text += f"Suggestion: {result.suggestion}\n"
        if result.error:
            text += f"Error: {result.error}\n"

    await send_long_message(chat_id, context, text=text)

    if result.screenshot_b64:
        try:
            img_bytes = base64.b64decode(result.screenshot_b64)
            await context.bot.send_photo(chat_id, photo=img_bytes)
        except Exception:
            logger.warning("Failed to send screenshot", exc_info=True)

    # Clean up user_data
    context.user_data.pop("browser_agent", None)
    context.user_data.pop("browser_task_handle", None)
    context.user_data.pop("browser_task_id", None)
