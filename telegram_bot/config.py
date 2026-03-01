"""
Bot configuration dataclass and environment loading.

All Telegram bot configuration is loaded from environment variables.
The BotConfig dataclass validates required fields at startup so
misconfigurations fail fast instead of at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class BotConfig:
    """Immutable configuration for the Telegram bot.

    All values come from environment variables. The frozen=True ensures
    configuration cannot be accidentally modified at runtime.
    """

    # --- Required ---
    bot_token: str                    # Telegram bot token from BotFather
    bot_owner_id: int                 # Your Telegram user ID (always has access)
    # Haiku classifier uses direct API (not claude -p) because spawning
    # a subprocess adds 3-5s latency per call. At ~$1.20/month for 100
    # messages/day, this is cheaper than the user's time. Pipeline agents
    # in Phase 3 still use claude -p through the Max subscription.
    classifier_api_key: str

    # --- Mode ---
    mode: str = "polling"             # "polling" or "webhook"

    # --- Webhook (required if mode=webhook) ---
    domain: Optional[str] = None      # e.g., "relay.cardinalsales.ca"
    webhook_port: int = 8443
    webhook_path: str = "webhook"     # Will be prefixed with random UUID at startup
    webhook_secret_token: str = ""    # 256-char random string

    # --- User Access ---
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)

    # --- Classifier ---
    classifier_model: str = "claude-haiku-4-5-20251001"
    classifier_confidence_threshold: float = 0.60
    classifier_high_confidence: float = 0.85

    # --- Voice ---
    openai_api_key: str = ""          # For Whisper transcription

    # --- Timeouts (seconds) ---
    conversation_timeout: int = 1800        # 30 min global
    clarification_timeout: int = 300        # 5 min for disambiguation
    critical_questions_timeout: int = 900   # 15 min for answering questions
    checkpoint_timeout: int = 1800          # 30 min for execution checkpoints

    # --- Progress ---
    progress_update_interval: float = 2.0   # Seconds between progress edits

    # --- Redis (Docker production) ---
    redis_url: str = ""               # e.g., "redis://:password@redis:6379/0"

    # --- Workspace ---
    workspace_path: str = "/workspace"

    # --- New VPS Infrastructure fields (Section 2) ---
    claude_max_session_token: Optional[str] = None   # None = VPS mode unavailable
    code_server_password: Optional[str] = None       # None = code-server not configured
    github_pat: Optional[str] = None                 # None = GitHub integration disabled
    preview_timeout_seconds: int = 7200              # Default 2 hours
    token_budget_default: float = 10.00              # USD equivalent per project

    # --- Computed properties ---

    @property
    def is_vps_mode(self) -> bool:
        """True if running on VPS with Claude Code CLI available."""
        return self.claude_max_session_token is not None

    @property
    def use_mock_orchestrator(self) -> bool:
        """True if pipeline should use mock (local dev / testing)."""
        return not self.is_vps_mode

    @property
    def webhook_url(self) -> str:
        """Full webhook URL for Telegram."""
        return f"https://{self.domain}" if self.domain else ""

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Load configuration from environment variables.

        Raises ValueError if required variables are missing.
        """
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not bot_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is not set. "
                "Get a token from @BotFather on Telegram."
            )

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Get a key at https://console.anthropic.com/"
            )

        mode = os.environ.get("BOT_MODE", "polling").lower()
        if mode not in ("polling", "webhook"):
            raise ValueError(
                f"BOT_MODE must be 'polling' or 'webhook', got '{mode}'"
            )

        allowed_ids_raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
        allowed_ids = frozenset(
            int(uid.strip())
            for uid in allowed_ids_raw.split(",")
            if uid.strip().isdigit()
        )

        domain = os.environ.get("BOT_DOMAIN")
        webhook_secret = os.environ.get("WEBHOOK_SECRET_TOKEN", "")

        if mode == "webhook":
            if not domain:
                raise ValueError(
                    "BOT_DOMAIN is required in webhook mode."
                )
            if not webhook_secret:
                raise ValueError(
                    "WEBHOOK_SECRET_TOKEN is required in webhook mode. "
                    'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(192))"'
                )

        # New VPS fields — with safe defaults for local dev
        redis_url = os.environ.get("REDIS_URL", "")
        claude_max_session_token = os.environ.get("CLAUDE_MAX_SESSION_TOKEN")  # None if not set
        code_server_password = os.environ.get("CODE_SERVER_PASSWORD")
        github_pat = os.environ.get("GITHUB_PAT")
        preview_timeout_seconds = int(os.environ.get("PREVIEW_TIMEOUT_SECONDS", "7200"))
        token_budget_default = float(os.environ.get("TOKEN_BUDGET_DEFAULT", "10.00"))

        # --- Validation ---

        # Validate Redis URL format (if set)
        if redis_url and not redis_url.startswith("redis://"):
            raise ValueError(
                f"REDIS_URL must start with 'redis://', got: {redis_url[:20]}..."
            )

        # Validate budget is positive
        if token_budget_default <= 0:
            raise ValueError(
                f"TOKEN_BUDGET_DEFAULT must be positive, got: {token_budget_default}"
            )

        # Validate preview timeout is reasonable (60s to 24h)
        if not (60 <= preview_timeout_seconds <= 86400):
            raise ValueError(
                f"PREVIEW_TIMEOUT_SECONDS must be 60-86400, got: {preview_timeout_seconds}"
            )

        return cls(
            bot_token=bot_token,
            bot_owner_id=int(os.environ.get("TELEGRAM_BOT_OWNER_ID", "0")),
            classifier_api_key=anthropic_key,
            mode=mode,
            domain=domain,
            webhook_port=int(os.environ.get("WEBHOOK_PORT", "8443")),
            webhook_path=os.environ.get("WEBHOOK_PATH", "webhook"),
            webhook_secret_token=webhook_secret,
            allowed_user_ids=allowed_ids,
            classifier_model=os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5-20251001"),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            redis_url=redis_url,
            workspace_path=os.environ.get("WORKSPACE_PATH", "/workspace"),
            claude_max_session_token=claude_max_session_token,
            code_server_password=code_server_password,
            github_pat=github_pat,
            preview_timeout_seconds=preview_timeout_seconds,
            token_budget_default=token_budget_default,
        )
