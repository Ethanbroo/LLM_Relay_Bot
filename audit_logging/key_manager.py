"""
Key management with security enforcement for Phase 3 audit logging.

Critical security invariant:
- Private keys MUST have 0600 permissions (owner read/write only)
- System halts if permissions are weaker

Provides:
- KeyManager class for loading and managing keys
- Permission enforcement on private keys
- Key fingerprint tracking
"""

import os
import stat
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from audit_logging.crypto import (
    load_private_key,
    load_public_key,
    get_public_key_bytes,
    compute_key_fingerprint,
)


class KeyPermissionError(Exception):
    """Raised when private key has insecure permissions."""
    pass


class KeyManager:
    """
    Manages Ed25519 keypair for audit log signing and verification.

    Enforces security invariant:
    - Private key file MUST have 0600 permissions (owner read/write only)
    - Loading fails if permissions are weaker

    Attributes:
        private_key: Ed25519PrivateKey instance
        public_key: Ed25519PublicKey instance
        public_key_fingerprint: SHA-256 fingerprint of public key (hex)
    """

    def __init__(
        self,
        private_key_path: str | Path,
        public_key_path: str | Path,
        enforce_permissions: bool = True
    ):
        """
        Initialize KeyManager by loading keypair from disk.

        Args:
            private_key_path: Path to private key PEM file
            public_key_path: Path to public key PEM file
            enforce_permissions: If True, enforce 0600 on private key (default: True)

        Raises:
            FileNotFoundError: If key files don't exist
            KeyPermissionError: If private key permissions are too weak
            ValueError: If keys are invalid or not Ed25519
        """
        self.private_key_path = Path(private_key_path)
        self.public_key_path = Path(public_key_path)
        self.enforce_permissions = enforce_permissions

        # Enforce private key permissions BEFORE loading
        if self.enforce_permissions:
            self._check_private_key_permissions()

        # Load keys
        self.private_key = load_private_key(self.private_key_path)
        self.public_key = load_public_key(self.public_key_path)

        # Compute public key fingerprint for manifest
        public_key_bytes = get_public_key_bytes(self.public_key)
        self.public_key_fingerprint = compute_key_fingerprint(public_key_bytes)

    def _check_private_key_permissions(self) -> None:
        """
        Check that private key has 0600 permissions (owner read/write only).

        Raises:
            KeyPermissionError: If permissions are weaker than 0600
        """
        if not self.private_key_path.exists():
            raise FileNotFoundError(f"Private key not found: {self.private_key_path}")

        # Get file permissions
        file_stat = os.stat(self.private_key_path)
        file_mode = stat.S_IMODE(file_stat.st_mode)

        # Expected: 0o600 (owner read/write only)
        # Check for any permissions beyond owner read/write
        if file_mode & 0o177:  # Check for group/other permissions or execute bits
            actual_perms = oct(file_mode)
            raise KeyPermissionError(
                f"LOG_KEY_PERMISSIONS_INVALID: Private key has insecure permissions "
                f"{actual_perms}. Required: 0o600 (owner read/write only). "
                f"File: {self.private_key_path}"
            )

        # Verify it's exactly 0o600
        if file_mode != 0o600:
            actual_perms = oct(file_mode)
            raise KeyPermissionError(
                f"LOG_KEY_PERMISSIONS_INVALID: Private key has unexpected permissions "
                f"{actual_perms}. Required: 0o600. "
                f"File: {self.private_key_path}"
            )

    @classmethod
    def from_config(cls, config: dict, enforce_permissions: bool = True) -> "KeyManager":
        """
        Create KeyManager from configuration dictionary.

        Args:
            config: Dict with keys 'ed25519_private_key_path' and 'ed25519_public_key_path'
            enforce_permissions: If True, enforce 0600 on private key

        Returns:
            KeyManager instance

        Raises:
            KeyError: If required config keys missing
            KeyPermissionError: If private key permissions too weak
        """
        private_key_path = config["ed25519_private_key_path"]
        public_key_path = config["ed25519_public_key_path"]

        return cls(
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            enforce_permissions=enforce_permissions
        )
