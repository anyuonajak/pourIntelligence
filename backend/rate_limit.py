from collections import defaultdict, deque
from time import time


class SlidingWindowLimiter:
    """In-memory per-instance limiter. Fine for a single Render free web service."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int = 3600) -> tuple[bool, int]:
        now = time()
        bucket = self._hits[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = int(bucket[0] + window_seconds - now) + 1
            return False, max(retry_after, 1)
        bucket.append(now)
        return True, 0


limiter = SlidingWindowLimiter()
