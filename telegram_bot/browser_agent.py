"""
Browser Agent — ReAct loop for LLM-driven browser automation.

Accepts a natural language task, creates a browser session, and runs an
observe→reason→act loop where Claude decides what actions to take.
Executes actions via the browser container API and injects credentials
via the credential vault when needed.

Security invariants:
  - Claude never sees plaintext credentials.
  - Domain validation before every credential injection.
  - Content sanitized before entering Claude's context.
  - Step and retry budgets prevent runaway costs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

from telegram_bot.approval_manager import ApprovalManager
from telegram_bot.browser_client import BrowserClient, BrowserError
from telegram_bot.browser_tools import BROWSER_AGENT_SYSTEM_PROMPT, BROWSER_TOOLS
from telegram_bot.content_sanitizer import FlatElement, sanitize_snapshot
from telegram_bot.credential_vault import (
    clear_credentials,
    generate_totp,
    get_credentials,
    validate_domain,
)
from telegram_bot.security_gate import security_gate

logger = logging.getLogger(__name__)

# Configuration via environment
MAX_STEPS = int(os.environ.get("BROWSER_MAX_STEPS", "30"))
MAX_RETRIES_PER_ACTION = int(os.environ.get("BROWSER_MAX_RETRIES", "3"))
LOOP_DETECT_WINDOW = 5
LOOP_DETECT_THRESHOLD = 3
RECENT_STEPS_FULL = 8  # Keep last N steps in full detail
CLAUDE_MODEL = os.environ.get("BROWSER_AGENT_MODEL", "claude-sonnet-4-5-20250514")

# Token budget for browser agent tasks (per-task limit)
TOKEN_BUDGET_INPUT = int(os.environ.get("BROWSER_TOKEN_BUDGET_INPUT", "50000"))
TOKEN_BUDGET_OUTPUT = int(os.environ.get("BROWSER_TOKEN_BUDGET_OUTPUT", "5000"))

# Cost per million tokens (Sonnet pricing)
_COST_PER_M = {
    "claude-sonnet-4-5-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}
_DEFAULT_COST_PER_M = {"input": 3.0, "output": 15.0}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a Claude API call."""
    rates = _COST_PER_M.get(model, _DEFAULT_COST_PER_M)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


@dataclass
class TaskResult:
    """Result of a browser automation task."""
    success: bool
    summary: str = ""
    data: str | None = None
    reason: str | None = None
    suggestion: str | None = None
    screenshot_b64: str | None = None
    steps_taken: int = 0
    error: str | None = None


@dataclass
class StepRecord:
    """Record of a single action step in the ReAct loop."""
    step_num: int
    action: str
    params: dict = field(default_factory=dict)
    reasoning: str = ""
    result: str = ""
    element_name: str = ""

    def to_summary(self) -> str:
        """Compress to a one-line summary for older steps."""
        if self.action == "navigate":
            return f"Step {self.step_num}: Navigated to {self.params.get('url', '?')} -> {self.result}"
        elif self.action == "click":
            return f"Step {self.step_num}: Clicked '{self.element_name}' -> {self.result}"
        elif self.action == "type_text":
            return f"Step {self.step_num}: Typed into '{self.element_name}' -> {self.result}"
        elif self.action == "fill_credentials":
            return f"Step {self.step_num}: Filled credentials for {self.params.get('domain', '?')} -> {self.result}"
        elif self.action == "scroll":
            return f"Step {self.step_num}: Scrolled {self.params.get('direction', '?')} -> {self.result}"
        elif self.action == "select_option":
            return f"Step {self.step_num}: Selected '{self.params.get('value', '?')}' in '{self.element_name}' -> {self.result}"
        elif self.action == "wait":
            return f"Step {self.step_num}: Waited {self.params.get('seconds', '?')}s -> {self.result}"
        return f"Step {self.step_num}: {self.action} -> {self.result}"


class BrowserAgent:
    """Runs the ReAct loop for a single browser automation task."""

    def __init__(
        self,
        browser_client: BrowserClient,
        task_state=None,
        approval_manager: ApprovalManager | None = None,
        bot=None,
        on_progress: Callable[[int, str, str | None], Any] | None = None,
        allowlist=None,
        audit=None,
        cost_tracker=None,
        credential_vault=None,
        credential_request_manager=None,
    ):
        self._browser = browser_client
        self._task_state = task_state
        self._approval_manager = approval_manager
        self._bot = bot
        self._on_progress = on_progress
        self._allowlist = allowlist   # Phase 5: DomainAllowlist
        self._audit = audit           # Phase 5: AuditLogger
        self._cost_tracker = cost_tracker  # Pipeline cost tracker
        self._credential_vault = credential_vault  # UserCredentialVault
        self._credential_request_manager = credential_request_manager
        self._claude = anthropic.AsyncAnthropic()
        self._session_id: str | None = None
        self._steps: list[StepRecord] = []
        self._elements: list[FlatElement] = []
        self._cancelled = False
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost_usd = 0.0

    def cancel(self):
        """Cancel the running task."""
        self._cancelled = True

    async def run(
        self,
        task: str,
        cookies: list[dict] | None = None,
        research_context: str | None = None,
    ) -> TaskResult:
        """Execute a browser automation task.

        Args:
            task: Natural language task description from the user.
            cookies: Optional pre-loaded cookies for session restoration.
            research_context: Optional web research findings to inject into
                the agent's context before it starts browsing.

        Returns:
            TaskResult with success/failure and summary.
        """
        import time as _time
        _task_start = _time.monotonic()
        self._research_context = research_context

        if self._audit and self._task_state:
            self._audit.log_task_started(
                task_id=self._task_state.task_id,
                user_id=self._task_state.user_id,
                user_task=task,
                expected_domains=self._task_state.expected_domains,
            )

        try:
            self._session_id = await self._browser.create_session(cookies=cookies)
            if self._task_state:
                self._task_state.session_id = self._session_id
            result = await self._react_loop(task)
        except BrowserError as e:
            logger.error("Browser error during task: %s", e)
            result = TaskResult(
                success=False, error=str(e), steps_taken=len(self._steps)
            )
        except Exception as e:
            logger.error("Unexpected error in browser agent: %s", e, exc_info=True)
            result = TaskResult(
                success=False, error=str(e), steps_taken=len(self._steps)
            )
        finally:
            if self._session_id:
                try:
                    await self._browser.destroy_session(self._session_id)
                except Exception:
                    logger.warning("Failed to destroy browser session", exc_info=True)

        if self._audit and self._task_state:
            duration = _time.monotonic() - _task_start
            self._audit.log_task_completed(
                task_id=self._task_state.task_id,
                status="completed" if result.success else "failed",
                total_steps=result.steps_taken,
                total_tokens=self._total_input_tokens + self._total_output_tokens,
                duration_seconds=duration,
                summary=result.summary or result.reason or "",
            )

        logger.info(
            "Browser task finished: steps=%d, tokens=%d+%d, cost=$%.4f",
            result.steps_taken,
            self._total_input_tokens,
            self._total_output_tokens,
            self._total_cost_usd,
        )

        return result

    async def _react_loop(self, task: str) -> TaskResult:
        """Core observe-reason-act loop."""
        last_screenshot_b64: str | None = None

        for step_num in range(1, MAX_STEPS + 1):
            if self._cancelled:
                return TaskResult(
                    success=False,
                    reason="Task cancelled by user",
                    steps_taken=len(self._steps),
                    screenshot_b64=last_screenshot_b64,
                )

            # Notify progress
            if self._on_progress:
                await self._on_progress(
                    step_num, f"Step {step_num}/{MAX_STEPS}", None
                )

            # --- OBSERVE ---
            try:
                snap_data = await self._browser.snapshot(self._session_id)
            except BrowserError as e:
                logger.warning("Snapshot failed at step %d: %s", step_num, e)
                snap_data = {"url": "unknown", "title": "Error", "snapshot": {}}

            current_url = snap_data.get("url", "")
            page_title = snap_data.get("title", "")
            # Keep task_state in sync
            if self._task_state:
                self._task_state.current_url = current_url
                self._task_state.step_count = step_num
                self._task_state.touch()
            sanitized_content, self._elements = sanitize_snapshot(
                snap_data, current_url, page_title
            )

            # Capture screenshot for potential Telegram delivery
            try:
                ss = await self._browser.screenshot(self._session_id, quality=70)
                last_screenshot_b64 = ss.get("image")
            except BrowserError:
                pass  # Non-critical

            # --- CHECK FOR LOOPS ---
            if self._detect_loop():
                # Inject loop warning into next message
                sanitized_content += (
                    "\n\nWARNING: You appear to be stuck in a loop. "
                    "You have attempted the same action multiple times without progress. "
                    "Please try a different approach: scroll to find other options, "
                    "navigate to a different page, or report that the task cannot "
                    "be completed."
                )

            # --- REASON --- (call Claude)
            messages = self._build_messages(task, sanitized_content)

            try:
                response = await self._claude.messages.create(
                    model=CLAUDE_MODEL,
                    system=BROWSER_AGENT_SYSTEM_PROMPT,
                    messages=messages,
                    tools=BROWSER_TOOLS,
                    max_tokens=1024,
                )
            except anthropic.APIError as e:
                logger.error("Claude API error at step %d: %s", step_num, e)
                return TaskResult(
                    success=False,
                    error=f"Claude API error: {e}",
                    steps_taken=len(self._steps),
                    screenshot_b64=last_screenshot_b64,
                )

            # Track token usage and cost
            usage = response.usage
            step_input = usage.input_tokens
            step_output = usage.output_tokens
            self._total_input_tokens += step_input
            self._total_output_tokens += step_output
            step_cost = _estimate_cost(CLAUDE_MODEL, step_input, step_output)
            self._total_cost_usd += step_cost

            if self._task_state:
                self._task_state.total_tokens = (
                    self._total_input_tokens + self._total_output_tokens
                )

            # Record cost via cost_tracker if available
            if self._cost_tracker and self._task_state:
                try:
                    await self._cost_tracker.record(
                        project_name=f"browser:{self._task_state.task_id}",
                        phase_name="browser_agent",
                        model=CLAUDE_MODEL,
                        input_tokens=step_input,
                        output_tokens=step_output,
                        cost_usd=step_cost,
                    )
                except Exception:
                    logger.debug("Failed to record cost", exc_info=True)

            # Check token budget
            if (
                self._total_input_tokens > TOKEN_BUDGET_INPUT
                or self._total_output_tokens > TOKEN_BUDGET_OUTPUT
            ):
                logger.warning(
                    "Token budget exceeded: input=%d/%d, output=%d/%d",
                    self._total_input_tokens, TOKEN_BUDGET_INPUT,
                    self._total_output_tokens, TOKEN_BUDGET_OUTPUT,
                )
                return TaskResult(
                    success=False,
                    reason=(
                        f"Token budget exceeded (input: {self._total_input_tokens:,}/"
                        f"{TOKEN_BUDGET_INPUT:,}, output: {self._total_output_tokens:,}/"
                        f"{TOKEN_BUDGET_OUTPUT:,}). Total cost: ${self._total_cost_usd:.4f}"
                    ),
                    steps_taken=len(self._steps),
                    screenshot_b64=last_screenshot_b64,
                )

            # Extract tool use from response
            tool_use = self._extract_tool_use(response)
            if tool_use is None:
                # Claude responded with text but no tool — record and retry
                text_content = self._extract_text(response)
                self._steps.append(StepRecord(
                    step_num=step_num,
                    action="text_response",
                    result=text_content[:200] if text_content else "no content",
                ))
                continue

            tool_name = tool_use.name
            tool_input = tool_use.input

            # --- TERMINAL ACTIONS ---
            if tool_name == "task_complete":
                return TaskResult(
                    success=True,
                    summary=tool_input.get("summary", ""),
                    data=tool_input.get("data"),
                    steps_taken=len(self._steps),
                    screenshot_b64=last_screenshot_b64,
                )

            if tool_name == "task_failed":
                return TaskResult(
                    success=False,
                    reason=tool_input.get("reason", "Unknown"),
                    suggestion=tool_input.get("suggestion"),
                    steps_taken=len(self._steps),
                    screenshot_b64=last_screenshot_b64,
                )

            # --- SECURITY GATE (Phase 4 + Phase 5) ---
            if self._task_state and self._approval_manager and self._bot:
                gate_result = await security_gate(
                    action_name=tool_name,
                    action_params=tool_input,
                    task_state=self._task_state,
                    elements=self._elements,
                    screenshot_b64=last_screenshot_b64,
                    bot=self._bot,
                    approval_manager=self._approval_manager,
                    allowlist=self._allowlist,
                    audit=self._audit,
                    sanitized_content=sanitized_content,
                )

                if gate_result == "cancel":
                    return TaskResult(
                        success=False,
                        reason="Cancelled by user",
                        steps_taken=len(self._steps),
                        screenshot_b64=last_screenshot_b64,
                    )

                if gate_result == "skip":
                    self._steps.append(StepRecord(
                        step_num=step_num,
                        action=tool_name,
                        params=self._sanitize_params_for_log(tool_input),
                        reasoning=tool_input.get("reasoning", ""),
                        result="skipped (rejected or blocked)",
                    ))
                    continue

            # --- ACT (with retry on transient failures) ---
            step_record = StepRecord(
                step_num=step_num,
                action=tool_name,
                params=self._sanitize_params_for_log(tool_input),
                reasoning=tool_input.get("reasoning", ""),
            )

            last_error: ActionError | BrowserError | None = None
            for attempt in range(1, MAX_RETRIES_PER_ACTION + 1):
                try:
                    result_msg = await self._execute_action(
                        tool_name, tool_input, current_url
                    )
                    step_record.result = result_msg
                    last_error = None
                    break
                except BrowserError as e:
                    # Transient browser errors (connection reset, timeout) — retry
                    last_error = e
                    logger.warning(
                        "Action failed at step %d (attempt %d/%d): %s",
                        step_num, attempt, MAX_RETRIES_PER_ACTION, e,
                    )
                    if attempt < MAX_RETRIES_PER_ACTION:
                        await asyncio.sleep(1.0 * attempt)  # backoff
                except ActionError as e:
                    # Non-transient errors (unknown action, element not found) — no retry
                    last_error = e
                    logger.warning("Action failed at step %d: %s", step_num, e)
                    break

            if last_error is not None:
                step_record.result = f"error: {last_error}"

            self._steps.append(step_record)

            # Small delay to avoid hammering the browser
            await asyncio.sleep(0.5)

        # Exceeded MAX_STEPS
        return TaskResult(
            success=False,
            reason=f"Task exceeded {MAX_STEPS} step limit",
            steps_taken=len(self._steps),
            screenshot_b64=last_screenshot_b64,
        )

    # --- Action Execution ---

    async def _execute_action(
        self, name: str, params: dict, current_url: str
    ) -> str:
        """Execute a single browser action. Returns a result message string."""
        if name == "navigate":
            result = await self._browser.navigate(self._session_id, params["url"])
            return f"Navigated to {result.get('url', params['url'])} (title: {result.get('title', '?')})"

        elif name == "click":
            element = self._resolve_element(params["element"])
            await self._browser.click(
                self._session_id, role=element.role, name=element.name
            )
            return f"Clicked {element.role} '{element.name}'"

        elif name == "type_text":
            element = self._resolve_element(params["element"])
            await self._browser.type_text(
                self._session_id,
                params["text"],
                role=element.role,
                name=element.name,
                clear=True,
            )
            if params.get("press_enter"):
                await self._browser.press_key(self._session_id, "Enter")
            # Store element name for step record
            self._steps_element_name = element.name
            return f"Typed into {element.role} '{element.name}'"

        elif name == "select_option":
            element = self._resolve_element(params["element"])
            await self._browser.select_option(
                self._session_id,
                params["value"],
                role=element.role,
                name=element.name,
            )
            return f"Selected '{params['value']}' in '{element.name}'"

        elif name == "scroll":
            direction = params.get("direction", "down")
            await self._browser.scroll(self._session_id, direction=direction)
            return f"Scrolled {direction}"

        elif name == "wait":
            seconds = min(max(params.get("seconds", 2), 1), 10)
            await asyncio.sleep(seconds)
            return f"Waited {seconds} seconds"

        elif name == "fill_credentials":
            return await self._handle_credential_fill(params["domain"], current_url)

        else:
            raise ActionError(f"Unknown action: {name}")

    async def _handle_credential_fill(self, domain: str, current_url: str) -> str:
        """Securely inject credentials for a domain.

        Three-tier credential lookup:
          1. SOPS vault (server-managed, admin credentials)
          2. UserCredentialVault (per-user Redis-backed credentials)
          3. Interactive: pause agent, ask user via Mini App, wait
        """
        # --- 1. Try SOPS vault first (admin credentials) ---
        creds = get_credentials(domain)
        if creds:
            return await self._inject_sops_credentials(creds, domain, current_url)

        # --- 2. Try per-user credential vault ---
        if self._credential_vault and self._task_state:
            user_creds = await self._credential_vault.get_credential_by_domain(
                self._task_state.user_id, domain
            )
            if user_creds:
                return await self._inject_user_credentials(
                    user_creds, domain, current_url
                )

        # --- 3. Interactive credential request ---
        if (
            self._credential_request_manager
            and self._bot
            and self._task_state
        ):
            return await self._request_credential_interactively(
                domain, current_url
            )

        return f"No credentials configured for {domain}"

    async def _inject_sops_credentials(
        self, creds: dict, domain: str, current_url: str
    ) -> str:
        """Inject credentials from the SOPS vault (admin-managed)."""
        allowed_domains = creds.get("domains", [domain])

        # Domain check #1: before username
        if not validate_domain(current_url, allowed_domains):
            clear_credentials(creds)
            return (
                f"BLOCKED: Browser is on {current_url} but credentials "
                f"for {domain} are only allowed on {allowed_domains}"
            )

        username = creds.get("username", "")
        password = creds.get("password", "")

        try:
            # Type username — find the username/email field
            if username:
                await self._type_into_field(username, (
                    "Email", "Username", "Email address",
                    "Email address or username", "Login",
                ))

            # Domain check #2: before password (guards against mid-fill redirects)
            snap = await self._browser.snapshot(self._session_id)
            new_url = snap.get("url", current_url)
            if not validate_domain(new_url, allowed_domains):
                clear_credentials(creds)
                return (
                    f"BLOCKED: Browser redirected to {new_url} during credential "
                    f"fill. Credentials for {domain} are only allowed on {allowed_domains}"
                )

            # Type password
            if password:
                await self._type_into_field(password, (
                    "Password", "password", "Pass", "Current password",
                ))

            # Generate TOTP if needed
            totp_code = generate_totp(domain)
            if totp_code:
                await self._type_into_field(totp_code, (
                    "Verification code", "TOTP", "2FA",
                    "Code", "Authentication code",
                ))

            return f"Credentials applied to {domain}"

        finally:
            clear_credentials(creds)

    async def _inject_user_credentials(
        self, creds: dict, domain: str, current_url: str
    ) -> str:
        """Inject credentials from the per-user vault."""
        allowed_domains = [creds.get("domain", domain)]

        # Domain check #1: before username
        if not validate_domain(current_url, allowed_domains):
            return (
                f"BLOCKED: Browser is on {current_url} but credentials "
                f"for {domain} are only allowed on {allowed_domains}"
            )

        username = creds.get("username", "")
        password = creds.get("password", "")

        # Type username
        if username:
            await self._type_into_field(username, (
                "Email", "Username", "Email address",
                "Email address or username", "Login",
            ))

        # Domain check #2: before password
        snap = await self._browser.snapshot(self._session_id)
        new_url = snap.get("url", current_url)
        if not validate_domain(new_url, allowed_domains):
            return (
                f"BLOCKED: Browser redirected to {new_url} during credential "
                f"fill. Credentials for {domain} are only allowed on {allowed_domains}"
            )

        # Type password
        if password:
            await self._type_into_field(password, (
                "Password", "password", "Pass", "Current password",
            ))

        return f"Credentials applied to {domain}"

    async def _request_credential_interactively(
        self, domain: str, current_url: str
    ) -> str:
        """Pause the agent and ask the user to add credentials via Mini App."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
        from telegram_bot.user_credential_vault import _normalize_domain
        from urllib.parse import urlencode

        normalized = _normalize_domain(domain)
        user_id = self._task_state.user_id
        chat_id = self._task_state.chat_id
        task_id = self._task_state.task_id

        # Create pending request
        pending = self._credential_request_manager.create(
            user_id=user_id, domain=normalized, task_id=task_id,
        )

        # Build Mini App URL with query params to pre-fill domain
        webapp_base_url = os.environ.get("WEBAPP_URL", "").rstrip("/")
        if webapp_base_url:
            params = urlencode({
                "prefill_domain": domain,
                "open_cred_form": "1",
            })
            webapp_url = f"{webapp_base_url}?{params}"
            buttons = [[
                InlineKeyboardButton(
                    f"Add login for {normalized}",
                    web_app=WebAppInfo(url=webapp_url),
                )
            ]]
        else:
            buttons = [[
                InlineKeyboardButton(
                    "Add login via /credentials",
                    callback_data=f"cr_noop:{task_id}",
                )
            ]]

        keyboard = InlineKeyboardMarkup(buttons)

        msg = await self._bot.send_message(
            chat_id=chat_id,
            text=(
                f"I need to log into {normalized} but don't have credentials.\n\n"
                f"Tap the button below to add your login info. "
                f"I'll automatically continue once you save it."
            ),
            reply_markup=keyboard,
        )

        # Update task state
        prev_status = self._task_state.status
        self._task_state.status = "waiting_credential"

        # Wait for credential to be saved (with timeout)
        from telegram_bot.credential_request_manager import (
            CREDENTIAL_REQUEST_TIMEOUT_SECONDS,
        )
        timeout = CREDENTIAL_REQUEST_TIMEOUT_SECONDS

        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._credential_request_manager.remove(user_id, normalized)
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    text=(
                        f"Credential request for {normalized} timed out "
                        f"after {timeout // 60} minutes."
                    ),
                    reply_markup=None,
                )
            except Exception:
                pass
            self._task_state.status = prev_status
            return f"No credentials provided for {normalized} (timed out)"

        # Clean up
        self._credential_request_manager.remove(user_id, normalized)
        self._task_state.status = prev_status

        if pending.cancelled:
            return f"Credential request for {normalized} was cancelled"

        if pending.fulfilled:
            # Update the Telegram message
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    text=f"Credentials received for {normalized}. Logging in...",
                    reply_markup=None,
                )
            except Exception:
                pass

            # Fetch the newly saved credential and inject it
            user_creds = await self._credential_vault.get_credential_by_domain(
                user_id, normalized,
            )
            if user_creds:
                return await self._inject_user_credentials(
                    user_creds, normalized, current_url
                )
            return f"Credential was saved but could not be retrieved for {normalized}"

        return f"Credential request for {normalized} was not fulfilled"

    async def _type_into_field(
        self, value: str, field_names: tuple[str, ...]
    ) -> None:
        """Try typing a value into a form field using multiple name variants."""
        for i, name in enumerate(field_names):
            try:
                await self._browser.type_text(
                    self._session_id, value, name=name, clear=True,
                )
                return
            except BrowserError:
                if i == len(field_names) - 1:
                    return  # All field names exhausted
                continue

    def _resolve_element(self, ref_num: int) -> FlatElement:
        """Look up a flattened element by its reference number."""
        for elem in self._elements:
            if elem.ref == ref_num:
                return elem
        raise ActionError(
            f"Element [{ref_num}] not found in current page snapshot. "
            f"Available elements: {[e.ref for e in self._elements[:20]]}"
        )

    # --- Message Building ---

    def _build_messages(self, task: str, current_page_content: str) -> list[dict]:
        """Build the Claude API message array with sliding window history."""
        messages = []

        # Original task is always first, optionally with research context
        task_content = f"Task: {task}"
        if self._research_context:
            task_content += (
                f"\n\nResearch context (from web search):\n"
                f"{self._research_context}"
            )
        messages.append({
            "role": "user",
            "content": task_content,
        })

        if self._steps:
            # Older steps compressed to summaries
            older = self._steps[:-RECENT_STEPS_FULL]
            recent = self._steps[-RECENT_STEPS_FULL:]

            if older:
                summary_lines = [step.to_summary() for step in older]
                messages.append({
                    "role": "assistant",
                    "content": "I have been working on this task. Here's what I've done so far.",
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "Previous action history (summarized):\n"
                        + "\n".join(summary_lines)
                    ),
                })

            # Recent steps in full detail as tool_use / tool_result pairs
            for step in recent:
                # Assistant's action as a tool_use block
                messages.append({
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "id": f"step_{step.step_num}",
                        "name": step.action if step.action in (
                            "navigate", "click", "type_text", "select_option",
                            "scroll", "fill_credentials", "wait"
                        ) else "navigate",  # fallback for non-tool actions
                        "input": step.params,
                    }],
                })
                # Result as tool_result
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": f"step_{step.step_num}",
                        "content": step.result,
                    }],
                })

        # Current page state observation
        messages.append({
            "role": "user",
            "content": f"Current page state after your last action:\n\n{current_page_content}",
        })

        return messages

    # --- Loop Detection ---

    def _detect_loop(self) -> bool:
        """Detect if the agent is stuck in a loop."""
        if len(self._steps) < LOOP_DETECT_WINDOW:
            return False

        recent = self._steps[-LOOP_DETECT_WINDOW:]
        action_keys = [
            f"{s.action}:{s.params.get('element', s.params.get('url', s.params.get('direction', '')))}"
            for s in recent
        ]

        # Check if any action appears >= threshold times
        from collections import Counter
        counts = Counter(action_keys)
        return any(c >= LOOP_DETECT_THRESHOLD for c in counts.values())

    # --- Helpers ---

    @staticmethod
    def _extract_tool_use(response) -> Any | None:
        """Extract the first tool_use block from a Claude response."""
        for block in response.content:
            if block.type == "tool_use":
                return block
        return None

    @staticmethod
    def _extract_text(response) -> str:
        """Extract text content from a Claude response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return " ".join(parts)

    @staticmethod
    def _sanitize_params_for_log(params: dict) -> dict:
        """Remove sensitive data from params before logging."""
        safe = dict(params)
        safe.pop("reasoning", None)  # Keep logs concise
        # Never log credential-related values
        for key in ("password", "username", "totp", "secret"):
            safe.pop(key, None)
        return safe


class ActionError(Exception):
    """Raised when a browser action fails."""
    pass
