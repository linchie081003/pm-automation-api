"""In-memory rate limits (per process). For multi-worker production use Redis or a gateway."""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

_lock = Lock()
_buckets: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(key: str, *, max_attempts: int, window_seconds: int) -> None:
    now = time.time()
    with _lock:
        hits = _buckets[key]
        hits[:] = [t for t in hits if now - t < window_seconds]
        if len(hits) >= max_attempts:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=429,
                detail="Terlalu banyak percobaan. Coba lagi nanti.",
            )
        hits.append(now)
