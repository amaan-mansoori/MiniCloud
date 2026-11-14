from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable


class FixedWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def consume(self, key: str) -> int | None:
        now = self._clock()
        with self._lock:
            expired = [
                existing_key
                for existing_key, (started_at, _) in self._windows.items()
                if now - started_at >= self.window_seconds
            ]
            for existing_key in expired:
                del self._windows[existing_key]

            started_at, count = self._windows.get(key, (now, 0))
            if count >= self.limit:
                return max(1, math.ceil(self.window_seconds - (now - started_at)))
            self._windows[key] = (started_at, count + 1)
            return None

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()
