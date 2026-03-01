"""
FastAPI webhook server for VPS deployment.

Separated from bot.py for clean concern boundaries:
  - bot.py handles python-telegram-bot setup
  - webhook_server.py handles FastAPI + webhook endpoint

They share the PTB Application instance.
"""

import logging
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application

from telegram_bot.config import BotConfig

logger = logging.getLogger(__name__)


def create_webhook_app(ptb_app: Application, config: BotConfig) -> FastAPI:
    """Create a FastAPI app wired to the python-telegram-bot Application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Register webhook with Telegram on startup
        webhook_url = f"{config.webhook_url}/webhook/{config.bot_token}"
        await ptb_app.bot.set_webhook(
            url=webhook_url,
            secret_token=config.webhook_secret_token,
            drop_pending_updates=True,
        )
        logger.info("Webhook registered: %s/webhook/<TOKEN>", config.webhook_url)

        async with ptb_app:
            await ptb_app.start()
            yield
            await ptb_app.stop()

    fastapi_app = FastAPI(lifespan=lifespan)

    @fastapi_app.post(f"/webhook/{config.bot_token}")
    async def telegram_webhook(request: Request):
        """Receive updates from Telegram via webhook."""
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != config.webhook_secret_token:
            return Response(status_code=HTTPStatus.FORBIDDEN)

        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=HTTPStatus.OK)

    @fastapi_app.get("/healthcheck")
    async def healthcheck():
        """Used by Docker HEALTHCHECK and nginx."""
        redis_client = ptb_app.bot_data.get("redis")
        redis_ok = False
        if redis_client:
            try:
                redis_ok = await redis_client.ping()
            except Exception:
                redis_ok = False

        return {
            "status": "healthy" if redis_ok else "degraded",
            "redis": "connected" if redis_ok else "disconnected",
            "mode": "vps" if ptb_app.bot_data["config"].is_vps_mode else "local",
        }

    return fastapi_app
