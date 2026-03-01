# telegram_bot/keyboards/project_select.py
"""
Paginated project selection keyboard.

Shows up to 6 projects per page with Previous/Next navigation buttons.
Each project button shows the display name and file count.
A footer row reminds the user they can type the project name instead.

Callback data format:
  "proj_{project_name}" — project selected
  "proj_page_{N}" — navigate to page N
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram_bot.project_registry import ProjectInfo

PROJECTS_PER_PAGE = 6


def build_project_select_keyboard(
    projects: list[ProjectInfo],
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Build a paginated project selection keyboard.

    Each project is a single button showing its display name and file
    count. Navigation buttons appear at the bottom if there are multiple
    pages. The keyboard also includes a hint that the user can type
    the project name instead of tapping.

    Args:
        projects: Full list of projects (pre-sorted by recency).
        page: Zero-indexed page number.

    Returns:
        InlineKeyboardMarkup with project buttons and optional nav.
    """
    total_pages = max(1, (len(projects) + PROJECTS_PER_PAGE - 1) // PROJECTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * PROJECTS_PER_PAGE
    end = start + PROJECTS_PER_PAGE
    page_projects = projects[start:end]

    rows = []

    # Project buttons (one per row for readability)
    for project in page_projects:
        label = f"\U0001f4c1 {project.display_name} ({project.file_count} files)"
        # Truncate label to keep callback_data under 64 bytes
        if len(label) > 50:
            label = label[:47] + "..."
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"proj_{project.name[:50]}",
                )
            ]
        )

    # Navigation row (only if multiple pages)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "\u2b05\ufe0f Previous", callback_data=f"proj_page_{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(
                f"Page {page + 1}/{total_pages}", callback_data="proj_page_noop"
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "Next \u27a1\ufe0f", callback_data=f"proj_page_{page + 1}"
                )
            )
        rows.append(nav)

    return InlineKeyboardMarkup(rows)
