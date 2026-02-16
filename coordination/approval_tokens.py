"""Approval tokens with Ed25519 signatures.

Phase 4 Invariants:
- Tokens are single-use, bound to payload hash
- Signatures verified with Ed25519
- Expiry based on event_seq
- Offline signing supported
"""

import hashlib
import json
from typing import Optional
from dataclasses import dataclass
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


@dataclass
class ApprovalToken:
    """Approval token with Ed25519 signature.

    Phase 4 Invariant: Token is bound to payload hash and expires at event_seq.
    """
    approval_id: str  # UUID v7
    action: str  # Action identifier (e.g., "filesystem.write_file")
    payload_hash: str  # SHA-256 hex of canonical payload
    approver_principal: str  # Principal ID of approver
    issued_event_seq: int  # Event seq when issued
    expires_event_seq: int  # Event seq when expires
    signature: str  # Ed25519 signature (hex)

    def to_signable_dict(self) -> dict:
        """Convert to dict for signing (excludes signature field).

        Returns:
            Dict without signature field
        """
        return {
            "approval_id": self.approval_id,
            "action": self.action,
            "payload_hash": self.payload_hash,
            "approver_principal": self.approver_principal,
            "issued_event_seq": self.issued_event_seq,
            "expires_event_seq": self.expires_event_seq
        }

    def to_dict(self) -> dict:
        """Convert to complete dict (includes signature).

        Returns:
            Complete dict representation
        """
        d = self.to_signable_dict()
        d["signature"] = self.signature
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalToken":
        """Create ApprovalToken from dict.

        Args:
            d: Dict representation

        Returns:
            ApprovalToken instance
        """
        return cls(
            approval_id=d["approval_id"],
            action=d["action"],
            payload_hash=d["payload_hash"],
            approver_principal=d["approver_principal"],
            issued_event_seq=d["issued_event_seq"],
            expires_event_seq=d["expires_event_seq"],
            signature=d["signature"]
        )


class ApprovalTokenSigner:
    """Sign approval tokens with Ed25519 private key."""

    def __init__(self, private_key: Ed25519PrivateKey):
        """Initialize signer.

        Args:
            private_key: Ed25519 private key
        """
        self.private_key = private_key

    @classmethod
    def from_pem_file(cls, private_key_path: str) -> "ApprovalTokenSigner":
        """Load signer from PEM file.

        Args:
            private_key_path: Path to private key PEM file

        Returns:
            ApprovalTokenSigner instance
        """
        with open(private_key_path, 'rb') as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=None
            )

        if not isinstance(private_key, Ed25519PrivateKey):
            raise ValueError("Key is not an Ed25519 private key")

        return cls(private_key)

    def sign_token(self, token: ApprovalToken) -> ApprovalToken:
        """Sign approval token.

        Args:
            token: ApprovalToken to sign (signature field ignored)

        Returns:
            New ApprovalToken with signature populated
        """
        # Get signable dict (without signature)
        signable = token.to_signable_dict()

        # Canonicalize
        canonical = self._canonicalize_json(signable)

        # Sign
        signature_bytes = self.private_key.sign(canonical.encode('utf-8'))

        # Create new token with signature
        return ApprovalToken(
            approval_id=token.approval_id,
            action=token.action,
            payload_hash=token.payload_hash,
            approver_principal=token.approver_principal,
            issued_event_seq=token.issued_event_seq,
            expires_event_seq=token.expires_event_seq,
            signature=signature_bytes.hex()
        )

    @staticmethod
    def _canonicalize_json(obj: dict) -> str:
        """Canonicalize JSON (same as Phase 3).

        Args:
            obj: Dict to canonicalize

        Returns:
            Canonical JSON string
        """
        return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


class ApprovalTokenVerifier:
    """Verify approval tokens with Ed25519 public key."""

    def __init__(self, public_key: Ed25519PublicKey):
        """Initialize verifier.

        Args:
            public_key: Ed25519 public key
        """
        self.public_key = public_key

    @classmethod
    def from_pem_file(cls, public_key_path: str) -> "ApprovalTokenVerifier":
        """Load verifier from PEM file.

        Args:
            public_key_path: Path to public key PEM file

        Returns:
            ApprovalTokenVerifier instance
        """
        with open(public_key_path, 'rb') as f:
            public_key = serialization.load_pem_public_key(f.read())

        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("Key is not an Ed25519 public key")

        return cls(public_key)

    def verify_token(self, token: ApprovalToken) -> bool:
        """Verify approval token signature.

        Args:
            token: ApprovalToken to verify

        Returns:
            True if signature valid, False otherwise
        """
        try:
            # Get signable dict (without signature)
            signable = token.to_signable_dict()

            # Canonicalize
            canonical = self._canonicalize_json(signable)

            # Decode signature
            signature_bytes = bytes.fromhex(token.signature)

            # Verify
            self.public_key.verify(signature_bytes, canonical.encode('utf-8'))
            return True

        except (InvalidSignature, ValueError):
            return False

    @staticmethod
    def _canonicalize_json(obj: dict) -> str:
        """Canonicalize JSON (same as Phase 3).

        Args:
            obj: Dict to canonicalize

        Returns:
            Canonical JSON string
        """
        return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def compute_payload_hash(payload: dict) -> str:
    """Compute SHA-256 hash of canonical payload.

    Args:
        payload: Payload dict

    Returns:
        SHA-256 hex digest
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def verify_payload_match(token: ApprovalToken, payload: dict) -> bool:
    """Verify token's payload_hash matches actual payload.

    Args:
        token: ApprovalToken
        payload: Actual payload to check

    Returns:
        True if hashes match, False otherwise
    """
    actual_hash = compute_payload_hash(payload)
    return actual_hash == token.payload_hash
