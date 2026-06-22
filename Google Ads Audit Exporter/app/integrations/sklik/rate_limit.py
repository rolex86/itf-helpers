from __future__ import annotations

from random import random


def compute_backoff_seconds(attempt: int, *, minimum: float = 1.0, cap: float = 30.0) -> float:
    attempt = max(1, int(attempt))
    base = min(cap, minimum * (2 ** (attempt - 1)))
    return min(cap, base + random())

