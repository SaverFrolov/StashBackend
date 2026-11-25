import time
from collections import defaultdict


class RateLimiter:
    """In-memory sliding window. Valid because a single uvicorn process serves traffic."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max = max_attempts
        self.window = window_seconds
        self.hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.hits[key] if now - t < self.window]
        if len(recent) >= self.max:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        if len(self.hits) > 10_000:
            self.hits = defaultdict(
                list, {k: v for k, v in self.hits.items() if v and now - v[-1] < self.window}
            )
        return True


signin_limiter = RateLimiter(max_attempts=10, window_seconds=15 * 60)
signup_limiter = RateLimiter(max_attempts=5, window_seconds=60 * 60)


def client_ip(request) -> str:
    # Caddy (the only reachable proxy) sets X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
