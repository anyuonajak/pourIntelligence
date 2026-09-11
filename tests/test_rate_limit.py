from collections import deque

from backend.rate_limit import SlidingWindowLimiter


def test_limiter_allows_under_cap():
    limiter = SlidingWindowLimiter()
    limiter._hits["k"] = deque()
    allowed, retry = limiter.allow("k", limit=2, window_seconds=60)
    assert allowed and retry == 0
    allowed, retry = limiter.allow("k", limit=2, window_seconds=60)
    assert allowed
    allowed, retry = limiter.allow("k", limit=2, window_seconds=60)
    assert not allowed
    assert retry >= 1
