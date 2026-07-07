"""Lightweight in-memory rate limiting utilities.

Re-exports the :class:`RateLimiter` from :mod:`utils.helpers` so it lives in
the ``middleware`` namespace as required by the project structure.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List


class RateLimiter:
    """Sliding-window rate limiter keyed by an arbitrary identifier."""

    def __init__(self, max_calls: int = 12, window_seconds: float = 15.0) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: Dict[str, List[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - self.window
        # drop entries outside the window
        bucket[:] = [ts for ts in bucket if ts > cutoff]
        if len(bucket) >= self.max_calls:
            return False
        bucket.append(now)
        return True

    def clear(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)
