"""
Domain allowlist manager — Phase 5.

Loads domain rules from config/domain-allowlist.yaml and provides a fast
check() method used by the security gate before every navigation action.

Three categories:
  - always_allowed: auto-approve navigation (combined with expected_domains)
  - allowed: navigation permitted but Phase 4 approval rules still apply
  - blocked: navigation denied regardless of user approval

Supports wildcard subdomains (*.example.com) and hot-reloading.
Dynamic additions via Telegram commands are stored in-memory (optionally
persisted to Redis across bot restarts).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class DomainAllowlist:
    """Manages the domain allowlist with wildcard matching."""

    def __init__(self, config_path: str = "config/domain-allowlist.yaml"):
        self.config_path = Path(config_path)
        self.always_allowed: set[str] = set()
        self.allowed: set[str] = set()
        self.blocked: set[str] = set()
        self.dynamic_additions: set[str] = set()
        self.settings: dict = {}
        self._last_loaded: Optional[datetime] = None
        self.load()

    def load(self):
        """Load or reload the allowlist from disk."""
        try:
            with open(self.config_path) as f:
                config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning("Allowlist config not found at %s, using empty allowlist", self.config_path)
            config = {}

        self.always_allowed = set(config.get("always_allowed", []))
        self.allowed = set(config.get("allowed", []))
        self.blocked = set(config.get("blocked", []))
        self.settings = config.get("settings", {})
        self._last_loaded = datetime.now(timezone.utc)
        logger.info(
            "Domain allowlist loaded: %d always_allowed, %d allowed, %d blocked",
            len(self.always_allowed), len(self.allowed), len(self.blocked),
        )

    def maybe_reload(self, max_age_seconds: int = 60):
        """Reload the config file if it has been modified since last load."""
        if self._last_loaded is None:
            self.load()
            return
        elapsed = (datetime.now(timezone.utc) - self._last_loaded).total_seconds()
        if elapsed > max_age_seconds:
            try:
                stat = self.config_path.stat()
                file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if file_mtime > self._last_loaded:
                    self.load()
            except FileNotFoundError:
                pass

    def check(self, domain: str) -> str:
        """Check a domain against the allowlist.

        Returns one of:
          'always_allowed' - domain is in the always-allowed list
          'allowed'        - domain is in the allowed list
          'blocked'        - domain is explicitly blocked
          'unlisted'       - domain is not in any list
        """
        self.maybe_reload()

        # Blocked list takes priority over everything
        if self._matches_any(domain, self.blocked):
            return "blocked"

        # Check always_allowed
        if self._matches_any(domain, self.always_allowed):
            return "always_allowed"

        # Check dynamic additions
        if self._matches_any(domain, self.dynamic_additions):
            return "allowed"

        # Check allowed
        if self._matches_any(domain, self.allowed):
            return "allowed"

        return "unlisted"

    def _matches_any(self, domain: str, patterns: set[str]) -> bool:
        """Check if a domain matches any pattern in the set.

        Supports exact matches and wildcard subdomains (*.example.com).
        """
        domain = domain.lower().strip(".")

        for pattern in patterns:
            pattern = pattern.lower().strip(".")

            # Exact match
            if domain == pattern:
                return True

            # Wildcard match: *.example.com matches sub.example.com
            # and also deeper subdomains like deep.sub.example.com
            if pattern.startswith("*."):
                base = pattern[2:]
                if domain == base or domain.endswith("." + base):
                    return True

        return False

    def add_dynamic(self, domain: str) -> bool:
        """Add a domain dynamically (via Telegram command).

        Returns False if the maximum dynamic domain limit is reached.
        """
        max_dynamic = self.settings.get("max_dynamic_domains", 50)
        if len(self.dynamic_additions) >= max_dynamic:
            return False
        self.dynamic_additions.add(domain.lower())
        return True

    def remove_dynamic(self, domain: str) -> bool:
        """Remove a dynamically added domain.

        Returns True if the domain was found and removed.
        """
        domain_lower = domain.lower()
        if domain_lower in self.dynamic_additions:
            self.dynamic_additions.discard(domain_lower)
            return True
        return False

    def get_dynamic_list(self) -> list[str]:
        """Return all dynamically added domains."""
        return sorted(self.dynamic_additions)

    @property
    def default_action(self) -> str:
        """What to do with unlisted domains: 'block' or 'prompt'."""
        return self.settings.get("default_action", "block")

    async def save_dynamic_to_redis(self, redis_client) -> None:
        """Persist dynamic additions to Redis."""
        if not redis_client or not self.settings.get("persist_dynamic_additions", True):
            return
        try:
            import json
            await redis_client.set(
                "domain_allowlist:dynamic",
                json.dumps(sorted(self.dynamic_additions)),
            )
        except Exception:
            logger.warning("Failed to save dynamic allowlist to Redis", exc_info=True)

    async def load_dynamic_from_redis(self, redis_client) -> None:
        """Load persisted dynamic additions from Redis."""
        if not redis_client or not self.settings.get("persist_dynamic_additions", True):
            return
        try:
            import json
            raw = await redis_client.get("domain_allowlist:dynamic")
            if raw:
                self.dynamic_additions = set(json.loads(raw))
                logger.info("Loaded %d dynamic domains from Redis", len(self.dynamic_additions))
        except Exception:
            logger.warning("Failed to load dynamic allowlist from Redis", exc_info=True)
