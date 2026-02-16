"""
Ed25519 cryptographic signing and verification for Phase 3 audit logging.

Provides:
- Private key loading from PEM files
- Public key loading from PEM files
- Event hash signing (deterministic)
- Signature verification
- Public key fingerprint computation
"""

import base64
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
import hashlib


def load_private_key(key_path: str | Path) -> Ed25519PrivateKey:
    """
    Load Ed25519 private key from PEM file.

    Args:
        key_path: Path to PEM-encoded private key file

    Returns:
        Ed25519PrivateKey instance

    Raises:
        FileNotFoundError: If key file doesn't exist
        ValueError: If PEM format is invalid or not Ed25519
    """
    path = Path(key_path)

    if not path.exists():
        raise FileNotFoundError(f"Private key not found: {key_path}")

    with open(path, 'rb') as f:
        pem_data = f.read()

    try:
        private_key = serialization.load_pem_private_key(
            pem_data,
            password=None  # Phase 3 doesn't require password-protected keys
        )
    except Exception as e:
        raise ValueError(f"Failed to load private key: {e}")

    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError(f"Key is not Ed25519 (got {type(private_key).__name__})")

    return private_key


def load_public_key(key_path: str | Path) -> Ed25519PublicKey:
    """
    Load Ed25519 public key from PEM file.

    Args:
        key_path: Path to PEM-encoded public key file

    Returns:
        Ed25519PublicKey instance

    Raises:
        FileNotFoundError: If key file doesn't exist
        ValueError: If PEM format is invalid or not Ed25519
    """
    path = Path(key_path)

    if not path.exists():
        raise FileNotFoundError(f"Public key not found: {key_path}")

    with open(path, 'rb') as f:
        pem_data = f.read()

    try:
        public_key = serialization.load_pem_public_key(pem_data)
    except Exception as e:
        raise ValueError(f"Failed to load public key: {e}")

    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError(f"Key is not Ed25519 (got {type(public_key).__name__})")

    return public_key


def sign_event_hash(private_key: Ed25519PrivateKey, event_hash_hex: str) -> str:
    """
    Sign an event hash using Ed25519 private key.

    Args:
        private_key: Ed25519PrivateKey instance
        event_hash_hex: 64-character lowercase hex string (SHA-256 hash)

    Returns:
        Base64-encoded signature string (88 characters ending with ==)

    Raises:
        ValueError: If event_hash_hex is not valid hex or not 64 characters
    """
    # Validate event_hash_hex
    if len(event_hash_hex) != 64:
        raise ValueError(f"event_hash must be 64 characters, got {len(event_hash_hex)}")

    try:
        # Convert hex to bytes for signing
        hash_bytes = bytes.fromhex(event_hash_hex)
    except ValueError as e:
        raise ValueError(f"Invalid hex string: {e}")

    # Sign the hash bytes
    signature_bytes = private_key.sign(hash_bytes)

    # Encode to base64
    signature_b64 = base64.b64encode(signature_bytes).decode('ascii')

    return signature_b64


def verify_signature(
    public_key: Ed25519PublicKey,
    event_hash_hex: str,
    signature_b64: str
) -> bool:
    """
    Verify an Ed25519 signature on an event hash.

    Args:
        public_key: Ed25519PublicKey instance
        event_hash_hex: 64-character lowercase hex string (SHA-256 hash)
        signature_b64: Base64-encoded signature string

    Returns:
        True if signature is valid, False otherwise

    Raises:
        ValueError: If inputs are malformed (invalid hex, invalid base64)
    """
    # Validate event_hash_hex
    if len(event_hash_hex) != 64:
        raise ValueError(f"event_hash must be 64 characters, got {len(event_hash_hex)}")

    try:
        hash_bytes = bytes.fromhex(event_hash_hex)
    except ValueError as e:
        raise ValueError(f"Invalid hex string: {e}")

    # Decode signature from base64
    try:
        signature_bytes = base64.b64decode(signature_b64)
    except Exception as e:
        raise ValueError(f"Invalid base64 signature: {e}")

    # Verify signature
    try:
        public_key.verify(signature_bytes, hash_bytes)
        return True
    except Exception:
        # Signature verification failed
        return False


def compute_key_fingerprint(public_key_bytes: bytes) -> str:
    """
    Compute SHA-256 fingerprint of public key bytes.

    This fingerprint is used in audit manifests to identify which key
    was used for signing.

    Args:
        public_key_bytes: Raw bytes of public key (32 bytes for Ed25519)

    Returns:
        64-character lowercase hex string (SHA-256 hash)
    """
    return hashlib.sha256(public_key_bytes).hexdigest()


def get_public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    """
    Extract raw bytes from Ed25519PublicKey.

    Args:
        public_key: Ed25519PublicKey instance

    Returns:
        32-byte raw public key
    """
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
