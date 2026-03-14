"""
HTTP client for the browser-agent container's internal API.

This is the only module in the Python codebase that communicates directly
with the browser container. All other modules interact with the browser
through this client.

Usage:
    client = BrowserClient()  # reads config from env
    session_id = await client.create_session()
    result = await client.navigate(session_id, "https://example.com")
    screenshot = await client.screenshot(session_id)
    await client.destroy_session(session_id)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Defaults — overridable via environment
_BASE_URL = os.environ.get("BROWSER_AGENT_URL", "http://browser-agent:3000")
_API_SECRET = os.environ.get("BROWSER_API_SECRET", "")
_TIMEOUT = float(os.environ.get("BROWSER_CLIENT_TIMEOUT", "60"))


class BrowserError(Exception):
    """Raised when the browser API returns an error response."""

    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


class BrowserClient:
    """Async HTTP client for the browser-agent internal API."""

    def __init__(
        self,
        base_url: str = _BASE_URL,
        api_secret: str = _API_SECRET,
        timeout: float = _TIMEOUT,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_secret}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    async def close(self):
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Send a request and return the parsed response data."""
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise BrowserError(
                "CONNECTION_FAILED",
                f"Cannot reach browser-agent: {e}",
            ) from e
        except httpx.TimeoutException as e:
            raise BrowserError(
                "TIMEOUT",
                f"Browser-agent request timed out: {e}",
            ) from e

        body = resp.json()

        if not body.get("success"):
            err = body.get("error", {})
            raise BrowserError(
                code=err.get("code", "UNKNOWN"),
                message=err.get("message", resp.text),
                status=resp.status_code,
            )

        return body.get("data", {})

    # --- Session management ---

    async def create_session(
        self,
        viewport: dict | None = None,
        user_agent: str | None = None,
        cookies: list[dict] | None = None,
    ) -> str:
        """Create a new browser session. Returns the session ID."""
        payload: dict[str, Any] = {}
        if viewport:
            payload["viewport"] = viewport
        if user_agent:
            payload["userAgent"] = user_agent
        if cookies:
            payload["cookies"] = cookies

        data = await self._request("POST", "/session/create", json=payload)
        session_id = data["sessionId"]
        logger.info("Browser session created: %s", session_id[:8])
        return session_id

    async def destroy_session(self, session_id: str) -> None:
        """Destroy a browser session, clearing all state."""
        await self._request("DELETE", f"/session/{session_id}")
        logger.info("Browser session destroyed: %s", session_id[:8])

    async def get_cookies(self, session_id: str) -> list[dict]:
        """Export cookies from a session."""
        data = await self._request("GET", f"/session/{session_id}/cookies")
        return data["cookies"]

    # --- Navigation ---

    async def navigate(
        self,
        session_id: str,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout: int = 30000,
    ) -> dict[str, Any]:
        """Navigate to a URL. Returns url, title, statusCode, ok."""
        return await self._request(
            "POST",
            f"/session/{session_id}/navigate",
            json={"url": url, "waitUntil": wait_until, "timeout": timeout},
        )

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        """Capture the accessibility tree. Returns url, title, snapshot."""
        return await self._request(
            "POST",
            f"/session/{session_id}/snapshot",
        )

    # --- Interaction ---

    async def click(
        self,
        session_id: str,
        *,
        selector: str | None = None,
        name: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Click an element."""
        payload: dict[str, Any] = {"action": "click"}
        if selector:
            payload["selector"] = selector
        if name:
            payload["name"] = name
        if role:
            payload["role"] = role
        return await self._request(
            "POST", f"/session/{session_id}/interact", json=payload
        )

    async def type_text(
        self,
        session_id: str,
        text: str,
        *,
        selector: str | None = None,
        name: str | None = None,
        role: str | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        """Type text into an element."""
        payload: dict[str, Any] = {"action": "type", "text": text, "clear": clear}
        if selector:
            payload["selector"] = selector
        if name:
            payload["name"] = name
        if role:
            payload["role"] = role
        return await self._request(
            "POST", f"/session/{session_id}/interact", json=payload
        )

    async def select_option(
        self,
        session_id: str,
        value: str,
        *,
        selector: str | None = None,
        name: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Select a dropdown option."""
        payload: dict[str, Any] = {"action": "select", "value": value}
        if selector:
            payload["selector"] = selector
        if name:
            payload["name"] = name
        if role:
            payload["role"] = role
        return await self._request(
            "POST", f"/session/{session_id}/interact", json=payload
        )

    async def scroll(
        self,
        session_id: str,
        direction: str = "down",
        amount: int = 500,
    ) -> dict[str, Any]:
        """Scroll the page."""
        return await self._request(
            "POST",
            f"/session/{session_id}/interact",
            json={"action": "scroll", "direction": direction, "amount": amount},
        )

    async def press_key(
        self,
        session_id: str,
        key: str,
    ) -> dict[str, Any]:
        """Press a keyboard key."""
        return await self._request(
            "POST",
            f"/session/{session_id}/interact",
            json={"action": "press", "key": key},
        )

    # --- JavaScript evaluation ---

    async def evaluate(
        self,
        session_id: str,
        expression: str,
    ) -> Any:
        """Execute JavaScript on the page and return the result."""
        data = await self._request(
            "POST",
            f"/session/{session_id}/interact",
            json={"action": "evaluate", "expression": expression},
        )
        return data.get("result")

    # --- Screenshot ---

    async def screenshot(
        self,
        session_id: str,
        full_page: bool = False,
        quality: int | None = None,
    ) -> dict[str, Any]:
        """Capture a screenshot. Returns image (base64), mimeType, sizeBytes."""
        payload: dict[str, Any] = {"fullPage": full_page}
        if quality is not None:
            payload["quality"] = quality
        return await self._request(
            "POST", f"/session/{session_id}/screenshot", json=payload
        )

    # --- Health ---

    async def health(self) -> dict[str, Any]:
        """Check browser-agent health. Returns status, activeSessions, etc."""
        return await self._request("GET", "/health")
