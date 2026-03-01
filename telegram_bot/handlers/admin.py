"""
/cost, /status, /sessions, /health admin commands.

These are always-available commands that work from any state.
They provide operational visibility without interrupting workflows.

Section 5 changes:
  - /cost enhanced with per-project + per-model breakdown, weekly totals
  - /status shows active pipelines from Redis progress records
  - /health shows container stats, preview status, job queue
  - config accessed as BotConfig object via context.bot_data["config"]
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def handle_cost(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /cost command. Detailed cost and usage report.

    Section 5: Per-project and per-model breakdowns, weekly totals.
    """
    redis_client = context.bot_data.get("redis")
    if not redis_client:
        session_cost = context.user_data.get("session_cost", 0.0)
        await update.message.reply_text(
            "\U0001f4b0 *Cost Summary*\n\n"
            f"This session: ${session_cost:.4f}\n\n"
            "_Full cost tracking requires Redis._",
            parse_mode="Markdown",
        )
        return

    today_str = date.today().isoformat()

    try:
        today_data = await redis_client.hgetall(f"cost:{today_str}") or {}
    except Exception as e:
        logger.warning("Failed to read cost from Redis: %s", e)
        today_data = {}

    # Today's summary
    today_tokens = int(today_data.get("total_tokens", 0))
    today_calls = int(today_data.get("total_calls", 0))

    # Weekly summary
    week_tokens, week_calls = 0, 0
    for i in range(7):
        day = (date.today() - timedelta(days=i)).isoformat()
        try:
            d = await redis_client.hgetall(f"cost:{day}") or {}
            week_tokens += int(d.get("total_tokens", 0))
            week_calls += int(d.get("total_calls", 0))
        except Exception:
            pass

    msg = (
        "\U0001f4b0 *Cost Report*\n\n"
        f"Today: ~{today_tokens:,} tokens across {today_calls} calls\n"
        f"This week: ~{week_tokens:,} tokens across {week_calls} calls\n"
        f"Budget: Max subscription ($200/mo flat rate)\n"
    )

    # Per-project breakdown (today)
    project_costs = {}
    for key, val in today_data.items():
        if key.startswith("project:") and key.endswith(":tokens"):
            proj_name = key.split(":")[1]
            project_costs[proj_name] = int(val)

    top_projects = sorted(project_costs.items(), key=lambda x: x[1], reverse=True)[:5]
    if top_projects:
        msg += "\n*Top projects today:*\n"
        for name, tokens in top_projects:
            msg += f"  {name}: {tokens:,} tokens\n"

    # Model breakdown (today)
    model_tokens = {}
    for key, val in today_data.items():
        if key.startswith("model:") and key.endswith(":tokens"):
            model_name = key.split(":")[1]
            model_tokens[model_name] = int(val)

    if model_tokens:
        msg += "\n*Model usage today:*\n"
        for model, tokens in sorted(model_tokens.items()):
            msg += f"  {model}: {tokens:,} tokens\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /status command. Show active pipeline progress.

    Section 5: Shows running pipelines from Redis progress records.
    """
    config = context.bot_data["config"]
    redis_client = context.bot_data.get("redis")

    redis_status = "Disconnected"
    if redis_client:
        try:
            await redis_client.ping()
            redis_status = "Connected"
        except Exception:
            redis_status = "Error"

    mode = "VPS (Claude Code CLI)" if config.is_vps_mode else "Local (API)"
    pipeline = "Mock" if config.use_mock_orchestrator else "Real (9-phase)"

    sections = [
        f"Bot: Online",
        f"Mode: {mode}",
        f"Pipeline: {pipeline}",
        f"Redis: {redis_status}",
    ]

    # Active pipelines
    if redis_client:
        try:
            active = []
            async for key in redis_client.scan_iter("progress:*"):
                data = await redis_client.hgetall(key)
                if data and data.get("status") == "running":
                    active.append(data)

            if active:
                sections.append(f"\n*Active Pipelines ({len(active)}):*")
                for p in active:
                    phase = p.get("phase_name", "?")
                    tokens = int(p.get("tokens_used", 0))
                    sections.append(f"  Phase: {phase} | Tokens: {tokens:,}")
            else:
                sections.append("\nNo active pipelines.")
        except Exception:
            pass

    # Preview status
    preview_mgr = context.bot_data.get("preview_manager")
    if preview_mgr:
        status = await preview_mgr.status()
        if status:
            remaining = status["remaining_seconds"] // 60
            sections.append(
                f"\nPreview: \U0001f7e2 {status['project']} ({remaining}m remaining)"
            )

    await update.message.reply_text(
        "\U0001f7e2 *Bot Status*\n\n" + "\n".join(sections),
        parse_mode="Markdown",
    )


async def handle_sessions(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /sessions command. List recent build sessions."""
    redis_client = context.bot_data.get("redis")

    if redis_client:
        try:
            session_keys = []
            async for key in redis_client.scan_iter(match="session:*", count=20):
                session_keys.append(key)

            if session_keys:
                lines = []
                for key in session_keys[:10]:
                    session_data = await redis_client.hgetall(key)
                    name = session_data.get("project_name", key.split(":")[-1])
                    phase = session_data.get("phase_reached", "?")
                    tokens = int(session_data.get("total_tokens", 0))
                    lines.append(f"\u2022 {name} (phase {phase}, {tokens:,} tokens)")

                await update.message.reply_text(
                    "\U0001f4cb *Recent Sessions*\n\n"
                    + "\n".join(lines),
                    parse_mode="Markdown",
                )
                return
        except Exception as e:
            logger.warning("Failed to read sessions from Redis: %s", e)

    last_session = context.user_data.get("last_session_id")
    if last_session:
        await update.message.reply_text(
            "\U0001f4cb *Recent Sessions*\n\n"
            f"\u2022 Last session: {last_session[:8]}...\n\n"
            "_Full session history requires Redis._",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "\U0001f4cb *Recent Sessions*\n\n"
            "_No sessions recorded yet._\n\n"
            "Sessions will appear here as you use the build pipeline.",
            parse_mode="Markdown",
        )


async def handle_health(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /health command. Comprehensive system health check.

    Section 5: Container stats, preview status, job queue depth.
    """
    config = context.bot_data["config"]
    redis_client = context.bot_data.get("redis")

    checks = []

    # Bot
    checks.append("\u2705 Bot: Online")

    # Redis
    if redis_client:
        try:
            info = await redis_client.info("memory")
            used_mb = info.get("used_memory_human", "?")
            checks.append(f"\u2705 Redis: Connected ({used_mb})")
        except Exception as e:
            checks.append(f"\u274c Redis: {e}")
    else:
        checks.append("\u26a0\ufe0f Redis: Not configured")

    # Claude client
    claude_client = context.bot_data.get("claude_client")
    if claude_client:
        checks.append(f"\u2705 Claude: {'VPS' if config.is_vps_mode else 'Local'}")
    else:
        checks.append("\u274c Claude: Not initialized")

    # Classifier
    classifier = context.bot_data.get("classifier")
    if classifier:
        checks.append(f"\u2705 Classifier: {config.classifier_model}")
    else:
        checks.append("\u274c Classifier: Not initialized")

    # Preview status
    preview_mgr = context.bot_data.get("preview_manager")
    if preview_mgr:
        status = await preview_mgr.status()
        if status:
            remaining = status["remaining_seconds"] // 60
            checks.append(
                f"\U0001f7e2 Preview: {status['project']} ({remaining}m remaining)"
            )
        else:
            checks.append("\u26aa Preview: None active")

    # Container stats (Section 5)
    monitor = context.bot_data.get("health_monitor")
    if monitor:
        container_stats = await monitor.get_status()
        checks.append(f"\n{container_stats}")

    # Job queue
    if redis_client:
        try:
            queue_len = await redis_client.llen("queue:jobs")
            if queue_len > 0:
                checks.append(f"\u23f3 Job queue: {queue_len} pending")
        except Exception:
            pass

    await update.message.reply_text(
        "\U0001f3e5 *Health Check*\n\n" + "\n".join(checks),
        parse_mode="Markdown",
    )
