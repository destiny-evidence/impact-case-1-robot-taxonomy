import asyncio
import time
from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def measure_runtime() -> Generator[float]:
    """Yield an ``ElapsedSeconds`` model with ``seconds`` set on context exit."""
    elapsed: float = 0
    start = time.perf_counter()
    try:
        yield elapsed
    finally:
        elapsed = round(time.perf_counter() - start, 3)


class RateLimiter:
    """Limits calls to `rate` per `period` seconds (e.g. rate=60, period=60 -> 60/min)."""

    def __init__(self, rate: int, period: float = 60.0) -> None:
        self.rate = rate
        self.period = period
        self._permits = float(rate)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: int = 1) -> None:
        """Acquire n permits, or wait until they become available."""
        while True:
            async with self._lock:
                if n > self.rate:
                    raise ValueError(f"cannot reserve {n} requests: larger than rate")
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._permits = min(self.rate, self._permits + elapsed * (self.rate / self.period))
                self._updated_at = now

                if self._permits >= n:
                    self._permits -= n
                    return

                wait_time = (n - self._permits) * (self.period / self.rate)

            await asyncio.sleep(wait_time)

    async def release(self, n: int) -> None:
        """Release unused permits into the pool."""
        async with self._lock:
            self._permits = min(self.rate, self._permits + n)
