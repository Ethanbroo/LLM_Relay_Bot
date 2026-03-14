"""
FastAPI router for the Telegram Mini App API.

Provides a REST endpoint that mirrors the bot's freeform message handling:
classify -> route -> respond. Auth is validated via Telegram's initData
HMAC-SHA256 signature (signed by Telegram using the bot token).

Endpoints:
  POST /api/chat   — Classify and respond to a user message
  GET  /api/status/{task_id} — Poll long-running task status (future)
  POST /api/credentials — Save an encrypted credential
  GET  /api/credentials — List saved credentials (no passwords)
  DELETE /api/credentials/{credential_id} — Delete a credential
  GET  /api/settings — Get user settings (safety tier + role)
  PUT  /api/settings — Update safety tier
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from telegram_bot.auth import ALLOWED_USER_IDS, BOT_OWNER_ID
from telegram_bot.classifier import Intent

logger = logging.getLogger(__name__)

# Max age for initData validation (5 minutes)
INIT_DATA_MAX_AGE_SECONDS = 300


# ── Request/Response Models ─────────────────────────────────


class ChatRequest(BaseModel):
    """Request body for POST /api/chat."""
    message: str
    init_data: str = ""


class ChatResponse(BaseModel):
    """Response body from POST /api/chat."""
    response: str
    intent: str = ""
    confidence: float = 0.0
    task_id: str | None = None


class CredentialSaveRequest(BaseModel):
    """Request body for POST /api/credentials."""
    domain: str
    username: str
    password: str
    init_data: str = ""


class CredentialResponse(BaseModel):
    """Response for credential list items."""
    credential_id: str
    domain: str
    username: str
    created_at: float


class SettingsUpdateRequest(BaseModel):
    """Request body for PUT /api/settings."""
    safety_tier: str
    init_data: str = ""


# ── Auth Helpers ────────────────────────────────────────────


def validate_webapp_init_data(init_data: str, bot_token: str) -> dict:
    """Validate Telegram WebApp initData using HMAC-SHA256.

    Per https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app:
    1. Parse init_data as URL query string
    2. Extract and remove 'hash' parameter
    3. Sort remaining params alphabetically
    4. Join as "key=value" with newlines
    5. HMAC-SHA256 with secret_key = HMAC-SHA256("WebAppData", bot_token)
    6. Compare computed hash with extracted hash

    Returns parsed user data dict on success.
    Raises HTTPException on validation failure.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")

    parsed = parse_qs(init_data, keep_blank_values=True)
    # parse_qs returns lists; flatten to single values
    flat = {k: v[0] for k, v in parsed.items()}

    received_hash = flat.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    # Check auth_date freshness
    auth_date_str = flat.get("auth_date", "0")
    try:
        auth_date = int(auth_date_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid auth_date")

    age = int(time.time()) - auth_date
    if age > INIT_DATA_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="initData expired")

    # Build data-check-string
    data_check_string = "\n".join(
        f"{k}={flat[k]}" for k in sorted(flat.keys())
    )

    # Compute HMAC
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid initData signature")

    # Extract user info
    user_data = {}
    if "user" in flat:
        try:
            user_data = json.loads(flat["user"])
        except json.JSONDecodeError:
            raise HTTPException(status_code=401, detail="Invalid user data")

    return user_data


def _check_user_authorized(user_data: dict) -> int:
    """Check if the user from initData is in the allowlist.

    Returns user_id on success, raises HTTPException on failure.
    """
    user_id = user_data.get("id", 0)
    if not user_id:
        raise HTTPException(status_code=403, detail="No user ID in initData")

    if user_id not in ALLOWED_USER_IDS and user_id != BOT_OWNER_ID:
        logger.warning("Webapp: unauthorized user_id=%d", user_id)
        raise HTTPException(status_code=403, detail="Access denied")

    return user_id


def _authenticate_request(
    init_data: str, bot_token: str, *, require_auth: bool = True
) -> int:
    """Validate initData and return user_id.

    When require_auth=False and WEBAPP_DEV_MODE is set, returns 0 (dev user).
    When require_auth=True, auth is always enforced (credential/settings endpoints).
    """
    dev_mode = bool(os.environ.get("WEBAPP_DEV_MODE"))

    if not require_auth and dev_mode:
        return 0  # Dev mode user

    if not init_data:
        if dev_mode:
            return 0
        raise HTTPException(status_code=401, detail="Missing initData")

    user_data = validate_webapp_init_data(init_data, bot_token)
    return _check_user_authorized(user_data)


# ── Router Factory ──────────────────────────────────────────


def create_webapp_router(ptb_app) -> APIRouter:
    """Create the webapp API router with access to the PTB application.

    The ptb_app reference is needed to access bot_data (classifier,
    pipeline_adapter, config, etc.) which are initialized in post_init.

    Creates a fresh APIRouter each time to avoid stale closures in tests.
    """
    router = APIRouter(prefix="/api", tags=["webapp"])

    # ── Chat ────────────────────────────────────────────────

    @router.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest, request: Request):
        """Classify a message and return the appropriate response."""
        if not req.message or not req.message.strip():
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        bot_token = ptb_app.bot_data["config"].bot_token
        _authenticate_request(req.init_data, bot_token, require_auth=False)

        message_text = req.message.strip()

        # Classify
        classifier = ptb_app.bot_data.get("classifier")
        if not classifier:
            raise HTTPException(status_code=503, detail="Classifier not initialized")

        classification = await classifier.classify(message_text)

        intent = classification.intent
        confidence = classification.confidence

        logger.info(
            "Webapp chat: intent=%s confidence=%.2f msg=%s",
            intent.value, confidence, message_text[:80],
        )

        # Route to handler
        pipeline = ptb_app.bot_data.get("pipeline_adapter")
        if not pipeline:
            raise HTTPException(status_code=503, detail="Pipeline not initialized")

        try:
            if intent == Intent.CONVERSATIONAL:
                response_text = await pipeline.run_conversational(message_text)

            elif intent == Intent.QUESTION:
                response_text = await pipeline.run_question(message_text)

            elif intent == Intent.RESEARCH:
                web_researcher = ptb_app.bot_data.get("web_researcher")
                if web_researcher and web_researcher.available:
                    research_result = await web_researcher.research(message_text)
                    enriched_query = message_text
                    if research_result.summary:
                        enriched_query = (
                            f"{message_text}\n\n"
                            f"Web research findings:\n{research_result.summary}"
                        )
                    response_text = await pipeline.run_research(enriched_query)
                else:
                    response_text = await pipeline.run_research(message_text)

            elif intent in (Intent.NEW_BUILD, Intent.EDIT_FIX, Intent.BROWSER_TASK,
                            Intent.EXTERNAL_ACTION):
                intent_labels = {
                    Intent.NEW_BUILD: "build",
                    Intent.EDIT_FIX: "edit",
                    Intent.BROWSER_TASK: "browser task",
                    Intent.EXTERNAL_ACTION: "external action",
                }
                label = intent_labels.get(intent, "task")
                return ChatResponse(
                    response=(
                        f"I understand you want to start a {label}. "
                        f"This requires the full pipeline which runs through "
                        f"the Telegram chat.\n\n"
                        f"Send this same message in the Telegram chat and "
                        f"I'll get started right away."
                    ),
                    intent=intent.value,
                    confidence=confidence,
                )

            else:
                response_text = await pipeline.run_conversational(message_text)

        except Exception as e:
            logger.error("Webapp chat handler error: %s", e, exc_info=True)
            response_text = "Sorry, something went wrong processing your request."

        return ChatResponse(
            response=response_text,
            intent=intent.value,
            confidence=confidence,
        )

    # ── Status ──────────────────────────────────────────────

    @router.get("/status/{task_id}")
    async def task_status(task_id: str):
        """Poll status of a long-running task (future implementation)."""
        return {"task_id": task_id, "status": "not_implemented"}

    # ── Credentials ─────────────────────────────────────────

    @router.post("/credentials", response_model=CredentialResponse)
    async def save_credential(req: CredentialSaveRequest, request: Request):
        """Save an encrypted credential for the authenticated user."""
        bot_token = ptb_app.bot_data["config"].bot_token
        user_id = _authenticate_request(req.init_data, bot_token)

        redis_client = ptb_app.bot_data.get("redis")
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not available")

        if not req.domain or not req.domain.strip():
            raise HTTPException(status_code=400, detail="Domain is required")
        if not req.username or not req.username.strip():
            raise HTTPException(status_code=400, detail="Username is required")
        if not req.password:
            raise HTTPException(status_code=400, detail="Password is required")

        from telegram_bot.user_credential_vault import UserCredentialVault
        from telegram_bot.permissions import is_admin
        vault = UserCredentialVault(redis_client)

        credential_id = await vault.save_credential(
            user_id=user_id,
            domain=req.domain.strip(),
            username=req.username.strip(),
            password=req.password,
            is_admin=is_admin(user_id),
        )

        from telegram_bot.user_credential_vault import _normalize_domain
        normalized_domain = _normalize_domain(req.domain)

        # Resolve any pending credential request from the browser agent
        credential_request_manager = ptb_app.bot_data.get(
            "credential_request_manager"
        )
        if credential_request_manager:
            credential_request_manager.resolve_for_domain(
                user_id, normalized_domain
            )

        return CredentialResponse(
            credential_id=credential_id,
            domain=normalized_domain,
            username=req.username.strip(),
            created_at=time.time(),
        )

    @router.get("/credentials")
    async def list_credentials(request: Request):
        """List saved credentials (domain + username only, no passwords)."""
        init_data = request.query_params.get("init_data", "")
        bot_token = ptb_app.bot_data["config"].bot_token
        user_id = _authenticate_request(init_data, bot_token)

        redis_client = ptb_app.bot_data.get("redis")
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not available")

        from telegram_bot.user_credential_vault import UserCredentialVault
        vault = UserCredentialVault(redis_client)
        creds = await vault.list_credentials(user_id)

        return [
            CredentialResponse(
                credential_id=c.credential_id,
                domain=c.domain,
                username=c.username,
                created_at=c.created_at,
            )
            for c in creds
        ]

    @router.delete("/credentials/{credential_id}")
    async def delete_credential(credential_id: str, request: Request):
        """Delete a saved credential."""
        init_data = request.query_params.get("init_data", "")
        bot_token = ptb_app.bot_data["config"].bot_token
        user_id = _authenticate_request(init_data, bot_token)

        redis_client = ptb_app.bot_data.get("redis")
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not available")

        from telegram_bot.user_credential_vault import UserCredentialVault
        vault = UserCredentialVault(redis_client)
        deleted = await vault.delete_credential(user_id, credential_id)

        if not deleted:
            raise HTTPException(status_code=404, detail="Credential not found")
        return {"deleted": True}

    # ── Settings ────────────────────────────────────────────

    @router.get("/settings")
    async def get_settings(request: Request):
        """Get user's current settings (safety tier + role)."""
        init_data = request.query_params.get("init_data", "")
        bot_token = ptb_app.bot_data["config"].bot_token
        user_id = _authenticate_request(init_data, bot_token)

        redis_client = ptb_app.bot_data.get("redis")
        from telegram_bot.permissions import PermissionsManager
        manager = PermissionsManager(redis_client)
        settings = await manager.get_settings(user_id)
        return settings

    @router.put("/settings")
    async def update_settings(req: SettingsUpdateRequest, request: Request):
        """Update user's safety tier."""
        bot_token = ptb_app.bot_data["config"].bot_token
        user_id = _authenticate_request(req.init_data, bot_token)

        from telegram_bot.permissions import PermissionsManager, SafetyTier
        redis_client = ptb_app.bot_data.get("redis")
        manager = PermissionsManager(redis_client)

        try:
            tier = SafetyTier(req.safety_tier)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid safety tier: {req.safety_tier}",
            )

        await manager.set_safety_tier(user_id, tier)
        return {"safety_tier": tier.value}

    return router
