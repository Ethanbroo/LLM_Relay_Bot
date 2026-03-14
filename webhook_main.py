"""
LLM Relay Bot — Production webhook entry point (Docker).

Wires python-telegram-bot (message handling) to FastAPI (webhook + healthcheck)
via the webhook_server module, with Redis and Claude client lifecycle
managed through post_init/post_shutdown callbacks.

Section 4 changes:
  - Redis client created BEFORE Application.builder() for persistence
  - RedisPersistence wired into builder
  - SessionManager, ProjectContext, MessageRouter, EpisodicManager in post_init

Entry point for the relay-bot container:
    CMD ["python", "webhook_main.py"]

For local development (polling mode), use:
    python -m telegram_bot
"""

import logging
import os
import sys
from pathlib import Path

import redis.asyncio as aioredis
import uvicorn
from telegram.ext import Application

from telegram_bot.config import BotConfig
from telegram_bot.conversation import create_conversation_handler
from telegram_bot.webhook_server import create_webhook_app

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Module-level Redis client — must exist before Application.builder()
_redis_client = None


async def post_init(application: Application) -> None:
    """Called after Application.initialize(), before first update is processed.

    Stores the pre-created Redis client, creates Claude client (dual-mode),
    classifier, pipeline adapter, session manager, project context, router,
    and episodic manager.
    """
    import traceback
    logger.info(">>> post_init STARTING <<<")
    print(">>> post_init STARTING <<<", flush=True)  # In case logger is broken
    try:
        await _post_init_inner(application)
        logger.info(">>> post_init COMPLETED SUCCESSFULLY <<<")
        print(">>> post_init COMPLETED SUCCESSFULLY <<<", flush=True)
    except Exception as e:
        logger.error(">>> post_init FAILED: %s <<<", e)
        logger.error(traceback.format_exc())
        print(f">>> post_init FAILED: {e} <<<", flush=True)
        print(traceback.format_exc(), flush=True)
        raise  # Re-raise so PTB knows initialization failed


async def _post_init_inner(application: Application) -> None:
    """Actual post_init logic, wrapped for error reporting."""
    config: BotConfig = application.bot_data["config"]

    # --- Redis client (already created before builder) ---
    redis_client = _redis_client
    if redis_client:
        try:
            await redis_client.ping()
            logger.info("Redis connected successfully")
        except Exception as e:
            logger.error("Redis ping failed in post_init: %s", e)
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

    # --- Classifier (Gemini Flash free tier if available, else Anthropic Haiku) ---
    from telegram_bot.classifier import MessageClassifier

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        from telegram_bot.gemini_client import GeminiClassifierClient
        classifier_client = GeminiClassifierClient(api_key=gemini_key)
        logger.info("Classifier using Gemini Flash (free tier)")
    else:
        from multi_agent_v2.real_claude import RealClaudeClient
        classifier_client = RealClaudeClient()
        logger.info("Classifier using Anthropic Haiku (no GEMINI_API_KEY set)")

    classifier = MessageClassifier(classifier_client, model=config.classifier_model)
    application.bot_data["classifier"] = classifier

    # --- Anthropic client (for Phase 1 critical thinking + build handler) ---
    anthropic_client = None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        try:
            import anthropic
            anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
            logger.info("Anthropic client initialized for Phase 1")
        except Exception as e:
            logger.warning("Failed to create Anthropic client: %s", e)
    application.bot_data["anthropic_client"] = anthropic_client

    # --- User credential vault ---
    from telegram_bot.user_credential_vault import UserCredentialVault
    credential_vault = UserCredentialVault(redis_client) if redis_client else None
    application.bot_data["credential_vault"] = credential_vault

    # --- Browser client ---
    from telegram_bot.browser_client import BrowserClient
    _browser_client = BrowserClient()
    application.bot_data["_browser_client"] = _browser_client

    # --- Credential request manager ---
    from telegram_bot.credential_request_manager import CredentialRequestManager
    cred_req_manager = CredentialRequestManager()
    application.bot_data["credential_request_manager"] = cred_req_manager

    # --- Pipeline adapter ---
    from telegram_bot.pipeline_adapter import PipelineAdapter
    application.bot_data["pipeline_adapter"] = PipelineAdapter(
        config=config,
        claude_client=claude_client,
        redis_client=redis_client,
        browser_client=_browser_client,
        credential_vault=credential_vault,
        credential_request_manager=cred_req_manager,
        anthropic_client=anthropic_client,
        bot=application.bot,
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
    if config.bot_owner_id:
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


def main() -> None:
    """Build PTB app, register handlers, launch via FastAPI + uvicorn."""
    global _redis_client

    try:
        config = BotConfig.from_env()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    # Create Redis client BEFORE builder (persistence must exist at build time)
    builder = (
        Application.builder()
        .token(config.bot_token)
        .updater(None)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )

    if config.redis_url:
        try:
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
            logger.warning("Redis persistence setup failed: %s", e)
            _redis_client = None

    ptb_app = builder.build()

    # Store config as BotConfig object (not dict) so handlers use config.field
    ptb_app.bot_data["config"] = config

    # Register the conversation handler (all states, all transitions)
    conv_handler = create_conversation_handler(persistent=_redis_client is not None)
    ptb_app.add_handler(conv_handler)

    # Create FastAPI app and run
    port = int(os.environ.get("PORT", "8000"))
    fastapi_app = create_webhook_app(ptb_app, config)

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
