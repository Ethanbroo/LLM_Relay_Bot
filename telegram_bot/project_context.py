"""Triple File management: CLAUDE.md, HANDOFF.md, AGENTS.md.

These files live on disk in the shared-workspace volume under each project
directory. They are the cross-session memory that Claude Code reads automatically.

- CLAUDE.md: Project-level coding standards (rarely changes)
- HANDOFF.md: Session-level handoff notes (written every session)
- AGENTS.md: Accumulated agent corrections (grows over time)
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ProjectContext:
    """Manages per-project context files in the workspace volume."""

    def __init__(self, workspace_path: str):
        self.workspace = workspace_path

    def _project_dir(self, project_name: str) -> str:
        return os.path.join(self.workspace, project_name)

    # ── Read ─────────────────────────────────────────────────

    def read_claude_md(self, project_name: str) -> str | None:
        path = os.path.join(self._project_dir(project_name), "CLAUDE.md")
        return self._read_file(path)

    def read_handoff_md(self, project_name: str) -> str | None:
        path = os.path.join(self._project_dir(project_name), "HANDOFF.md")
        return self._read_file(path)

    def read_agents_md(self, project_name: str) -> str | None:
        path = os.path.join(self._project_dir(project_name), "AGENTS.md")
        return self._read_file(path)

    def read_all(self, project_name: str) -> str:
        """Read all three files and concatenate for system prompt injection.
        Returns empty string if no files exist."""
        parts = []

        claude_md = self.read_claude_md(project_name)
        if claude_md:
            parts.append(f"=== CLAUDE.md (Project Standards) ===\n{claude_md}")

        handoff_md = self.read_handoff_md(project_name)
        if handoff_md:
            parts.append(f"=== HANDOFF.md (Previous Session) ===\n{handoff_md}")

        agents_md = self.read_agents_md(project_name)
        if agents_md:
            parts.append(f"=== AGENTS.md (Learned Preferences) ===\n{agents_md}")

        return "\n\n".join(parts)

    # ── Write ────────────────────────────────────────────────

    def write_handoff(
        self,
        project_name: str,
        session_id: str,
        phase_reached: int,
        what_was_done: str,
        whats_next: str = "",
        blockers: str = "",
        open_questions: str = "",
        key_decisions: str = "",
    ) -> None:
        """Write HANDOFF.md at the end of a session."""
        now = datetime.now(timezone.utc).isoformat()

        content = f"""# HANDOFF.md — Session Handoff

## Session: {session_id}
## Date: {now}
## Phase Reached: {phase_reached}

## What Was Done
{what_was_done}

## What's Next
{whats_next if whats_next else "- No next steps defined"}

## Blockers
{blockers if blockers else "- None"}

## Open Questions
{open_questions if open_questions else "- None"}

## Key Decisions Made
{key_decisions if key_decisions else "- See session logs"}
"""
        path = os.path.join(self._project_dir(project_name), "HANDOFF.md")
        self._write_file(path, content)

    def write_claude_md(self, project_name: str, content: str) -> None:
        """Write or overwrite CLAUDE.md."""
        path = os.path.join(self._project_dir(project_name), "CLAUDE.md")
        self._write_file(path, content)

    def append_to_agents_md(self, project_name: str, agent_name: str, note: str) -> None:
        """Append a correction/preference to AGENTS.md under the agent's section."""
        path = os.path.join(self._project_dir(project_name), "AGENTS.md")
        existing = self._read_file(path) or "# AGENTS.md — Agent-Specific Notes\n"

        section_header = f"## {agent_name}"
        if section_header in existing:
            # Append under existing section
            existing = existing.replace(
                section_header,
                f"{section_header}\n- {note}",
            )
        else:
            # Add new section
            existing += f"\n\n{section_header}\n- {note}\n"

        self._write_file(path, existing)

    def has_project_files(self, project_name: str) -> dict[str, bool]:
        """Check which triple files exist for a project."""
        project_dir = self._project_dir(project_name)
        return {
            "claude_md": os.path.isfile(os.path.join(project_dir, "CLAUDE.md")),
            "handoff_md": os.path.isfile(os.path.join(project_dir, "HANDOFF.md")),
            "agents_md": os.path.isfile(os.path.join(project_dir, "AGENTS.md")),
        }

    # ── Helpers ──────────────────────────────────────────────

    def _read_file(self, path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return None

    def _write_file(self, path: str, content: str) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Failed to write {path}: {e}")
