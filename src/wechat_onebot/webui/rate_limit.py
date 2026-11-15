"""Best-effort in-memory login rate limiting for the WebUI (U-05a).

State is per process and never persisted: restarting the gateway clears the
window. Only failed authentication attempts count; a successful login clears the
client's window. This is intentionally local (no shared proxy state or trusted
``X-Forwarded-For`` handling).
"""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(self, *, max_failures: int = 5, window_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float] | None:
        entries = self._failures.get(key)
        if entries is None:
            return None
        threshold = self._clock() - self.window_seconds
        while entries and entries[0] <= threshold:
            entries.popleft()
        if not entries:
            self._failures.pop(key, None)
            return None
        return entries

    def retry_after(self, key: str) -> float:
        """Seconds until the client may try again, or 0 when not blocked."""
        entries = self._recent(key)
        if entries is None or len(entries) < self.max_failures:
            return 0.0
        return max(0.0, entries[0] + self.window_seconds - self._clock())

    def record_failure(self, key: str) -> None:
        now = self._clock()
        entries = self._recent(key)
        if entries is None:
            entries = deque()
            self._failures[key] = entries
        entries.append(now)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)

    @staticmethod
    def retry_after_header(seconds: float) -> str:
        return str(max(1, math.ceil(seconds)))
