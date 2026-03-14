"""
Per-user encrypted credential vault backed by Redis.

Each user gets an isolated namespace. Credentials are encrypted with
AES-256-GCM using a per-user key derived from a master secret + Telegram ID.

Security invariants:
  - Passwords are NEVER returned by list_credentials().
  - Each user can only access their own credentials.
  - Credentials are encrypted at rest with AES-256-GCM.
  - Master secret is an environment variable, never in code.
  - Credentials are never sent to the LLM.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


@dataclass
class StoredCredential:
    """Represents a saved credential (without decrypted password)."""
    domain: str
    username: str
    created_at: float
    credential_id: str  # domain_hash used as ID


def _get_master_secret() -> str:
    """Get master secret from environment."""
    return os.environ.get("CREDENTIAL_MASTER_SECRET", "")


def _derive_user_key(user_id: int) -> bytes:
    """Derive a 256-bit AES key for a specific user."""
    secret = _get_master_secret()
    if not secret:
        raise ValueError("CREDENTIAL_MASTER_SECRET is not set")
    return hmac.new(
        secret.encode(),
        str(user_id).encode(),
        hashlib.sha256,
    ).digest()


def _normalize_domain(raw: str) -> str:
    """Extract and normalize domain from a URL or domain string."""
    raw = raw.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        return (parsed.hostname or raw).lower().strip()
    return raw.lower().strip()


def _domain_hash(domain: str) -> str:
    """SHA-256 hash of normalized domain, used as credential ID."""
    normalized = _normalize_domain(domain)
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def _encrypt(plaintext: bytes, key: bytes) -> tuple[bytes, bytes]:
    """Encrypt with AES-256-GCM. Returns (nonce, ciphertext)."""
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce, ciphertext


def _decrypt(nonce: bytes, ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt AES-256-GCM. Raises on tamper/wrong key."""
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


class UserCredentialVault:
    """Per-user encrypted credential vault backed by Redis."""

    KEY_PREFIX = "cred"

    def __init__(self, redis_client):
        self.redis = redis_client

    def _redis_key(self, user_id: int, domain_hash: str) -> str:
        return f"{self.KEY_PREFIX}:{user_id}:{domain_hash}"

    def _index_key(self, user_id: int) -> str:
        """Key for the set of all credential IDs for a user."""
        return f"{self.KEY_PREFIX}:{user_id}:_index"

    async def save_credential(
        self,
        user_id: int,
        domain: str,
        username: str,
        password: str,
        *,
        is_admin: bool = False,
    ) -> str:
        """Encrypt and store a credential. Returns the credential_id."""
        key = _derive_user_key(user_id)
        nonce, encrypted_password = _encrypt(password.encode("utf-8"), key)

        dh = _domain_hash(domain)
        normalized_domain = _normalize_domain(domain)

        record = {
            "domain": normalized_domain,
            "username": username,
            "encrypted_password": encrypted_password.hex(),
            "nonce": nonce.hex(),
            "created_at": time.time(),
        }

        redis_key = self._redis_key(user_id, dh)
        await self.redis.set(redis_key, json.dumps(record))

        # Set TTL (admin gets no expiry, users get configurable TTL)
        ttl_hours = int(os.environ.get("CREDENTIAL_TTL_HOURS", "24"))
        if not is_admin and ttl_hours > 0:
            await self.redis.expire(redis_key, ttl_hours * 3600)

        # Add to user's credential index
        await self.redis.sadd(self._index_key(user_id), dh)

        logger.info(
            "Saved credential for user=%d domain=%s",
            user_id, normalized_domain,
        )
        return dh

    async def list_credentials(self, user_id: int) -> list[StoredCredential]:
        """List all credentials for a user. NEVER includes passwords."""
        index_key = self._index_key(user_id)
        domain_hashes = await self.redis.smembers(index_key)

        credentials = []
        stale_hashes = []

        for dh in domain_hashes:
            redis_key = self._redis_key(user_id, dh)
            raw = await self.redis.get(redis_key)
            if not raw:
                stale_hashes.append(dh)
                continue
            try:
                record = json.loads(raw)
                credentials.append(StoredCredential(
                    domain=record["domain"],
                    username=record["username"],
                    created_at=record["created_at"],
                    credential_id=dh,
                ))
            except (json.JSONDecodeError, KeyError):
                stale_hashes.append(dh)

        # Clean up stale index entries (expired TTL credentials)
        if stale_hashes:
            await self.redis.srem(index_key, *stale_hashes)

        return sorted(credentials, key=lambda c: c.domain)

    async def get_credential(
        self, user_id: int, credential_id: str
    ) -> dict | None:
        """Decrypt and return a credential. Returns dict with password."""
        redis_key = self._redis_key(user_id, credential_id)
        raw = await self.redis.get(redis_key)
        if not raw:
            return None

        try:
            record = json.loads(raw)
            key = _derive_user_key(user_id)
            password = _decrypt(
                bytes.fromhex(record["nonce"]),
                bytes.fromhex(record["encrypted_password"]),
                key,
            ).decode("utf-8")

            return {
                "domain": record["domain"],
                "username": record["username"],
                "password": password,
                "created_at": record["created_at"],
            }
        except Exception:
            logger.error(
                "Failed to decrypt credential for user=%d id=%s",
                user_id, credential_id, exc_info=True,
            )
            return None

    async def get_credential_by_domain(
        self, user_id: int, domain: str
    ) -> dict | None:
        """Look up credential by domain (for browser agent integration)."""
        dh = _domain_hash(domain)
        return await self.get_credential(user_id, dh)

    async def delete_credential(
        self, user_id: int, credential_id: str
    ) -> bool:
        """Delete a credential. Returns True if it existed."""
        redis_key = self._redis_key(user_id, credential_id)
        deleted = await self.redis.delete(redis_key)
        await self.redis.srem(self._index_key(user_id), credential_id)
        if deleted:
            logger.info(
                "Deleted credential for user=%d id=%s",
                user_id, credential_id,
            )
        return bool(deleted)
