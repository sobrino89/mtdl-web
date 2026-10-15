"""The global bandwidth limit: one token bucket that every downloaded byte passes, whatever the download.

The rate is changed at runtime and takes effect at once: waiting downloads wake up and recompute. Bytes taken
beyond the available tokens become debt, so concurrent downloads share the rate in arrival order.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable


class TokenBucket:
    def __init__(self, rate: int = 0, *, burst_seconds: float = 0.5, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._burst_seconds = burst_seconds
        self._rate = max(0, rate)
        self._tokens = 0.0
        self._last = clock()
        self._changed = asyncio.Event()

    @property
    def rate(self) -> int:
        """Bytes per second, 0 = unlimited."""
        return self._rate

    def set_rate(self, rate: int) -> None:
        self._refill()
        self._rate = max(0, rate)
        if self._rate == 0:
            self._tokens = 0.0
        self._tokens = min(self._tokens, self._rate * self._burst_seconds)
        self._changed.set()  # wake every waiter; they recompute with the new rate
        self._changed = asyncio.Event()

    def _refill(self) -> None:
        now = self._clock()
        if self._rate:
            self._tokens = min(self._rate * self._burst_seconds, self._tokens + (now - self._last) * self._rate)
        self._last = now

    async def take(self, size: int) -> None:
        """Account for ``size`` bytes; returns when the limit allows them."""
        if self._rate == 0:
            return
        self._refill()
        self._tokens -= size
        while self._rate and self._tokens < 0:
            changed = self._changed
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout=-self._tokens / self._rate)
            self._refill()
