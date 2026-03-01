"""
LLM Relay Bot -- Local development entry point (polling mode).

Usage:
    python -m telegram_bot

For Docker production (webhook mode), use webhook_main.py instead.
This module handles:
  - Loading .env from the project root
  - Building the PTB Application with post_init/post_shutdown
  - Initializing bot_data (classifier, config, optional Redis, Claude client)
  - Initializing persistence (RedisPersistence for ConversationHandler state)
  - Initializing session_manager, project_context, router, episodic manager
  - Registering handlers
  - Starting long-polling

Section 4 changes:
  - Redis client created BEFORE Application.builder() (persistence requirement)
  - RedisPersistence wired into builder
  - SessionManager, ProjectContext, MessageRouter, EpisodicManager in post_init
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from telegram.ext import Application

from telegram_bot.config import BotConfig

logger = logging.getLogger(__name__)

# Module-level Redis client — must exist before Application.builder()
_redis_client = None

# Update types the bot handles -- everything else is ignored by Telegram
ALLOWED_UPDATES = ["message", "callback_query", "edited_message"]


def _load_dotenv() -> None:
    """Load .env from the project root (llm-relay/)."""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(this_dir)
    env_path = os.path.join(project_root, ".env")
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


async def post_init(application: Application) -> None:
    """Called after Application.initialize(), before first update is processed.

    Stores the pre-created Redis client, creates Claude client (dual-mode),
    classifier, pipeline adapter, session manager, project context, router,
    and episodic manager.
    """
    config: BotConfig = application.bot_data["config"]

    # --- Redis client (already created before builder, stored in module-level var) ---
    redis_client = _redis_client
    application.bot_data["redis"] = redis_client

    # --- Claude client (dual-mode) ---
    if config.is_vps_mode:
        from telegram_bot.claude_code_client import ClaudeCodeClient
        claude_client = ClaudeCodeClient(
            session_token=config.claude_max_session_token,
            workspace_path=config.workspace_path,
        )
        logger.info("Claude Code CLI client initialized (VPS mode)")
    else:
        from telegram_bot.api_backend import APIBackend
        claude_client = APIBackend(api_key=config.classifier_api_key)
        logger.info("APIBackend initialized (local/API mode)")

    application.bot_data["claude_client"] = claude_client

    # --- Classifier (always uses direct Anthropic API — Haiku) ---
    from multi_agent_v2.real_claude import RealClaudeClient
    from telegram_bot.classifier import MessageClassifier

    api_client = RealClaudeClient()
    classifier = MessageClassifier(api_client, model=config.classifier_model)
    application.bot_data["classifier"] = classifier

    # --- Pipeline adapter ---
    from telegram_bot.pipeline_adapter import PipelineAdapter
    application.bot_data["pipeline_adapter"] = PipelineAdapter(
        config=config,
        claude_client=claude_client,
        redis_client=redis_client,
    )

    # --- Session manager (Section 4) ---
    from telegram_bot.session_manager import SessionManager
    session_manager = SessionManager(redis_client)
    application.bot_data["session_manager"] = session_manager

    # --- Project context (triple files) ---
    from telegram_bot.project_context import ProjectContext
    project_context = ProjectContext(config.workspace_path)
    application.bot_data["project_context"] = project_context

    # --- Project registry ---
    from telegram_bot.project_registry import FilesystemProjectRegistry
    project_registry = FilesystemProjectRegistry(Path(config.workspace_path), redis_client=redis_client)
    application.bot_data["project_registry"] = project_registry

    # --- Message router ---
    from telegram_bot.routing import MessageRouter
    router = MessageRouter(
        classifier=classifier,
        session_manager=session_manager,
        project_registry=project_registry,
    )
    application.bot_data["router"] = router

    # --- Episodic manager ---
    from telegram_bot.episodic import EpisodicManager
    application.bot_data["episodic_manager"] = EpisodicManager(
        session_manager=session_manager,
        project_context=project_context,
    )

    # --- Section 5: Preview manager ---
    from telegram_bot.preview_manager import PreviewManager
    application.bot_data["preview_manager"] = PreviewManager(
        claude_client=claude_client,
        config=config,
        redis_client=redis_client,
    )

    # --- Section 5: Git manager ---
    from telegram_bot.git_manager import GitManager
    application.bot_data["git_manager"] = GitManager(
        workspace_path=config.workspace_path,
        github_pat=config.github_pat,
    )

    # --- Section 5: Failure handlers ---
    from telegram_bot.failure_handlers import FailureHandlers
    application.bot_data["failure_handlers"] = FailureHandlers(
        redis_client=redis_client,
        claude_client=claude_client,
        session_manager=session_manager,
        project_context=project_context,
        config=config,
    )

    # --- Section 5: Health monitor ---
    if config.is_vps_mode and config.bot_owner_id:
        from telegram_bot.health_monitor import HealthMonitor
        monitor = HealthMonitor(
            bot=application.bot,
            owner_id=config.bot_owner_id,
            redis_client=redis_client,
        )
        monitor.start()
        application.bot_data["health_monitor"] = monitor


async def post_shutdown(application: Application) -> None:
    """Called after Application.shutdown(). Clean up connections."""
    # Stop health monitor
    monitor = application.bot_data.get("health_monitor")
    if monitor:
        await monitor.stop()
        logger.info("Health monitor stopped")

    # Stop active preview
    preview_mgr = application.bot_data.get("preview_manager")
    if preview_mgr:
        await preview_mgr.stop()

    # Close Redis
    redis_client = application.bot_data.get("redis")
    if redis_client:
        await redis_client.aclose()
        logger.info("Redis connection closed")


def build_application(config: BotConfig) -> Application:
    """Build the PTB Application for polling mode with lifecycle callbacks.

    Section 4: Redis client is created BEFORE the builder because
    persistence must exist at build time (PTB calls get_conversations
    during construction to load existing state).
    """
    global _redis_client

    builder = (
        Application.builder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )

    # Create Redis client for persistence (if configured)
    if config.redis_url:
        try:
            import redis.asyncio as aioredis
            _redis_client = aioredis.from_url(
                config.redis_url,
                decode_responses=True,
                max_connections=20,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            from telegram_bot.persistence.redis_persistence import RedisPersistence
            persistence = RedisPersistence(_redis_client)
            builder = builder.persistence(persistence)
            logger.info("Redis persistence configured: %s", config.redis_url)
        except Exception as e:
            logger.warning("Redis persistence setup failed, using in-memory: %s", e)
            _redis_client = None

    return builder.build()


def register_handlers(app: Application) -> None:
    """Register the main ConversationHandler with all states and transitions."""
    from telegram_bot.conversation import create_conversation_handler

    has_persistence = _redis_client is not None
    conv_handler = create_conversation_handler(persistent=has_persistence)
    app.add_handler(conv_handler)


def main() -> None:
    """Entry point for local development: load config, build app, start polling."""
    _load_dotenv()

    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        config = BotConfig.from_env()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    app = build_application(config)
    # Store config as BotConfig object (not dict) so handlers use config.field
    app.bot_data["config"] = config
    register_handlers(app)

    logger.info("Starting polling mode (local development)")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=ALLOWED_UPDATES,
        poll_interval=0.5,
        timeout=10,
    )


if __name__ == "__main__":
    main()
