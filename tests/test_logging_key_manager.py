"""
Tests for audit_logging/key_manager.py

Covers:
- KeyManager initialization
- Permission enforcement (0600)
- Permission rejection (too weak)
- Config loading
- Key fingerprint computation
"""

import pytest
import tempfile
import os
import stat
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from audit_logging.key_manager import KeyManager, KeyPermissionError


@pytest.fixture
def temp_key_dir():
    """Create temporary directory for test keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_keypair_0600(temp_key_dir):
    """Generate Ed25519 keypair with correct 0600 permissions."""
    # Generate new keypair
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Save private key with 0600 permissions
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    private_path = temp_key_dir / "private_0600.pem"
    private_path.write_bytes(private_pem)
    os.chmod(private_path, 0o600)  # Owner read/write only

    # Save public key
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path = temp_key_dir / "public.pem"
    public_path.write_bytes(public_pem)

    return {
        "private_path": private_path,
        "public_path": public_path,
    }


@pytest.fixture
def test_keypair_0644(temp_key_dir):
    """Generate Ed25519 keypair with weak 0644 permissions (too permissive)."""
    # Generate new keypair
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Save private key with 0644 permissions (readable by others!)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    private_path = temp_key_dir / "private_0644.pem"
    private_path.write_bytes(private_pem)
    os.chmod(private_path, 0o644)  # Owner read/write, group/others read

    # Save public key
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path = temp_key_dir / "public.pem"
    public_path.write_bytes(public_pem)

    return {
        "private_path": private_path,
        "public_path": public_path,
    }


class TestKeyManagerInit:
    """Tests for KeyManager initialization."""

    def test_load_keys_with_correct_permissions(self, test_keypair_0600):
        """KeyManager loads successfully with 0600 permissions"""
        km = KeyManager(
            private_key_path=test_keypair_0600["private_path"],
            public_key_path=test_keypair_0600["public_path"],
            enforce_permissions=True
        )

        assert km.private_key is not None
        assert km.public_key is not None
        assert isinstance(km.public_key_fingerprint, str)
        assert len(km.public_key_fingerprint) == 64

    def test_reject_weak_permissions_0644(self, test_keypair_0644):
        """KeyManager rejects private key with 0644 permissions"""
        with pytest.raises(KeyPermissionError, match="LOG_KEY_PERMISSIONS_INVALID"):
            KeyManager(
                private_key_path=test_keypair_0644["private_path"],
                public_key_path=test_keypair_0644["public_path"],
                enforce_permissions=True
            )

    def test_reject_group_readable(self, temp_key_dir):
        """Reject private key with group read permission"""
        # Generate keypair with 0640 (owner rw, group r)
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        private_path = temp_key_dir / "private_0640.pem"
        private_path.write_bytes(private_pem)
        os.chmod(private_path, 0o640)

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        public_path = temp_key_dir / "public.pem"
        public_path.write_bytes(public_pem)

        with pytest.raises(KeyPermissionError, match="LOG_KEY_PERMISSIONS_INVALID"):
            KeyManager(
                private_key_path=private_path,
                public_key_path=public_path,
                enforce_permissions=True
            )

    def test_skip_permission_check_when_disabled(self, test_keypair_0644):
        """KeyManager loads with weak permissions when enforcement disabled"""
        # This should succeed even with 0644
        km = KeyManager(
            private_key_path=test_keypair_0644["private_path"],
            public_key_path=test_keypair_0644["public_path"],
            enforce_permissions=False  # Disable enforcement
        )

        assert km.private_key is not None
        assert km.public_key is not None

    def test_private_key_not_found(self, temp_key_dir):
        """Raise FileNotFoundError if private key doesn't exist"""
        with pytest.raises(FileNotFoundError):
            KeyManager(
                private_key_path=temp_key_dir / "nonexistent.pem",
                public_key_path=temp_key_dir / "public.pem",
                enforce_permissions=True
            )

    def test_public_key_not_found(self, test_keypair_0600):
        """Raise FileNotFoundError if public key doesn't exist"""
        with pytest.raises(FileNotFoundError):
            KeyManager(
                private_key_path=test_keypair_0600["private_path"],
                public_key_path=test_keypair_0600["private_path"].parent / "nonexistent.pem",
                enforce_permissions=True
            )


class TestKeyManagerFingerprint:
    """Tests for public key fingerprint computation."""

    def test_fingerprint_is_sha256(self, test_keypair_0600):
        """Fingerprint is 64-character SHA-256 hex"""
        km = KeyManager(
            private_key_path=test_keypair_0600["private_path"],
            public_key_path=test_keypair_0600["public_path"],
            enforce_permissions=True
        )

        assert len(km.public_key_fingerprint) == 64
        assert all(c in '0123456789abcdef' for c in km.public_key_fingerprint)

    def test_fingerprint_deterministic(self, test_keypair_0600):
        """Same key produces same fingerprint"""
        km1 = KeyManager(
            private_key_path=test_keypair_0600["private_path"],
            public_key_path=test_keypair_0600["public_path"],
            enforce_permissions=True
        )

        km2 = KeyManager(
            private_key_path=test_keypair_0600["private_path"],
            public_key_path=test_keypair_0600["public_path"],
            enforce_permissions=True
        )

        assert km1.public_key_fingerprint == km2.public_key_fingerprint


class TestKeyManagerFromConfig:
    """Tests for from_config class method."""

    def test_from_config_valid(self, test_keypair_0600):
        """Load KeyManager from config dict"""
        config = {
            "ed25519_private_key_path": str(test_keypair_0600["private_path"]),
            "ed25519_public_key_path": str(test_keypair_0600["public_path"]),
        }

        km = KeyManager.from_config(config, enforce_permissions=True)

        assert km.private_key is not None
        assert km.public_key is not None
        assert len(km.public_key_fingerprint) == 64

    def test_from_config_missing_private_key_path(self):
        """Raise KeyError if private key path missing from config"""
        config = {
            "ed25519_public_key_path": "public.pem",
        }

        with pytest.raises(KeyError):
            KeyManager.from_config(config)

    def test_from_config_missing_public_key_path(self):
        """Raise KeyError if public key path missing from config"""
        config = {
            "ed25519_private_key_path": "private.pem",
        }

        with pytest.raises(KeyError):
            KeyManager.from_config(config)

    def test_from_config_with_weak_permissions(self, test_keypair_0644):
        """from_config respects permission enforcement"""
        config = {
            "ed25519_private_key_path": str(test_keypair_0644["private_path"]),
            "ed25519_public_key_path": str(test_keypair_0644["public_path"]),
        }

        with pytest.raises(KeyPermissionError):
            KeyManager.from_config(config, enforce_permissions=True)
