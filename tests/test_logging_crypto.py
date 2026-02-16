"""
Tests for audit_logging/crypto.py

Covers:
- Ed25519 key loading
- Signing event hashes
- Signature verification
- Key fingerprint computation
- Error handling
"""

import pytest
import tempfile
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from audit_logging.crypto import (
    load_private_key,
    load_public_key,
    sign_event_hash,
    verify_signature,
    compute_key_fingerprint,
    get_public_key_bytes,
)


@pytest.fixture
def temp_key_dir():
    """Create temporary directory for test keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_keypair(temp_key_dir):
    """Generate Ed25519 keypair for testing."""
    # Generate new keypair
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Save private key
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    private_path = temp_key_dir / "test_private.pem"
    private_path.write_bytes(private_pem)

    # Save public key
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path = temp_key_dir / "test_public.pem"
    public_path.write_bytes(public_pem)

    return {
        "private_key": private_key,
        "public_key": public_key,
        "private_path": private_path,
        "public_path": public_path,
    }


class TestLoadPrivateKey:
    """Tests for load_private_key function."""

    def test_load_valid_private_key(self, test_keypair):
        """Load valid Ed25519 private key"""
        key = load_private_key(test_keypair["private_path"])
        assert isinstance(key, Ed25519PrivateKey)

    def test_private_key_not_found(self, temp_key_dir):
        """Raise FileNotFoundError if key doesn't exist"""
        with pytest.raises(FileNotFoundError, match="Private key not found"):
            load_private_key(temp_key_dir / "nonexistent.pem")

    def test_invalid_pem_format(self, temp_key_dir):
        """Raise ValueError for invalid PEM format"""
        bad_key_path = temp_key_dir / "bad.pem"
        bad_key_path.write_text("not a valid PEM file")

        with pytest.raises(ValueError, match="Failed to load private key"):
            load_private_key(bad_key_path)


class TestLoadPublicKey:
    """Tests for load_public_key function."""

    def test_load_valid_public_key(self, test_keypair):
        """Load valid Ed25519 public key"""
        key = load_public_key(test_keypair["public_path"])
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        assert isinstance(key, Ed25519PublicKey)

    def test_public_key_not_found(self, temp_key_dir):
        """Raise FileNotFoundError if key doesn't exist"""
        with pytest.raises(FileNotFoundError, match="Public key not found"):
            load_public_key(temp_key_dir / "nonexistent.pem")

    def test_invalid_pem_format(self, temp_key_dir):
        """Raise ValueError for invalid PEM format"""
        bad_key_path = temp_key_dir / "bad.pem"
        bad_key_path.write_text("not a valid PEM file")

        with pytest.raises(ValueError, match="Failed to load public key"):
            load_public_key(bad_key_path)


class TestSignEventHash:
    """Tests for sign_event_hash function."""

    def test_sign_valid_hash(self, test_keypair):
        """Sign a valid 64-character hex hash"""
        event_hash = "a" * 64
        signature = sign_event_hash(test_keypair["private_key"], event_hash)

        # Ed25519 signature is 64 bytes, base64 encodes to 88 chars ending with ==
        assert len(signature) == 88
        assert signature.endswith("==")

    def test_sign_different_hashes_different_signatures(self, test_keypair):
        """Different hashes produce different signatures"""
        hash1 = "a" * 64
        hash2 = "b" * 64

        sig1 = sign_event_hash(test_keypair["private_key"], hash1)
        sig2 = sign_event_hash(test_keypair["private_key"], hash2)

        assert sig1 != sig2

    def test_sign_deterministic(self, test_keypair):
        """Same hash produces same signature"""
        event_hash = "c" * 64

        sig1 = sign_event_hash(test_keypair["private_key"], event_hash)
        sig2 = sign_event_hash(test_keypair["private_key"], event_hash)

        assert sig1 == sig2

    def test_sign_invalid_hash_length(self, test_keypair):
        """Reject hash that isn't 64 characters"""
        with pytest.raises(ValueError, match="event_hash must be 64 characters"):
            sign_event_hash(test_keypair["private_key"], "a" * 63)

    def test_sign_invalid_hex(self, test_keypair):
        """Reject non-hex characters"""
        with pytest.raises(ValueError, match="Invalid hex string"):
            sign_event_hash(test_keypair["private_key"], "z" * 64)


class TestVerifySignature:
    """Tests for verify_signature function."""

    def test_verify_valid_signature(self, test_keypair):
        """Verify a valid signature"""
        event_hash = "d" * 64
        signature = sign_event_hash(test_keypair["private_key"], event_hash)

        result = verify_signature(
            test_keypair["public_key"],
            event_hash,
            signature
        )
        assert result is True

    def test_verify_invalid_signature(self, test_keypair):
        """Reject tampered signature"""
        event_hash = "e" * 64
        signature = sign_event_hash(test_keypair["private_key"], event_hash)

        # Tamper with signature (change one character)
        tampered_sig = "A" + signature[1:]

        result = verify_signature(
            test_keypair["public_key"],
            event_hash,
            tampered_sig
        )
        assert result is False

    def test_verify_wrong_hash(self, test_keypair):
        """Reject signature for different hash"""
        event_hash1 = "f" * 64
        event_hash2 = "0" * 64

        signature = sign_event_hash(test_keypair["private_key"], event_hash1)

        # Try to verify with different hash
        result = verify_signature(
            test_keypair["public_key"],
            event_hash2,
            signature
        )
        assert result is False

    def test_verify_invalid_hash_length(self, test_keypair):
        """Reject hash that isn't 64 characters"""
        with pytest.raises(ValueError, match="event_hash must be 64 characters"):
            verify_signature(test_keypair["public_key"], "a" * 63, "dummy_sig==")

    def test_verify_invalid_base64(self, test_keypair):
        """Reject invalid base64 signature"""
        event_hash = "1" * 64

        with pytest.raises(ValueError, match="Invalid base64 signature"):
            verify_signature(test_keypair["public_key"], event_hash, "not-base64!!!")


class TestKeyFingerprint:
    """Tests for compute_key_fingerprint and get_public_key_bytes."""

    def test_fingerprint_deterministic(self, test_keypair):
        """Same public key produces same fingerprint"""
        key_bytes = get_public_key_bytes(test_keypair["public_key"])

        fp1 = compute_key_fingerprint(key_bytes)
        fp2 = compute_key_fingerprint(key_bytes)

        assert fp1 == fp2

    def test_fingerprint_is_sha256(self, test_keypair):
        """Fingerprint is 64-character hex (SHA-256)"""
        key_bytes = get_public_key_bytes(test_keypair["public_key"])
        fingerprint = compute_key_fingerprint(key_bytes)

        assert len(fingerprint) == 64
        assert all(c in '0123456789abcdef' for c in fingerprint)

    def test_different_keys_different_fingerprints(self, temp_key_dir):
        """Different keys produce different fingerprints"""
        # Generate two different keypairs
        key1 = Ed25519PrivateKey.generate().public_key()
        key2 = Ed25519PrivateKey.generate().public_key()

        bytes1 = get_public_key_bytes(key1)
        bytes2 = get_public_key_bytes(key2)

        fp1 = compute_key_fingerprint(bytes1)
        fp2 = compute_key_fingerprint(bytes2)

        assert fp1 != fp2

    def test_public_key_bytes_length(self, test_keypair):
        """Ed25519 public key is 32 bytes"""
        key_bytes = get_public_key_bytes(test_keypair["public_key"])
        assert len(key_bytes) == 32
