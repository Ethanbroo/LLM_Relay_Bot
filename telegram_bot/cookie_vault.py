"""
Cookie vault — encrypt/decrypt browser session cookies with PyNaCl.

Encrypted cookies are stored on a tmpfs mount (RAM-backed) so they
never touch persistent disk. Each saved session has a configurable TTL
after which it auto-expires.

The encryption key is stored in a SOPS-encrypted file and loaded at
startup via credential_vault's SOPS decryption.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path

import nacl.secret
import nacl.utils
import nacl.exceptions

logger = logging.getLogger(__name__)

# Where encrypted session files are stored (should be a tmpfs mount)
SESSION_DIR = Path(os.environ.get("SESSION_DATA_DIR", "/app/session_data"))

# Default TTL for saved sessions (hours)
DEFAULT_TTL_HOURS = int(os.environ.get("COOKIE_TTL_HOURS", "24"))

# Cached encryption key (loaded once from SOPS)
_cached_key: bytes | None = None


def _get_encryption_key() -> bytes:
    """Load the cookie encryption key from the SOPS-encrypted file."""
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    from telegram_bot.credential_vault import _decrypt_sops_file, SECRETS_DIR

    key_path = SECRETS_DIR / "cookie-vault-key.yaml"
    if not key_path.exists():
        raise FileNotFoundError(f"Cookie vault key not found at {key_path}")

    data = _decrypt_sops_file(key_path)
    key_b64 = data.get("cookie_encryption_key", "")
    if not key_b64:
        raise ValueError("cookie_encryption_key is empty in cookie-vault-key.yaml")

    _cached_key = base64.b64decode(key_b64)
    if len(_cached_key) != nacl.secret.SecretBox.KEY_SIZE:
        _cached_key = None
        raise ValueError(
            f"Cookie encryption key must be {nacl.secret.SecretBox.KEY_SIZE} bytes, "
            f"got {len(base64.b64decode(key_b64))}"
        )

    return _cached_key


def _domain_hash(domain: str) -> str:
    """SHA-256 hash of domain name, used as filename."""
    return hashlib.sha256(domain.encode()).hexdigest()[:32]


def encrypt_cookies(cookies: list[dict]) -> bytes:
    """Encrypt a list of cookie dicts using NaCl SecretBox.

    Returns encrypted bytes (nonce prepended automatically by PyNaCl).
    """
    key = _get_encryption_key()
    plaintext = json.dumps(cookies).encode("utf-8")
    box = nacl.secret.SecretBox(key)
    return box.encrypt(plaintext)


def decrypt_cookies(encrypted_data: bytes) -> list[dict]:
    """Decrypt cookie data. Returns empty list if decryption fails."""
    try:
        key = _get_encryption_key()
        box = nacl.secret.SecretBox(key)
        plaintext = box.decrypt(encrypted_data)
        return json.loads(plaintext.decode("utf-8"))
    except nacl.exceptions.CryptoError:
        logger.warning("Cookie decryption failed — data may be corrupted or key changed")
        return []
    except Exception:
        logger.error("Unexpected error decrypting cookies", exc_info=True)
        return []


def save_session(domain: str, cookies: list[dict], ttl_hours: int | None = None) -> None:
    """Encrypt and save cookies for a domain.

    Args:
        domain: The domain these cookies belong to.
        cookies: List of Playwright cookie dicts.
        ttl_hours: Time-to-live in hours. Defaults to COOKIE_TTL_HOURS env var.
    """
    if ttl_hours is None:
        ttl_hours = DEFAULT_TTL_HOURS

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    domain_id = _domain_hash(domain)
    encrypted = encrypt_cookies(cookies)

    # Write encrypted cookie data
    cookie_path = SESSION_DIR / f"{domain_id}.enc"
    cookie_path.write_bytes(encrypted)

    # Write metadata
    now = time.time()
    meta = {
        "domain": domain,
        "created_at": now,
        "ttl_hours": ttl_hours,
        "expires_at": now + (ttl_hours * 3600),
    }
    meta_path = SESSION_DIR / f"{domain_id}.meta.json"
    meta_path.write_text(json.dumps(meta))

    logger.info("Saved session for %s (TTL: %dh)", domain, ttl_hours)


def load_session(domain: str) -> list[dict] | None:
    """Load and decrypt saved cookies for a domain.

    Returns None if no session exists or if it has expired.
    Expired sessions are automatically cleaned up.
    """
    domain_id = _domain_hash(domain)
    cookie_path = SESSION_DIR / f"{domain_id}.enc"
    meta_path = SESSION_DIR / f"{domain_id}.meta.json"

    if not cookie_path.exists() or not meta_path.exists():
        return None

    # Check TTL
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt metadata for %s — removing session", domain)
        _remove_session_files(domain_id)
        return None

    if time.time() > meta.get("expires_at", 0):
        logger.info("Session for %s has expired — removing", domain)
        _remove_session_files(domain_id)
        return None

    # Decrypt
    encrypted = cookie_path.read_bytes()
    cookies = decrypt_cookies(encrypted)

    if not cookies:
        logger.warning("Failed to decrypt session for %s — removing", domain)
        _remove_session_files(domain_id)
        return None

    logger.info("Loaded saved session for %s (%d cookies)", domain, len(cookies))
    return cookies


def delete_session(domain: str) -> bool:
    """Explicitly delete a saved session for a domain."""
    domain_id = _domain_hash(domain)
    return _remove_session_files(domain_id)


def list_sessions() -> list[dict]:
    """List all saved sessions with their domains and expiry times."""
    sessions = []
    if not SESSION_DIR.exists():
        return sessions

    for meta_path in SESSION_DIR.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
            meta["expired"] = time.time() > meta.get("expires_at", 0)
            sessions.append(meta)
        except (json.JSONDecodeError, OSError):
            continue

    return sorted(sessions, key=lambda s: s.get("domain", ""))


def cleanup_expired() -> int:
    """Remove all expired sessions. Returns the number removed."""
    removed = 0
    if not SESSION_DIR.exists():
        return removed

    for meta_path in SESSION_DIR.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
            if time.time() > meta.get("expires_at", 0):
                domain_id = meta_path.stem.replace(".meta", "")
                _remove_session_files(domain_id)
                removed += 1
        except (json.JSONDecodeError, OSError):
            continue

    if removed:
        logger.info("Cleaned up %d expired sessions", removed)
    return removed


def _remove_session_files(domain_id: str) -> bool:
    """Remove cookie and metadata files for a domain hash."""
    cookie_path = SESSION_DIR / f"{domain_id}.enc"
    meta_path = SESSION_DIR / f"{domain_id}.meta.json"

    removed = False
    for path in (cookie_path, meta_path):
        try:
            path.unlink(missing_ok=True)
            removed = True
        except OSError as e:
            logger.warning("Failed to remove %s: %s", path, e)

    return removed
