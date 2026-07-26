"""Optional API rate limit (SPEC §5: 429, RATE_LIMIT_PER_MINUTE, default off).

In-memory sliding 60s window — sufficient for a single api process (ARCHITECTURE §1).
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    """per_minute=0 disables the limit entirely."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._per_minute = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        # sync endpoints run in Starlette's threadpool — check-then-append must be atomic
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self._per_minute <= 0:
            return True
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - 60.0:
                hits.popleft()
            if len(hits) >= self._per_minute:
                return False
            hits.append(now)
            return True
