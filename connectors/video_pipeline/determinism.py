"""Deterministic rendering verification.

Utilities for verifying that the compositing pipeline produces identical
output across runs. For non-AI-generated layers (text overlays, transitions,
effects, compositing), the same Timeline JSON must always produce the
exact same output, byte-for-byte.

How it works:
1. compute_render_hash() renders a sample of frames and hashes their raw bytes
2. If the hash matches across runs, rendering is deterministic
3. verify_determinism() renders twice and compares — any mismatch is a bug

This does NOT cover AI image generation (inherently non-deterministic).
It covers everything downstream: compositing, effects, text, transitions.
"""

import hashlib
import logging
from typing import Optional

from PIL import Image

from .schemas import Timeline
from .compositor import FrameCompositor

logger = logging.getLogger(__name__)


def compute_render_hash(
    timeline: Timeline,
    image_cache: dict[str, Image.Image],
    sample_frames: int = 10,
) -> str:
    """Compute a SHA-256 hash of sampled rendered frames.

    Doesn't render every frame (too slow for verification). Instead, samples
    N evenly-spaced frames and hashes their raw pixel bytes. If the hash
    matches across runs, the rendering is deterministic.

    Args:
        timeline: Timeline to render
        image_cache: Pre-loaded images keyed by clip_id
        sample_frames: How many frames to sample (more = more confident,
                       but slower). 10 covers start, middle, end, and
                       several transition points.

    Returns:
        SHA-256 hex digest of concatenated sampled frame bytes
    """
    compositor = FrameCompositor(timeline, image_cache)
    total = timeline.total_frames

    if total == 0:
        return hashlib.sha256(b"empty").hexdigest()

    if total <= sample_frames:
        indices = list(range(total))
    else:
        step = total / sample_frames
        indices = [round(i * step) for i in range(sample_frames)]
        # Ensure last frame is included
        if indices[-1] != total - 1:
            indices[-1] = total - 1

    hasher = hashlib.sha256()

    # Include timeline metadata in hash for extra specificity
    hasher.update(timeline.timeline_id.encode("utf-8"))
    hasher.update(str(timeline.total_frames).encode("utf-8"))
    hasher.update(str(timeline.fps).encode("utf-8"))

    for idx in indices:
        frame = compositor.render_frame(idx)
        # tobytes() returns raw pixel data — deterministic for identical images
        hasher.update(frame.tobytes())

    return hasher.hexdigest()


def verify_determinism(
    timeline: Timeline,
    image_cache: dict[str, Image.Image],
    sample_frames: int = 10,
    audit_callback: Optional[callable] = None,
) -> dict:
    """Verify that rendering is deterministic by rendering twice and comparing.

    Renders the sampled frames twice with fresh FrameCompositor instances
    and compares every frame byte-for-byte. Any mismatch indicates a
    non-determinism bug.

    Args:
        timeline: Timeline to verify
        image_cache: Pre-loaded images keyed by clip_id
        sample_frames: How many frames to sample
        audit_callback: Optional fn(event_type, payload) for audit events

    Returns:
        Dict with:
            deterministic: bool — True if both passes match
            hash_pass_1: str — SHA-256 from first pass
            hash_pass_2: str — SHA-256 from second pass
            frames_checked: int — Number of frames compared
            mismatched_frames: list[int] — Frame indices that differed (if any)
    """
    total = timeline.total_frames

    if total == 0:
        return {
            "deterministic": True,
            "hash_pass_1": hashlib.sha256(b"empty").hexdigest(),
            "hash_pass_2": hashlib.sha256(b"empty").hexdigest(),
            "frames_checked": 0,
            "mismatched_frames": [],
        }

    if total <= sample_frames:
        indices = list(range(total))
    else:
        step = total / sample_frames
        indices = [round(i * step) for i in range(sample_frames)]
        if indices[-1] != total - 1:
            indices[-1] = total - 1

    # Pass 1
    compositor_1 = FrameCompositor(timeline, image_cache)
    hasher_1 = hashlib.sha256()
    hasher_1.update(timeline.timeline_id.encode("utf-8"))
    hasher_1.update(str(timeline.total_frames).encode("utf-8"))
    hasher_1.update(str(timeline.fps).encode("utf-8"))
    frames_1 = {}
    for idx in indices:
        frame = compositor_1.render_frame(idx)
        raw = frame.tobytes()
        hasher_1.update(raw)
        frames_1[idx] = raw

    # Pass 2
    compositor_2 = FrameCompositor(timeline, image_cache)
    hasher_2 = hashlib.sha256()
    hasher_2.update(timeline.timeline_id.encode("utf-8"))
    hasher_2.update(str(timeline.total_frames).encode("utf-8"))
    hasher_2.update(str(timeline.fps).encode("utf-8"))
    mismatched = []
    for idx in indices:
        frame = compositor_2.render_frame(idx)
        raw = frame.tobytes()
        hasher_2.update(raw)
        if raw != frames_1[idx]:
            mismatched.append(idx)

    hash_1 = hasher_1.hexdigest()
    hash_2 = hasher_2.hexdigest()
    is_deterministic = hash_1 == hash_2

    result = {
        "deterministic": is_deterministic,
        "hash_pass_1": hash_1,
        "hash_pass_2": hash_2,
        "frames_checked": len(indices),
        "mismatched_frames": mismatched,
    }

    if audit_callback:
        audit_callback(
            "RENDER_DETERMINISM_VERIFIED" if is_deterministic else "RENDER_DETERMINISM_FAILED",
            {
                "timeline_id": timeline.timeline_id,
                "deterministic": is_deterministic,
                "render_hash": hash_1,
                "frames_checked": len(indices),
                "mismatched_count": len(mismatched),
            },
        )

    if not is_deterministic:
        logger.warning(
            "Non-deterministic rendering detected! %d/%d frames differ. "
            "Mismatched frames: %s",
            len(mismatched), len(indices), mismatched[:10],
        )
    else:
        logger.info(
            "Determinism verified: %d frames match (hash=%s)",
            len(indices), hash_1[:16],
        )

    return result
