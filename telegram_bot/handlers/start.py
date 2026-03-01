# telegram_bot/handlers/start.py
"""
Entry point handlers: /start, /help, /cancel, freeform message routing,
intent disambiguation, and timeout handling.

handle_freeform_message is the most important function in Phase 1.
Every non-command text message and every transcribed voice message
passes through it.
"""

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.classifier import Classification, Intent, MessageClassifier
from telegram_bot.keyboards.clarification import (
    build_full_intent_keyboard,
    build_intent_clarification_keyboard,
)
from telegram_bot.message_utils import send_long_message
from telegram_bot.states import BotState

logger = logging.getLogger(__name__)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start command. Sends welcome message and resets state."""
    context.user_data.clear()
    await update.message.reply_text(
        "\U0001f44b Hey! Relay Bot is ready.\n\n"
        "Just tell me what you need in plain English. Some examples:\n\n"
        '\u2022 "Build me a landing page for the new product line"\n'
        '\u2022 "Fix the mobile layout on the blog"\n'
        '\u2022 "How does the auth system work in the dashboard?"\n'
        '\u2022 "Research what competitors are doing with AI"\n\n'
        "Or use commands: /help, /cost, /status, /sessions, /health, /cancel"
    )
    return BotState.IDLE


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /help command."""
    await send_long_message(
        update.effective_chat.id,
        context,
        text=(
            "*Available Commands*\n\n"
            "/start \u2014 Reset and show welcome message\n"
            "/help \u2014 Show this help text\n"
            "/cost \u2014 Show today's and monthly cost breakdown\n"
            "/status \u2014 Show current build status\n"
            "/sessions \u2014 List recent project sessions\n"
            "/health \u2014 Comprehensive system health check\n"
            "/cancel \u2014 Cancel current operation and return to idle\n\n"
            "*You can also just type in plain English:*\n\n"
            '"Build me a..." \u2014 Start a new project\n'
            '"Fix the..." / "Change the..." \u2014 Edit an existing project\n'
            '"How does..." / "Explain..." \u2014 Ask about built code\n'
            '"Research..." / "Find out..." \u2014 Gather information\n'
            '"Create a Google Doc..." \u2014 External service actions'
        ),
    )
    return BotState.IDLE


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel command. Cancels any active pipeline and resets."""
    task = context.user_data.get("pipeline_task")
    if task and not task.done():
        task.cancel()
        await update.message.reply_text(
            "\u23f9 Cancelling the current build. This may take a moment..."
        )
    else:
        await update.message.reply_text("\U0001f44d Ready for your next request.")

    # Clear conversation state but preserve session history
    keys_to_keep = {"session_history"}
    keys_to_remove = [k for k in context.user_data if k not in keys_to_keep]
    for k in keys_to_remove:
        del context.user_data[k]

    return BotState.IDLE


async def handle_freeform_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    override_text: Optional[str] = None,
) -> int:
    """Route any non-command text message to the appropriate handler.

    Section 4: Uses MessageRouter when available for project-aware routing.
    Falls back to direct classifier if router isn't initialized.

    The override_text parameter exists for voice messages: after Whisper
    transcribes the audio, voice.py calls this function with the
    transcribed text instead of the (nonexistent) message text.

    Note on mid-flow detection: Since ConversationHandler routes mid-flow
    messages to state-specific handlers (not entry_points), this function
    only fires from IDLE or AWAITING_DELIVERY_ACTION states.
    """
    message_text = override_text or (update.message.text if update.message else "")

    if not message_text or not message_text.strip():
        return BotState.IDLE

    context.user_data["original_message"] = message_text

    # Section 4: Use MessageRouter for project-aware routing
    router = context.bot_data.get("router")
    if router:
        return await _route_via_router(router, update, context, message_text)

    # Fallback: direct classifier (pre-Section 4 path)
    classifier: MessageClassifier = context.bot_data["classifier"]
    classification = await classifier.classify(message_text)

    context.user_data["last_classification"] = classification

    logger.info(
        "Classified: intent=%s confidence=%.2f latency=%.0fms msg=%s",
        classification.intent.value,
        classification.confidence,
        classification.latency_ms,
        message_text[:80],
    )

    if classification.needs_clarification:
        keyboard = build_intent_clarification_keyboard(classification)
        truncated = message_text[:100]
        ellipsis = "..." if len(message_text) > 100 else ""

        await send_long_message(
            update.effective_chat.id,
            context,
            text=(
                f"\U0001f914 I'm not quite sure what you'd like me to do.\n\n"
                f'Your message: "{truncated}{ellipsis}"\n\n'
                f"What did you have in mind?"
            ),
            reply_markup=keyboard,
        )
        return BotState.AWAITING_INTENT_CLARIFICATION

    return await _route_by_intent(classification, update, context, message_text)


async def _route_via_router(
    router,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Route using the Section 4 MessageRouter (project + session aware).

    The router classifies and determines the path in one call, enriching
    the decision with project context and session history.
    """
    decision = await router.route(message_text, context.user_data)

    # Store routing decision for downstream handlers
    context.user_data["last_routing_decision"] = {
        "path": decision.path,
        "project_name": decision.project_name,
        "confidence": decision.confidence,
    }
    if decision.project_name:
        context.user_data["last_project"] = decision.project_name
    if decision.classification:
        context.user_data["last_classification"] = decision.classification

    logger.info(
        "Routed: path=%s project=%s confidence=%s msg=%s",
        decision.path,
        decision.project_name or "(none)",
        decision.confidence,
        message_text[:80],
    )

    # Low confidence — ask the user to clarify
    if decision.path == "AMBIGUOUS" or (
        decision.classification and decision.classification.needs_clarification
    ):
        classification = decision.classification
        if classification:
            keyboard = build_intent_clarification_keyboard(classification)
        else:
            keyboard = build_full_intent_keyboard()

        truncated = message_text[:100]
        ellipsis = "..." if len(message_text) > 100 else ""
        await send_long_message(
            update.effective_chat.id,
            context,
            text=(
                f"\U0001f914 I'm not quite sure what you'd like me to do.\n\n"
                f'Your message: "{truncated}{ellipsis}"\n\n'
                f"What did you have in mind?"
            ),
            reply_markup=keyboard,
        )
        return BotState.AWAITING_INTENT_CLARIFICATION

    # Route by path
    match decision.path:
        case "PATH_A":
            from telegram_bot.handlers.edit import start_project_selection
            return await start_project_selection(update, context, message_text)

        case "PATH_B":
            # Existing project — store context, then route to build
            if decision.project_name:
                context.user_data["selected_project"] = decision.project_name
            if decision.session:
                context.user_data["last_session_id"] = decision.session.session_id
            from telegram_bot.handlers.build import start_critical_questions
            return await start_critical_questions(update, context, message_text)

        case "NEW_BUILD":
            import uuid
            context.user_data["session_id"] = str(uuid.uuid4())
            from telegram_bot.handlers.build import start_critical_questions
            return await start_critical_questions(update, context, message_text)

        case "PATH_C":
            from telegram_bot.handlers.question import handle_question
            return await handle_question(update, context, message_text)

        case "RESEARCH":
            from telegram_bot.handlers.research import handle_research
            return await handle_research(update, context, message_text)

        case "EXTERNAL":
            from telegram_bot.handlers.external import handle_external
            return await handle_external(update, context, message_text)

        case "CONVERSATIONAL":
            from telegram_bot.handlers.conversational import handle_conversational
            return await handle_conversational(update, context, message_text)

        case _:
            logger.error("Unknown routing path: %s", decision.path)
            if update.message:
                await update.message.reply_text(
                    "I'm not sure how to handle that. Could you rephrase?"
                )
            return BotState.IDLE


async def _route_by_intent(
    classification: Classification,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str,
) -> int:
    """Route a classified message to its intent-specific handler.

    Extracted as a separate function so handle_intent_selection (when
    the user taps a button on the clarification keyboard) can reuse
    it without duplicating the routing logic.
    """
    intent = classification.intent

    if intent == Intent.NEW_BUILD:
        import uuid

        context.user_data["session_id"] = str(uuid.uuid4())
        from telegram_bot.handlers.build import start_critical_questions

        return await start_critical_questions(update, context, message_text)

    elif intent == Intent.EDIT_FIX:
        from telegram_bot.handlers.edit import start_project_selection

        return await start_project_selection(update, context, message_text)

    elif intent == Intent.QUESTION:
        from telegram_bot.handlers.question import handle_question

        return await handle_question(update, context, message_text)

    elif intent == Intent.RESEARCH:
        from telegram_bot.handlers.research import handle_research

        return await handle_research(update, context, message_text)

    elif intent == Intent.EXTERNAL_ACTION:
        from telegram_bot.handlers.external import handle_external

        return await handle_external(update, context, message_text)

    elif intent == Intent.CONVERSATIONAL:
        from telegram_bot.handlers.conversational import handle_conversational

        return await handle_conversational(update, context, message_text)

    else:
        logger.error("Unknown intent: %s", intent)
        if update.message:
            await update.message.reply_text(
                "I'm not sure how to handle that. Could you rephrase?"
            )
        return BotState.IDLE


async def handle_intent_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Called when user taps an intent button on the clarification keyboard.

    Callback data format: "intent_{INTENT_NAME}" or "intent_OTHER"
    """
    query = update.callback_query
    await query.answer()
    action = query.data.replace("intent_", "")

    if action == "OTHER":
        keyboard = build_full_intent_keyboard()
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=keyboard,
        )
        return BotState.AWAITING_INTENT_CLARIFICATION

    # User selected a specific intent — create a forced classification
    forced_intent = Intent(action)
    original_message = context.user_data.get("original_message", "")

    forced_classification = Classification(
        intent=forced_intent,
        confidence=1.0,
        secondary_intent=None,
        raw_message=original_message,
        latency_ms=0.0,
        needs_clarification=False,
    )
    context.user_data["last_classification"] = forced_classification

    await query.edit_message_text(
        f"Got it \u2014 routing as: {forced_intent.value.replace('_', ' ').title()}"
    )
    return await _route_by_intent(
        forced_classification, update, context, original_message
    )


async def handle_timeout(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle conversation timeout. Sends a friendly message and resets."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id:
        await context.bot.send_message(
            chat_id,
            "\u23f0 No worries \u2014 just send another message when you're ready.",
        )
    return BotState.IDLE
