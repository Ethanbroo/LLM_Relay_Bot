"""UUID v7 generator for Phase 6.

UUID v7 provides time-ordered unique identifiers with millisecond precision.
Format: timestamp (48 bits) + version/variant (16 bits) + random (62 bits)
"""

import time
import random
import struct


def generate_uuid7() -> str:
    """Generate UUID v7 with millisecond timestamp.

    UUID v7 format (RFC draft):
    - 48 bits: Unix timestamp in milliseconds
    - 4 bits: version (0111 = 7)
    - 12 bits: random
    - 2 bits: variant (10)
    - 62 bits: random

    Returns:
        UUID v7 string in standard format (e.g., "018d3f64-8b2a-7000-8000-0123456789ab")
    """
    # Get current timestamp in milliseconds
    timestamp_ms = int(time.time() * 1000)

    # Generate random bits
    rand_a = random.getrandbits(12)  # 12 bits
    rand_b = random.getrandbits(62)  # 62 bits

    # Construct UUID v7
    # First 48 bits: timestamp
    # Next 4 bits: version (0111 = 7)
    # Next 12 bits: random A
    # Next 2 bits: variant (10)
    # Next 62 bits: random B

    # Pack into 128 bits
    time_high = (timestamp_ms >> 16) & 0xFFFFFFFF
    time_mid = (timestamp_ms >> 4) & 0xFFFF
    time_low_and_version = ((timestamp_ms & 0xF) << 12) | 0x7000 | rand_a

    clock_seq_and_variant = (0x8000 | (rand_b >> 48)) & 0xFFFF
    node = rand_b & 0xFFFFFFFFFFFF

    # Format as UUID string
    uuid_str = f"{time_high:08x}-{time_mid:04x}-{time_low_and_version:04x}-{clock_seq_and_variant:04x}-{node:012x}"

    return uuid_str


def validate_uuid7(uuid_str: str) -> bool:
    """Validate UUID v7 format.

    Args:
        uuid_str: UUID string to validate

    Returns:
        True if valid UUID v7, False otherwise
    """
    if not isinstance(uuid_str, str):
        return False

    # Check format
    parts = uuid_str.split('-')
    if len(parts) != 5:
        return False

    if len(parts[0]) != 8 or len(parts[1]) != 4 or len(parts[2]) != 4 or len(parts[3]) != 4 or len(parts[4]) != 12:
        return False

    # Check version (7)
    try:
        version_part = int(parts[2][0], 16)
        if version_part != 7:
            return False
    except ValueError:
        return False

    # Check variant (10xx in binary = 8-b in hex)
    try:
        variant_part = int(parts[3][0], 16)
        if variant_part not in [8, 9, 0xa, 0xb]:
            return False
    except ValueError:
        return False

    return True


def extract_timestamp_ms(uuid_str: str) -> int:
    """Extract millisecond timestamp from UUID v7.

    Args:
        uuid_str: UUID v7 string

    Returns:
        Unix timestamp in milliseconds

    Raises:
        ValueError: If not a valid UUID v7
    """
    if not validate_uuid7(uuid_str):
        raise ValueError(f"Invalid UUID v7: {uuid_str}")

    parts = uuid_str.split('-')

    # Extract timestamp components
    time_high = int(parts[0], 16)
    time_mid = int(parts[1], 16)
    time_low = int(parts[2], 16) >> 12  # Upper 4 bits of this part

    # Reconstruct timestamp
    timestamp_ms = (time_high << 16) | (time_mid << 4) | time_low

    return timestamp_ms
