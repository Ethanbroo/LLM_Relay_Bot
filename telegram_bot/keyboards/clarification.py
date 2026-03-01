"""
Clarifying question keyboards.

Two keyboard types:
1. Intent clarification — shown when classifier confidence < 0.60
2. Critical question — shown for each clarifying question from the
   Critical Thinking Agent, with a default answer button.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_bot.classifier import Intent, Classification


# Human-readable labels for each intent
INTENT_LABELS = {
    Intent.NEW_BUILD: "\U0001f528 Build something new",
    Intent.EDIT_FIX: "\u270f\ufe0f Edit/fix existing project",
    Intent.QUESTION: "\u2753 Ask a question",
    Intent.RESEARCH: "\U0001f50d Research something",
    Intent.EXTERNAL_ACTION: "\U0001f4e4 External action (Docs, Calendar, etc.)",
    Intent.CONVERSATIONAL: "\U0001f4ac Just chatting",
}


def build_intent_clarification_keyboard(
    classification: Classification,
) -> InlineKeyboardMarkup:
    """Build the keyboard shown when classifier confidence is low.

    Shows the primary classified intent, the secondary intent (if any),
    and a "Something else" button that presents all remaining options.

    Callback data format: "intent_{INTENT_NAME}"
    Example: "intent_NEW_BUILD", "intent_EDIT_FIX"
    All fit well within the 64-byte limit.
    """
    buttons = []

    # Primary intent (even at low confidence, it's the best guess)
    buttons.append([InlineKeyboardButton(
        text=f"{INTENT_LABELS[classification.intent]} (best guess)",
        callback_data=f"intent_{classification.intent.value}",
    )])

    # Secondary intent if available and different from primary
    if (classification.secondary_intent
            and classification.secondary_intent != classification.intent):
        buttons.append([InlineKeyboardButton(
            text=INTENT_LABELS[classification.secondary_intent],
            callback_data=f"intent_{classification.secondary_intent.value}",
        )])

    # "Something else" shows a second keyboard with all remaining intents
    buttons.append([InlineKeyboardButton(
        text="\U0001f504 Something else",
        callback_data="intent_OTHER",
    )])

    return InlineKeyboardMarkup(buttons)


def build_full_intent_keyboard() -> InlineKeyboardMarkup:
    """Build a keyboard showing all six intent options.

    Shown when the user taps "Something else" on the clarification keyboard.
    """
    buttons = []
    for intent in Intent:
        buttons.append([InlineKeyboardButton(
            text=INTENT_LABELS[intent],
            callback_data=f"intent_{intent.value}",
        )])
    return InlineKeyboardMarkup(buttons)


# Backward-compatible alias used by tests
build_all_intents_keyboard = build_full_intent_keyboard


def critical_question_keyboard(
    question_index: int,
    total_questions: int,
    default_answer: str,
) -> InlineKeyboardMarkup:
    """Keyboard for a single clarifying question.

    The default answer button lets the user skip with one tap for
    questions where the obvious answer is correct.

    Callback data format: "defans_{question_index}"
    The actual default text is stored in context.user_data, not in
    callback_data (which would exceed 64 bytes for long answers).
    """
    # Truncate default for display on button
    display_default = default_answer[:40]
    if len(default_answer) > 40:
        display_default += "..."

    buttons = [
        [InlineKeyboardButton(
            text=f'Use default: "{display_default}"',
            callback_data=f"defans_{question_index}",
        )],
    ]

    # Skip remaining questions button (only after the first question)
    if question_index > 0:
        buttons.append([InlineKeyboardButton(
            text="\u23e9 Use defaults for remaining questions",
            callback_data="defans_all",
        )])

    return InlineKeyboardMarkup(buttons)
