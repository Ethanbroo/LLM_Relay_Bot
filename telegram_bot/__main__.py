"""Allow running the bot as: python -m telegram_bot

Dispatches to webhook mode (FastAPI + uvicorn) or polling mode
based on the BOT_MODE environment variable.
"""

import os

mode = os.environ.get("BOT_MODE", "polling").lower()

if mode == "webhook":
    from webhook_main import main
else:
    from telegram_bot.app import main

main()
