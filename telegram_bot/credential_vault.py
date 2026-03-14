"""
Credential vault — single point of access for all credential operations.

Credentials are stored as SOPS-encrypted YAML files in the secrets/ directory.
Decryption happens via subprocess call to the `sops` binary, which reads the
age private key from the path specified by SOPS_AGE_KEY_FILE.

Security invariants:
  - Plaintext credentials exist ONLY in Python memory, never on disk or in logs.
  - The LLM never sees credential values — only domain names and status messages.
  - Credentials can only be injected on domains listed in the credential file.
  - The browser container has no access to encryption keys or credential files.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Base directory for encrypted credential files
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/app/secrets"))


def _decrypt_sops_file(filepath: Path) -> dict:
    """Decrypt a SOPS-encrypted YAML file and return the plaintext as a dict."""
    result = subprocess.run(
        ["sops", "--decrypt", "--output-type", "json", str(filepath)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SOPS decryption failed for {filepath.name}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _load_domain_map() -> dict[str, str]:
    """Load and decrypt the domain-to-credential-file mapping."""
    domain_map_path = SECRETS_DIR / "domain-map.yaml"
    if not domain_map_path.exists():
        logger.warning("Domain map not found at %s", domain_map_path)
        return {}
    data = _decrypt_sops_file(domain_map_path)
    # Filter out SOPS metadata keys
    return {k: v for k, v in data.items() if not k.startswith("sops")}


def get_credentials(domain: str) -> dict | None:
    """Look up and decrypt credentials for a domain.

    Returns a dict with keys: username, password, totp_seed, login_url, domains.
    Returns None if no credentials exist for the domain.
    The caller MUST call clear_credentials() when done.
    """
    try:
        domain_map = _load_domain_map()
    except Exception:
        logger.error("Failed to load domain map", exc_info=True)
        return None

    cred_name = domain_map.get(domain)
    if not cred_name:
        logger.info("No credentials mapped for domain: %s", domain)
        return None

    cred_path = SECRETS_DIR / f"{cred_name}.yaml"
    if not cred_path.exists():
        logger.error("Credential file not found: %s", cred_path)
        return None

    try:
        data = _decrypt_sops_file(cred_path)
    except Exception:
        logger.error("Failed to decrypt credentials for %s", domain, exc_info=True)
        return None

    # Strip SOPS metadata
    return {k: v for k, v in data.items() if not k.startswith("sops")}


def generate_totp(domain: str) -> str | None:
    """Generate a TOTP code for a domain, if a totp_seed is configured.

    Returns the 6-digit code string, or None if no TOTP seed exists.
    """
    creds = get_credentials(domain)
    if not creds:
        return None

    seed = creds.get("totp_seed", "")
    clear_credentials(creds)

    if not seed:
        return None

    try:
        import pyotp
        totp = pyotp.TOTP(seed)
        return totp.now()
    except Exception:
        logger.error("Failed to generate TOTP for %s", domain, exc_info=True)
        return None


def validate_domain(browser_url: str, allowed_domains: list[str]) -> bool:
    """Check if the browser's current URL matches an allowed domain.

    Supports exact match and subdomain match (e.g. www.spotify.com matches spotify.com).
    """
    parsed = urlparse(browser_url)
    current_domain = parsed.hostname
    if current_domain is None:
        return False

    for allowed in allowed_domains:
        if current_domain == allowed:
            return True
        if current_domain.endswith("." + allowed):
            return True

    return False


def clear_credentials(credentials: dict) -> None:
    """Best-effort overwrite of credential values in memory.

    Python strings are immutable so true secure erasure isn't possible,
    but this reduces the window during which plaintext exists in RAM.
    """
    for key in list(credentials.keys()):
        if isinstance(credentials[key], str):
            # Overwrite the dict entry (original string remains until GC)
            credentials[key] = "\x00" * len(credentials[key])
    credentials.clear()


def list_configured_domains() -> list[str]:
    """Return a list of all domains that have configured credentials."""
    try:
        domain_map = _load_domain_map()
        return sorted(domain_map.keys())
    except Exception:
        logger.error("Failed to list configured domains", exc_info=True)
        return []
