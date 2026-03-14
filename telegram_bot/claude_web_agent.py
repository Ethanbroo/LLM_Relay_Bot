"""
ClaudeWebAgent — Deterministic browser automation for claude.ai.

Unlike BrowserAgent (which uses an LLM-driven ReAct loop), this module
follows a fixed script to interact with claude.ai:
  1. Navigate and log in
  2. Select model and features
  3. Submit a prompt (using fill(), not keystrokes)
  4. Wait for completion (poll via evaluate() + snapshot)
  5. Extract the full response via DOM query

This lets the bot use the user's Claude Pro/Max subscription for deep
research and analysis — free of API cost — while keeping code generation
and debugging on Claude Code CLI.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Timeouts and polling
MAX_WAIT_SECONDS = 600  # 10 minutes max for a response
POLL_INTERVAL_SECONDS = 5  # Check every 5 seconds
STABILITY_CHECKS = 2  # Response text must be stable for this many checks


class ClaudeWebError(Exception):
    """Raised when claude.ai interaction fails."""
    pass


class ClaudeWebAgent:
    """Deterministic browser automation for submitting prompts to claude.ai."""

    CLAUDE_URL = "https://claude.ai"

    def __init__(
        self,
        browser_client,
        credential_vault=None,
        credential_request_manager=None,
        user_id: int = 0,
        bot=None,
        chat_id: int = 0,
    ):
        self._browser = browser_client
        self._credential_vault = credential_vault
        self._credential_request_manager = credential_request_manager
        self._user_id = user_id
        self._bot = bot
        self._chat_id = chat_id
        self._session_id: str | None = None
        self._logged_in: bool = False

    async def submit_prompt(
        self,
        prompt: str,
        *,
        model: str = "Claude Opus 4.6",
        enable_deep_research: bool = True,
        enable_web_search: bool = True,
        new_conversation: bool = True,
    ) -> str:
        """Submit a prompt to claude.ai and return the full response text.

        Args:
            prompt: The prompt text to send.
            model: Model name to select in the UI (e.g., "Claude Opus 4.6").
            enable_deep_research: Whether to enable deep research mode.
            enable_web_search: Whether to enable web search.
            new_conversation: True to start a new chat, False to continue.

        Returns:
            The full response text from Claude.

        Raises:
            ClaudeWebError: On login failure, timeout, or extraction failure.
        """
        try:
            # Ensure we have a browser session
            if not self._session_id:
                self._session_id = await self._browser.create_session(
                    viewport={"width": 1280, "height": 900},
                )

            # Navigate and login if needed
            if not self._logged_in:
                await self._login()

            # Start new conversation if requested
            if new_conversation:
                await self._start_new_conversation()

            # Select model and features
            await self._select_model(model)
            await self._toggle_features(enable_deep_research, enable_web_search)

            # Submit the prompt
            await self._submit_text(prompt)

            # Wait for response to complete
            response_text = await self._wait_and_extract()

            # Send progress to Telegram
            if self._bot and self._chat_id:
                preview = response_text[:500] + "..." if len(response_text) > 500 else response_text
                try:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=f"Claude (web) response received ({len(response_text)} chars):\n\n{preview}",
                    )
                except Exception:
                    logger.debug("Failed to send progress message", exc_info=True)

            return response_text

        except ClaudeWebError:
            raise
        except Exception as e:
            raise ClaudeWebError(f"Unexpected error interacting with claude.ai: {e}") from e

    async def cleanup(self):
        """Destroy the browser session."""
        if self._session_id:
            try:
                await self._browser.destroy_session(self._session_id)
            except Exception:
                logger.debug("Failed to destroy claude.ai session", exc_info=True)
            self._session_id = None
            self._logged_in = False

    # --- Internal methods ---

    async def _login(self):
        """Navigate to claude.ai and log in if not already authenticated."""
        await self._browser.navigate(
            self._session_id,
            self.CLAUDE_URL,
            wait_until="networkidle",
            timeout=30000,
        )
        await asyncio.sleep(2)  # Let the page settle

        # Check if we're already logged in by looking at the page state
        snapshot = await self._browser.snapshot(self._session_id)
        page_text = str(snapshot)

        # If we see a chat input or "New chat", we're logged in
        if self._is_logged_in(page_text):
            self._logged_in = True
            logger.info("Already logged in to claude.ai")
            return

        # Need to log in — get credentials
        creds = None
        if self._credential_vault:
            creds = await self._credential_vault.get_credential_by_domain(
                self._user_id, "claude.ai"
            )

        if not creds:
            # Try to get credentials interactively
            if self._credential_request_manager and self._bot and self._chat_id:
                from telegram_bot.credential_request_manager import CREDENTIAL_REQUEST_TIMEOUT_SECONDS
                pending = self._credential_request_manager.create(
                    user_id=self._user_id,
                    domain="claude.ai",
                    task_id=f"claude_web_{int(time.time())}",
                )

                try:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=(
                            "I need to log into claude.ai to use deep research.\n"
                            "Please add your claude.ai login credentials in the settings."
                        ),
                    )
                except Exception:
                    pass

                try:
                    await asyncio.wait_for(
                        pending.event.wait(),
                        timeout=CREDENTIAL_REQUEST_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    self._credential_request_manager.remove(self._user_id, "claude.ai")
                    raise ClaudeWebError("Timed out waiting for claude.ai credentials")

                if pending.cancelled:
                    raise ClaudeWebError("Credential request was cancelled")

                # Re-fetch credentials after user saves them
                creds = await self._credential_vault.get_credential_by_domain(
                    self._user_id, "claude.ai"
                )

            if not creds:
                raise ClaudeWebError(
                    "No claude.ai credentials available. Add them via /credentials or the settings panel."
                )

        # Fill login form
        try:
            # Click "Continue with email" or similar if present
            try:
                await self._browser.click(
                    self._session_id,
                    name="Continue with email",
                )
                await asyncio.sleep(1)
            except Exception:
                pass  # May not exist if email field is directly visible

            # Type email
            await self._browser.type_text(
                self._session_id,
                creds["username"],
                name="Email address",
                clear=True,
            )
            await self._browser.press_key(self._session_id, "Enter")
            await asyncio.sleep(2)

            # Type password
            await self._browser.type_text(
                self._session_id,
                creds["password"],
                name="Password",
                clear=True,
            )
            await self._browser.press_key(self._session_id, "Enter")
            await asyncio.sleep(3)

            # Verify login succeeded
            snapshot = await self._browser.snapshot(self._session_id)
            if not self._is_logged_in(str(snapshot)):
                raise ClaudeWebError("Login to claude.ai failed — check credentials")

            self._logged_in = True
            logger.info("Successfully logged in to claude.ai")

        except ClaudeWebError:
            raise
        except Exception as e:
            raise ClaudeWebError(f"Failed to log in to claude.ai: {e}") from e

    def _is_logged_in(self, page_text: str) -> bool:
        """Check if the page shows a logged-in state."""
        login_indicators = ["New chat", "Start a new chat", "Message Claude", "Send a message"]
        return any(indicator.lower() in page_text.lower() for indicator in login_indicators)

    async def _start_new_conversation(self):
        """Click 'New chat' to start a fresh conversation."""
        try:
            await self._browser.click(self._session_id, name="New chat")
            await asyncio.sleep(1)
        except Exception:
            # May already be on a new chat page
            try:
                await self._browser.navigate(
                    self._session_id,
                    f"{self.CLAUDE_URL}/new",
                    wait_until="networkidle",
                    timeout=15000,
                )
                await asyncio.sleep(1)
            except Exception:
                logger.debug("Could not start new conversation, continuing anyway")

    async def _select_model(self, model_name: str):
        """Select the specified model in the model picker."""
        try:
            # Click the model selector button/dropdown
            await self._browser.click(self._session_id, selector="[data-testid='model-selector']")
            await asyncio.sleep(0.5)
        except Exception:
            try:
                # Fallback: try clicking by visible model name text
                await self._browser.click(self._session_id, name="Claude")
                await asyncio.sleep(0.5)
            except Exception:
                logger.debug("Could not find model selector, using default model")
                return

        # Click the target model
        try:
            await self._browser.click(self._session_id, name=model_name)
            await asyncio.sleep(0.5)
            logger.info("Selected model: %s", model_name)
        except Exception:
            logger.warning("Could not select model '%s', using current model", model_name)

    async def _toggle_features(self, deep_research: bool, web_search: bool):
        """Enable/disable deep research and web search toggles."""
        # These toggles may not always be visible — fail silently
        if deep_research:
            try:
                await self._browser.click(self._session_id, name="Extended thinking")
                await asyncio.sleep(0.3)
            except Exception:
                pass

        if web_search:
            try:
                await self._browser.click(self._session_id, name="Search")
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _submit_text(self, prompt: str):
        """Fill the prompt into the textbox and submit."""
        # Use fill() for atomic paste (not keystroke simulation)
        # Try multiple possible textbox identifiers
        textbox_names = [
            "Message Claude",
            "Send a message",
            "Reply to Claude...",
            "Type your message",
        ]

        submitted = False
        for name in textbox_names:
            try:
                await self._browser.type_text(
                    self._session_id,
                    prompt,
                    name=name,
                    clear=True,
                )
                submitted = True
                break
            except Exception:
                continue

        if not submitted:
            # Last resort: try by role
            try:
                await self._browser.type_text(
                    self._session_id,
                    prompt,
                    role="textbox",
                    clear=True,
                )
                submitted = True
            except Exception:
                pass

        if not submitted:
            # CSS selector fallback
            try:
                await self._browser.type_text(
                    self._session_id,
                    prompt,
                    selector="[contenteditable='true'], textarea",
                    clear=True,
                )
                submitted = True
            except Exception:
                raise ClaudeWebError("Could not find the message input on claude.ai")

        # Submit by pressing Enter or clicking Send
        await asyncio.sleep(0.5)
        try:
            await self._browser.press_key(self._session_id, "Enter")
        except Exception:
            try:
                await self._browser.click(self._session_id, name="Send")
            except Exception:
                raise ClaudeWebError("Could not submit the prompt")

        logger.info("Prompt submitted (%d chars)", len(prompt))

    async def _wait_and_extract(self) -> str:
        """Wait for Claude's response to complete and extract the full text."""
        start = time.monotonic()
        last_text_length = 0
        stability_count = 0

        while (time.monotonic() - start) < MAX_WAIT_SECONDS:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

            # Try to extract current response text
            current_text = await self._extract_response_text()

            if not current_text:
                # No response yet — keep waiting
                stability_count = 0
                continue

            current_length = len(current_text)

            # Check if response is still being generated
            is_streaming = await self._is_still_streaming()

            if is_streaming:
                last_text_length = current_length
                stability_count = 0
                continue

            # Not streaming — check stability
            if current_length == last_text_length and current_length > 0:
                stability_count += 1
                if stability_count >= STABILITY_CHECKS:
                    logger.info(
                        "Response complete (%d chars, %.1fs)",
                        current_length,
                        time.monotonic() - start,
                    )
                    return current_text
            else:
                stability_count = 0
                last_text_length = current_length

        # Timeout — return whatever we have
        final_text = await self._extract_response_text()
        if final_text:
            logger.warning("Response extraction timed out, returning partial (%d chars)", len(final_text))
            return final_text

        raise ClaudeWebError("Timed out waiting for claude.ai response")

    async def _is_still_streaming(self) -> bool:
        """Check if Claude is still generating by looking for streaming indicators."""
        try:
            # Check for a "stop" button (visible during generation)
            result = await self._browser.evaluate(
                self._session_id,
                """
                (() => {
                    // Check for stop/cancel button (visible during streaming)
                    const stopBtn = document.querySelector('[aria-label="Stop"]') ||
                                    document.querySelector('button[aria-label*="stop" i]');
                    if (stopBtn) return true;

                    // Check for the streaming cursor/animation
                    const cursor = document.querySelector('.animate-pulse, .cursor-blink');
                    if (cursor) return true;

                    return false;
                })()
                """,
            )
            return bool(result)
        except Exception:
            return False

    async def _extract_response_text(self) -> str:
        """Extract Claude's latest response text from the page via DOM queries."""
        # Strategy 1: Direct DOM query for the last assistant message
        js_expressions = [
            # Most likely: data attribute on the message container
            """
            (() => {
                const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                if (msgs.length === 0) return '';
                return msgs[msgs.length - 1].innerText || '';
            })()
            """,
            # Fallback: look for message containers by class patterns
            """
            (() => {
                const msgs = document.querySelectorAll('.font-claude-message, .prose');
                if (msgs.length === 0) return '';
                return msgs[msgs.length - 1].innerText || '';
            })()
            """,
            # Fallback: look for any large text block that appeared after the user message
            """
            (() => {
                const containers = document.querySelectorAll('[class*="message"], [class*="response"]');
                let longest = '';
                for (const el of containers) {
                    const text = el.innerText || '';
                    if (text.length > longest.length) longest = text;
                }
                return longest;
            })()
            """,
        ]

        for expr in js_expressions:
            try:
                result = await self._browser.evaluate(self._session_id, expr)
                if result and len(str(result)) > 10:
                    return str(result).strip()
            except Exception:
                continue

        # Strategy 2: Fall back to accessibility tree scan
        try:
            snapshot = await self._browser.snapshot(self._session_id)
            text = self._extract_from_snapshot(snapshot)
            if text:
                return text
        except Exception:
            pass

        return ""

    def _extract_from_snapshot(self, snapshot: dict) -> str:
        """Extract response text from the accessibility tree snapshot."""
        # Walk the tree looking for large text blocks
        snapshot_data = snapshot.get("snapshot") or snapshot
        if not snapshot_data:
            return ""

        texts = []
        self._walk_tree(snapshot_data, texts)

        # Return the longest text block (likely the response)
        if texts:
            return max(texts, key=len)
        return ""

    def _walk_tree(self, node: dict | list, texts: list[str], min_length: int = 50):
        """Recursively walk accessibility tree collecting text content."""
        if isinstance(node, list):
            for item in node:
                self._walk_tree(item, texts, min_length)
            return

        if not isinstance(node, dict):
            return

        name = node.get("name", "")
        if isinstance(name, str) and len(name) >= min_length:
            texts.append(name)

        for child in node.get("children", []):
            self._walk_tree(child, texts, min_length)
