"""Message routing with project matching and session awareness.

Routes incoming messages to the correct handler path based on:
1. Explicit command prefixes (/edit, /fix, /build)
2. Classifier intent (NEW_BUILD, EDIT_FIX, QUESTION, RESEARCH, etc.)
3. Project context (active project, project name in message)
4. Session context (most recent session for matched project)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from telegram_bot.classifier import MessageClassifier, Classification, Intent
from telegram_bot.session_manager import SessionManager, SessionRecord
from telegram_bot.project_registry import FilesystemProjectRegistry, ProjectInfo

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of routing a user message."""
    path: str               # "PATH_A" | "PATH_B" | "PATH_C" | "NEW_BUILD" |
                            # "RESEARCH" | "CONVERSATIONAL" | "EXTERNAL" | "AMBIGUOUS"
    project_name: Optional[str] = None
    session: Optional[SessionRecord] = None
    classification: Optional[Classification] = None
    confidence: str = "high"   # high | medium | low


class MessageRouter:
    """Routes incoming messages to the correct handler path."""

    def __init__(
        self,
        classifier: MessageClassifier,
        session_manager: SessionManager,
        project_registry: FilesystemProjectRegistry,
    ):
        self.classifier = classifier
        self.sessions = session_manager
        self.projects = project_registry

    async def route(self, message: str, user_data: dict) -> RoutingDecision:
        """Determine how to handle a user message.

        Priority chain:
        1. /edit or /fix prefix -> Path A
        2. /build + existing project -> Path B
        3. /build + no match -> New project
        4. System commands -> admin handler (handled upstream, not here)
        5-9. Freeform text -> classify then route with project context
        """

        text = message.strip()

        # ── Priority 1: Explicit /edit or /fix ───────────────
        if text.lower().startswith(("/edit", "/fix")):
            project = self._find_active_project(user_data)
            session = await self._find_session(project) if project else None
            return RoutingDecision(
                path="PATH_A",
                project_name=project,
                session=session,
            )

        # ── Priority 2-3: Explicit /build ────────────────────
        if text.lower().startswith("/build"):
            prompt = text.split(maxsplit=1)[1] if " " in text else ""
            project = self._match_project_from_prompt(prompt)
            if project:
                session = await self._find_session(project)
                return RoutingDecision(
                    path="PATH_B",
                    project_name=project,
                    session=session,
                )
            else:
                return RoutingDecision(path="NEW_BUILD")

        # ── Priority 5-9: Freeform text -> classify ──────────
        classification = await self.classifier.classify(text)

        if classification.intent == Intent.NEW_BUILD:
            # Check if message references existing project
            project = self._match_project_from_prompt(text)
            if project:
                session = await self._find_session(project)
                return RoutingDecision(
                    path="PATH_B",
                    project_name=project,
                    session=session,
                    classification=classification,
                )
            return RoutingDecision(
                path="NEW_BUILD",
                classification=classification,
            )

        if classification.intent == Intent.EDIT_FIX:
            project = self._find_active_project(user_data)
            session = await self._find_session(project) if project else None
            if project and session:
                return RoutingDecision(
                    path="PATH_A",
                    project_name=project,
                    session=session,
                    classification=classification,
                )
            # No project context — ambiguous
            return RoutingDecision(
                path="AMBIGUOUS",
                classification=classification,
                confidence="low",
            )

        if classification.intent == Intent.QUESTION:
            project = self._find_active_project(user_data)
            session = await self._find_session(project) if project else None
            return RoutingDecision(
                path="PATH_C",
                project_name=project,
                session=session,
                classification=classification,
            )

        if classification.intent == Intent.RESEARCH:
            return RoutingDecision(
                path="RESEARCH",
                classification=classification,
            )

        if classification.intent == Intent.EXTERNAL_ACTION:
            return RoutingDecision(
                path="EXTERNAL",
                classification=classification,
            )

        # CONVERSATIONAL or unknown
        return RoutingDecision(
            path="CONVERSATIONAL",
            classification=classification,
        )

    # ── Project Matching ─────────────────────────────────────

    def _match_project_from_prompt(self, prompt: str) -> Optional[str]:
        """Try to find an existing project name mentioned in the prompt."""
        if not prompt:
            return None

        # Use the registry's find_project which does substring matching
        match = self.projects.find_project(prompt)
        if match:
            return match.name

        return None

    def _find_active_project(self, user_data: dict) -> Optional[str]:
        """Get the user's most recently active project from user_data."""
        return user_data.get("selected_project") or user_data.get("last_project")

    async def _find_session(self, project_name: Optional[str]) -> Optional[SessionRecord]:
        """Get the most recent session for a project."""
        if not project_name:
            return None
        return await self.sessions.get_latest_for_project(project_name)
