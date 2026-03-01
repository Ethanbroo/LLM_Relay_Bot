"""LRU cache for rendered frames.

Prevents re-rendering when scrubbing back and forth over the same frames.
Thread-safe via OrderedDict (GIL-protected for single-writer use case).
"""

import threading
from collections import OrderedDict
from typing import Optional


class FrameCache:
    """LRU cache for JPEG-encoded frames.

    Stores rendered frame bytes keyed by frame number. When the cache
    exceeds max_frames, the least recently used entries are evicted.

    Usage:
        cache = FrameCache(max_frames=300)
        cache.put(42, jpeg_bytes)
        data = cache.get(42)  # Returns bytes or None
    """

    def __init__(self, max_frames: int = 300):
        """
        Args:
            max_frames: Maximum number of frames to cache.
                        300 frames at ~50KB/frame = ~15MB.
        """
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._max = max_frames
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, frame_num: int) -> Optional[bytes]:
        """Retrieve a cached frame.

        Args:
            frame_num: Frame number to look up

        Returns:
            JPEG bytes if cached, None if not
        """
        with self._lock:
            if frame_num in self._cache:
                self._cache.move_to_end(frame_num)
                self._hits += 1
                return self._cache[frame_num]
            self._misses += 1
            return None

    def put(self, frame_num: int, data: bytes) -> None:
        """Store a rendered frame.

        Args:
            frame_num: Frame number
            data: JPEG-encoded bytes
        """
        with self._lock:
            if frame_num in self._cache:
                self._cache.move_to_end(frame_num)
                self._cache[frame_num] = data
            else:
                if len(self._cache) >= self._max:
                    self._cache.popitem(last=False)
                self._cache[frame_num] = data

    def invalidate(self, frame_num: int) -> None:
        """Remove a specific frame from cache."""
        with self._lock:
            self._cache.pop(frame_num, None)

    def clear(self) -> None:
        """Clear all cached frames."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def size(self) -> int:
        """Number of frames currently cached."""
        return len(self._cache)

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0-1.0)."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict:
        """Cache statistics."""
        return {
            "size": self.size,
            "max_size": self._max,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 3),
            "memory_estimate_mb": round(
                sum(len(v) for v in self._cache.values()) / (1024 * 1024), 2
            ),
        }
