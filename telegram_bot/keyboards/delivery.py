"""
Delivery keyboards.

Section 5 enhancements:
  - VS Code deep-link button (url= type, opens in browser)
  - PR creation callback button
  - Preview start callback button
  - format_quality_gates() for review status line

Two keyboards:
1. delivery_keyboard — shown when a build completes successfully.
   Dynamic buttons based on project type (web project vs. script vs. blog).
2. quick_fix_keyboard — shown after a quick edit/fix is applied.
"""

from __future__ import annotations

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_vscode_url(
    domain: str, project_name: str, file_path: str | None = None,
) -> str:
    """Build a code-server URL that opens a specific project/file.

    Path mapping: code-server mounts workspace at /config/workspace.
    The relay-bot knows it as /app/workspace, claude-code as /workspace.
    All three mount the SAME Docker volume — use /config/workspace for
    code-server URLs.
    """
    base = f"https://{domain}/code/"
    folder = f"/config/workspace/{project_name}"
    url = f"{base}?folder={folder}"
    if file_path:
        full_path = f"/config/workspace/{project_name}/{file_path}"
        url += f"&file={full_path}"
    return url


def delivery_keyboard(
    github_pr_url: Optional[str] = None,
    preview_url: Optional[str] = None,
    vscode_url: Optional[str] = None,
    has_downloadable_files: bool = True,
    has_documentation: bool = True,
    is_deployable: bool = False,
    can_create_pr: bool = False,
    can_preview: bool = False,
) -> InlineKeyboardMarkup:
    """Dynamic delivery keyboard based on what was built.

    Only shows buttons relevant to the project. A blog post won't have
    a "Deploy" button; a CLI script won't have a "Preview" button.

    url= buttons open in browser directly. callback_data= buttons send
    a callback query to the bot for processing.
    """
    rows = []

    # Row 1: Primary link actions (open externally via url= buttons)
    primary = []
    if github_pr_url:
        primary.append(InlineKeyboardButton(
            "\U0001f4c4 View PR", url=github_pr_url
        ))
    if preview_url:
        primary.append(InlineKeyboardButton(
            "\U0001f441 Live Preview", url=preview_url
        ))
    if vscode_url:
        primary.append(InlineKeyboardButton(
            "\U0001f4bb VS Code", url=vscode_url
        ))
    if primary:
        rows.append(primary)

    # Row 2: Callback actions (PR creation, preview start)
    callback_actions = []
    if can_create_pr and not github_pr_url:
        callback_actions.append(InlineKeyboardButton(
            "\U0001f4c4 Create PR", callback_data="dlvr_pr"
        ))
    if can_preview and not preview_url:
        callback_actions.append(InlineKeyboardButton(
            "\U0001f680 Preview", callback_data="dlvr_preview"
        ))
    if callback_actions:
        rows.append(callback_actions)

    # Row 3: File actions
    file_actions = []
    if has_downloadable_files:
        file_actions.append(InlineKeyboardButton(
            "\U0001f4e5 Download ZIP", callback_data="dlvr_download"
        ))
    if has_documentation:
        file_actions.append(InlineKeyboardButton(
            "\U0001f4c4 Full Docs", callback_data="dlvr_docs"
        ))
    if file_actions:
        rows.append(file_actions)

    # Row 4: Deploy (separate row because it's a significant action)
    if is_deployable:
        rows.append([InlineKeyboardButton(
            "\U0001f680 Deploy to Production", callback_data="dlvr_deploy"
        )])

    return InlineKeyboardMarkup(rows)


def format_quality_gates(review_json: dict | None, cicd_passed: bool) -> str:
    """Format the quality gate line for the delivery message."""
    parts = []

    if review_json:
        score = review_json.get("alignment_score", 0)
        passed = review_json.get("passed", False)

        # Lint status
        lint_issues = [
            i for i in review_json.get("issues", [])
            if i.get("severity") == "warning"
        ]
        parts.append(f"{'\u2705' if not lint_issues else '\u26a0\ufe0f'} Lint")

        # Test status (inferred from review)
        if passed and score >= 7:
            parts.append("\u2705 Tests")
        elif score >= 5:
            parts.append("\u26a0\ufe0f Tests")
        else:
            parts.append("\u274c Tests")

        # Security
        security = review_json.get("security_findings", [])
        parts.append(f"{'\u2705' if not security else '\U0001f534'} Security")
    else:
        parts.append("\u26aa Review skipped")

    if cicd_passed:
        parts.append("\u2705 CI/CD")
    else:
        parts.append("\u26aa CI/CD (deferred)")

    return " | ".join(parts)


def quick_fix_keyboard() -> InlineKeyboardMarkup:
    """Post-fix confirmation keyboard.

    Callback data format: "qfix_approve", "qfix_retry", "qfix_revert"
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 Looks Good", callback_data="qfix_approve"),
            InlineKeyboardButton("\U0001f504 Try Again", callback_data="qfix_retry"),
        ],
        [
            InlineKeyboardButton("\u21a9\ufe0f Revert Changes", callback_data="qfix_revert"),
        ],
    ])
