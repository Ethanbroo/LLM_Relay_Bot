"""Context overflow detection and forced handoff for long sessions.

Prevents context rot by detecting when a Claude Code session has accumulated
too much context, and triggers a forced handoff — writing HANDOFF.md and
starting a new session that reads the handoff notes.

The key insight: Claude Code sessions accumulate conversation history. After
many turns, the model starts hallucinating or drifting from the original goal.
Episodic execution forces a break, writes down what was accomplished, and
starts fresh with only the essential context (triple files + anchor).
"""

import logging

from telegram_bot.project_context import ProjectContext
from telegram_bot.session_manager import SessionManager, SessionRecord

logger = logging.getLogger(__name__)

# Token thresholds for context overflow detection
SOFT_LIMIT_TOKENS = 100_000    # Warn the user, suggest handoff
HARD_LIMIT_TOKENS = 180_000    # Force handoff — no choice

# Turn-based thresholds (backup when token counts aren't available)
SOFT_LIMIT_TURNS = 30
HARD_LIMIT_TURNS = 50


class EpisodicManager:
    """Detects context overflow and manages forced handoffs."""

    def __init__(
        self,
        session_manager: SessionManager,
        project_context: ProjectContext,
    ):
        self.sessions = session_manager
        self.context = project_context

    def should_warn(self, total_tokens: int, turn_count: int) -> bool:
        """Check if we should warn the user about context buildup."""
        if total_tokens >= SOFT_LIMIT_TOKENS:
            return True
        if turn_count >= SOFT_LIMIT_TURNS:
            return True
        return False

    def should_force_handoff(self, total_tokens: int, turn_count: int) -> bool:
        """Check if we must force a handoff to prevent context rot."""
        if total_tokens >= HARD_LIMIT_TOKENS:
            return True
        if turn_count >= HARD_LIMIT_TURNS:
            return True
        return False

    async def execute_handoff(
        self,
        session: SessionRecord,
        what_was_done: str,
        reason: str = "context overflow",
    ) -> str:
        """Execute a forced handoff: write HANDOFF.md and return guidance message.

        Returns:
            Message to send to the user about the handoff.
        """
        # Write HANDOFF.md with current state
        self.context.write_handoff(
            project_name=session.project_name,
            session_id=session.session_id,
            phase_reached=session.phase_reached,
            what_was_done=what_was_done,
            blockers=f"Session ended due to {reason}",
        )

        # Update session record
        session.handoff_written = True
        await self.sessions.save(session)

        logger.info(
            f"Forced handoff executed for {session.project_name} "
            f"(session {session.session_id[:8]}): {reason}"
        )

        return (
            f"\u26a0\ufe0f *Session Handoff*\n\n"
            f"This session has reached its context limit ({reason}). "
            f"To maintain quality, I've saved your progress to HANDOFF.md.\n\n"
            f"Send your next message to continue — I'll start a new session "
            f"that reads the handoff notes and picks up where we left off.\n\n"
            f"Your project context (CLAUDE.md, HANDOFF.md, AGENTS.md) will "
            f"carry over automatically."
        )

    def build_warning_message(self, total_tokens: int, turn_count: int) -> str:
        """Build a context warning message for the user."""
        token_pct = (total_tokens / HARD_LIMIT_TOKENS) * 100
        return (
            f"\u26a0\ufe0f *Context Warning*\n\n"
            f"This session is using {token_pct:.0f}% of its context capacity "
            f"({total_tokens:,} tokens, {turn_count} turns).\n\n"
            f"Consider wrapping up the current task. I'll automatically "
            f"save progress and start a fresh session if we hit the limit."
        )

    def estimate_remaining_capacity(
        self, total_tokens: int, avg_tokens_per_phase: int = 15_000,
    ) -> int:
        """Estimate how many more pipeline phases can fit in the context."""
        remaining = HARD_LIMIT_TOKENS - total_tokens
        if avg_tokens_per_phase <= 0:
            return 0
        return max(0, remaining // avg_tokens_per_phase)
