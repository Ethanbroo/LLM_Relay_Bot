"""
ConversationHandler definition with all states and transitions.

This is a single, flat ConversationHandler (not nested). Nested handlers
add complexity that isn't justified for a single-user system. All states
are in one handler, and transitions are managed by returning the
appropriate BotState integer from each callback.

Key configuration choices:
- per_chat=True, per_user=True: Each user in each chat gets independent state.
- per_message=False: State is per-conversation, not per-message.
- conversation_timeout=1800: 30-minute global timeout.
- allow_reentry=True: A user in any state can restart by sending /start.
- persistent=True: State persisted via RedisPersistence (Section 4).
"""

from __future__ import annotations

from telegram.ext import (
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from telegram_bot.states import BotState
from telegram_bot.auth import restricted
from telegram_bot.handlers import (
    start,
    build,
    edit,
    voice,
    file_inbound,
    admin,
)

# These are imported for completeness per the plan — they are stubs
# that will be fully implemented in later phases.
from telegram_bot.handlers import (  # noqa: F401
    question,
    research,
    external,
    conversational,
)


def create_conversation_handler(persistent: bool = True) -> ConversationHandler:
    """Build the main ConversationHandler with all states and transitions.

    Args:
        persistent: Whether to persist state via RedisPersistence. Must be False
                    when the Application has no persistence backend configured.
    """
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", restricted(start.handle_start)),
            CommandHandler("help", restricted(start.handle_help)),
            CommandHandler("cost", restricted(admin.handle_cost)),
            CommandHandler("status", restricted(admin.handle_status)),
            CommandHandler("sessions", restricted(admin.handle_sessions)),
            CommandHandler("health", restricted(admin.handle_health)),
            CommandHandler("cancel", restricted(start.handle_cancel)),
            # Catch-all for any message when not in a conversation
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                restricted(start.handle_freeform_message),
            ),
            MessageHandler(filters.VOICE, restricted(voice.handle_voice)),
            MessageHandler(
                filters.Document.ALL | filters.PHOTO,
                restricted(file_inbound.handle_inbound_file),
            ),
        ],
        states={
            # --- Intent disambiguation ---
            BotState.AWAITING_INTENT_CLARIFICATION: [
                CallbackQueryHandler(
                    start.handle_intent_selection,
                    pattern=r"^intent_",
                ),
            ],
            BotState.AWAITING_BUILD_VS_EDIT: [
                CallbackQueryHandler(
                    build.handle_build_vs_edit_choice,
                    pattern=r"^bve_",
                ),
            ],
            BotState.AWAITING_PROJECT_SELECTION: [
                CallbackQueryHandler(
                    edit.handle_project_selected,
                    pattern=r"^proj_",
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit.handle_project_typed,
                ),
            ],

            # --- Pipeline clarification ---
            BotState.AWAITING_CRITICAL_QUESTIONS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    build.handle_question_answers,
                ),
                CallbackQueryHandler(
                    build.handle_default_answer,
                    pattern=r"^defans_",
                ),
            ],
            BotState.AWAITING_ANCHOR_APPROVAL: [
                CallbackQueryHandler(
                    build.handle_anchor_decision,
                    pattern=r"^anchor_",
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    build.handle_anchor_edit,
                ),
            ],

            # --- Execution ---
            BotState.EXECUTING: [
                CallbackQueryHandler(
                    build.handle_execution_control,
                    pattern=r"^exec_",
                ),
                # Continue build button appears after pipeline hits a limit
                CallbackQueryHandler(
                    build.handle_continue_build,
                    pattern=r"^cont_",
                ),
                # Delivery buttons may appear when pipeline completes in background
                CallbackQueryHandler(
                    build.handle_delivery_action,
                    pattern=r"^dlvr_",
                ),
                # Allow new messages even during execution (they queue)
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    build.handle_message_during_execution,
                ),
            ],
            BotState.AWAITING_CHECKPOINT_APPROVAL: [
                CallbackQueryHandler(
                    build.handle_checkpoint_decision,
                    pattern=r"^ckpt_",
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    build.handle_checkpoint_text,
                ),
            ],
            BotState.AWAITING_HUMAN_DECISION: [
                CallbackQueryHandler(
                    build.handle_human_decision,
                    pattern=r"^hdec_",
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    build.handle_human_decision_text,
                ),
            ],

            # --- Delivery ---
            BotState.AWAITING_DELIVERY_ACTION: [
                CallbackQueryHandler(
                    build.handle_delivery_action,
                    pattern=r"^dlvr_",
                ),
                # New messages in delivery state start a new classification
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    restricted(start.handle_freeform_message),
                ),
            ],
            BotState.AWAITING_QUICK_FIX_CONFIRM: [
                CallbackQueryHandler(
                    edit.handle_quick_fix_decision,
                    pattern=r"^qfix_",
                ),
            ],
            BotState.AWAITING_CONTINUE_BUILD: [
                CallbackQueryHandler(
                    build.handle_continue_build,
                    pattern=r"^cont_",
                ),
                # Allow new messages while waiting for continue decision
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    restricted(start.handle_freeform_message),
                ),
            ],

            # --- Timeout ---
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, start.handle_timeout),
                CallbackQueryHandler(start.handle_timeout),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", restricted(start.handle_cancel)),
            CommandHandler("start", restricted(start.handle_start)),
            CommandHandler("help", restricted(start.handle_help)),
            CommandHandler("cost", restricted(admin.handle_cost)),
            CommandHandler("status", restricted(admin.handle_status)),
            CommandHandler("sessions", restricted(admin.handle_sessions)),
            CommandHandler("health", restricted(admin.handle_health)),
        ],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
        per_message=False,
        conversation_timeout=1800,  # 30 minutes global
        name="main_conversation",
        persistent=persistent,  # Section 4: True when RedisPersistence is wired
    )
