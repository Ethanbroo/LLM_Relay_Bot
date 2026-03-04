"""
State enum and transition map for the conversation state machine.

All states the bot can be in during a conversation. States are grouped
by pipeline phase with gaps in numbering so future states can be inserted
without renumbering everything.

State Transition Map:
    IDLE → MessageClassifier → route to intent-specific state
    AWAITING_INTENT_CLARIFICATION → user taps button → re-route
    AWAITING_BUILD_VS_EDIT → New Build / Edit Existing
    AWAITING_PROJECT_SELECTION → project chosen → pipeline or quick fix
    AWAITING_CRITICAL_QUESTIONS → answers collected → Semantic Anchor
    AWAITING_ANCHOR_APPROVAL → Approve / Restart / Edit
    EXECUTING → pipeline running (pause/cancel/skip controls)
    AWAITING_CHECKPOINT_APPROVAL → Continue / Adjust
    AWAITING_HUMAN_DECISION → decision button or text
    AWAITING_DELIVERY_ACTION → View PR / Download / Deploy
    AWAITING_QUICK_FIX_CONFIRM → Approve / Retry / Revert
"""

from enum import IntEnum


class BotState(IntEnum):
    """All states the bot can be in during a conversation.

    States are grouped by pipeline phase. The numbering leaves gaps
    so future states can be inserted without renumbering everything.

    Group 0xx: Idle and classification
    Group 1xx: Clarification (intent disambiguation)
    Group 2xx: Intent clarification (Critical Thinking / Semantic Anchor)
    Group 3xx: Execution (pipeline running)
    Group 4xx: Review and delivery
    """

    # --- Group 0: Entry ---
    IDLE = 0                        # Waiting for any message

    # --- Group 1: Intent Disambiguation ---
    AWAITING_INTENT_CLARIFICATION = 10    # Classifier confidence too low
    AWAITING_BUILD_VS_EDIT = 11           # Ambiguous: new or edit?
    AWAITING_PROJECT_SELECTION = 12       # "Edit the project" — which one?

    # --- Group 2: Pipeline Clarification ---
    AWAITING_CRITICAL_QUESTIONS = 20      # Critical Thinking Agent asked questions
    AWAITING_ANCHOR_APPROVAL = 21         # Semantic anchor presented for approval

    # --- Group 3: Execution ---
    EXECUTING = 30                        # Pipeline is running
    AWAITING_CHECKPOINT_APPROVAL = 31     # Mid-execution checkpoint
    AWAITING_HUMAN_DECISION = 32          # AuthorityModelResolver deferred to user

    # --- Group 4: Delivery ---
    AWAITING_DELIVERY_ACTION = 40         # Build complete, delivery options shown
    AWAITING_QUICK_FIX_CONFIRM = 41       # Quick fix applied, confirm or retry
    AWAITING_CONTINUE_BUILD = 42          # Build hit limit, user can continue
