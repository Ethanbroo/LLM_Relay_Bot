#!/usr/bin/env python3
"""Offline approval token signing tool.

Usage:
    python tools/approve.py sign \\
        --action filesystem.write_file \\
        --payload '{"path": "/tmp/test.txt", "content": "hello"}' \\
        --approver alice \\
        --expires-in 1000 \\
        --private-key keys/approval_private.pem

    python tools/approve.py verify \\
        --token token.json \\
        --public-key keys/approval_public.pem

    python tools/approve.py info \\
        --token token.json
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from coordination.approval_tokens import (
    ApprovalToken,
    ApprovalTokenSigner,
    ApprovalTokenVerifier,
    compute_payload_hash
)


def generate_uuid_v7_like():
    """Generate UUID v7-like string (fake but valid format)."""
    base = uuid.uuid4()
    uuid_str = str(base)
    parts = uuid_str.split('-')
    parts[2] = '7' + parts[2][1:]  # Change version to 7
    return '-'.join(parts)


def cmd_sign(args):
    """Sign approval token."""
    # Load private key
    try:
        signer = ApprovalTokenSigner.from_pem_file(args.private_key)
    except Exception as e:
        print(f"Error loading private key: {e}", file=sys.stderr)
        return 1

    # Parse payload
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as e:
        print(f"Error parsing payload JSON: {e}", file=sys.stderr)
        return 1

    # Compute payload hash
    payload_hash = compute_payload_hash(payload)

    # Generate approval ID
    approval_id = generate_uuid_v7_like()

    # Create token
    token = ApprovalToken(
        approval_id=approval_id,
        action=args.action,
        payload_hash=payload_hash,
        approver_principal=args.approver,
        issued_event_seq=args.issued_at,
        expires_event_seq=args.issued_at + args.expires_in,
        signature=""  # Will be populated by signer
    )

    # Sign token
    signed_token = signer.sign_token(token)

    # Output token
    output = signed_token.to_dict()

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"Token written to: {args.output}")
    else:
        print(json.dumps(output, indent=2))

    # Print summary
    print(f"\nApproval Token Summary:", file=sys.stderr)
    print(f"  Approval ID: {approval_id}", file=sys.stderr)
    print(f"  Action: {args.action}", file=sys.stderr)
    print(f"  Approver: {args.approver}", file=sys.stderr)
    print(f"  Issued at: event_seq {args.issued_at}", file=sys.stderr)
    print(f"  Expires at: event_seq {args.issued_at + args.expires_in}", file=sys.stderr)
    print(f"  Payload hash: {payload_hash[:16]}...", file=sys.stderr)
    print(f"  Signature: {signed_token.signature[:16]}...", file=sys.stderr)

    return 0


def cmd_verify(args):
    """Verify approval token."""
    # Load token
    try:
        with open(args.token, 'r') as f:
            token_dict = json.load(f)
        token = ApprovalToken.from_dict(token_dict)
    except Exception as e:
        print(f"Error loading token: {e}", file=sys.stderr)
        return 1

    # Load public key
    try:
        verifier = ApprovalTokenVerifier.from_pem_file(args.public_key)
    except Exception as e:
        print(f"Error loading public key: {e}", file=sys.stderr)
        return 1

    # Verify signature
    valid = verifier.verify_token(token)

    if valid:
        print("✅ Token signature is VALID")
        print(f"\nToken Details:")
        print(f"  Approval ID: {token.approval_id}")
        print(f"  Action: {token.action}")
        print(f"  Approver: {token.approver_principal}")
        print(f"  Issued at: event_seq {token.issued_event_seq}")
        print(f"  Expires at: event_seq {token.expires_event_seq}")
        print(f"  Payload hash: {token.payload_hash}")
        return 0
    else:
        print("❌ Token signature is INVALID", file=sys.stderr)
        return 1


def cmd_info(args):
    """Display token information."""
    # Load token
    try:
        with open(args.token, 'r') as f:
            token_dict = json.load(f)
        token = ApprovalToken.from_dict(token_dict)
    except Exception as e:
        print(f"Error loading token: {e}", file=sys.stderr)
        return 1

    print("Approval Token Information:")
    print(f"  Approval ID: {token.approval_id}")
    print(f"  Action: {token.action}")
    print(f"  Approver Principal: {token.approver_principal}")
    print(f"  Issued at: event_seq {token.issued_event_seq}")
    print(f"  Expires at: event_seq {token.expires_event_seq}")
    print(f"  TTL: {token.expires_event_seq - token.issued_event_seq} events")
    print(f"  Payload Hash: {token.payload_hash}")
    print(f"  Signature: {token.signature[:32]}...")

    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Offline approval token signing tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Command')

    # Sign command
    sign_parser = subparsers.add_parser('sign', help='Sign approval token')
    sign_parser.add_argument('--action', required=True, help='Action identifier')
    sign_parser.add_argument('--payload', required=True, help='Payload JSON string')
    sign_parser.add_argument('--approver', required=True, help='Approver principal ID')
    sign_parser.add_argument('--issued-at', type=int, default=0, help='Issued event_seq (default: 0)')
    sign_parser.add_argument('--expires-in', type=int, required=True, help='TTL in event_seq units')
    sign_parser.add_argument('--private-key', required=True, help='Path to Ed25519 private key PEM')
    sign_parser.add_argument('--output', '-o', help='Output token file (default: stdout)')

    # Verify command
    verify_parser = subparsers.add_parser('verify', help='Verify approval token')
    verify_parser.add_argument('--token', required=True, help='Token JSON file')
    verify_parser.add_argument('--public-key', required=True, help='Path to Ed25519 public key PEM')

    # Info command
    info_parser = subparsers.add_parser('info', help='Display token information')
    info_parser.add_argument('--token', required=True, help='Token JSON file')

    args = parser.parse_args()

    if args.command == 'sign':
        return cmd_sign(args)
    elif args.command == 'verify':
        return cmd_verify(args)
    elif args.command == 'info':
        return cmd_info(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
