"""Bounded network workers; the caller alone validates and writes to SQLite."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import heapq
from itertools import count
from math import isfinite
import time
from ..providers.errors import RateLimitError, TransientDownloadError


@dataclass(frozen=True)
class DownloadOptions:
    workers: int = 4
    timeout: float = 10
    attempts: int = 2
    retry_delay: float = 2
    rate_limit_delay: float = 30

    def __post_init__(self):
        for name in ("workers", "attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("timeout", "retry_delay", "rate_limit_delay"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not isfinite(value) or value < 0
                    or (name in ("timeout", "rate_limit_delay") and value == 0)):
                raise ValueError(f"{name} must be finite and {'nonnegative' if name == 'retry_delay' else 'positive'}")


@dataclass
class DownloadResult:
    ticker: str
    bars: list | None
    error: Exception | None
    attempts: int
    elapsed: float


class PriceDownloader:
    """Reuse across sequential markets so rate-limit cooldowns carry over.

    Workers perform one network attempt only. Delayed retries don't occupy a
    worker. At most `workers` futures/results are held, not the full universe.
    Already-running requests finish during a cooldown; no new ones are launched.
    """

    def __init__(self, options=None):
        self.options = options or DownloadOptions()
        self.workers = self.options.workers
        self.pause_until = 0.0

    def fetch(self, provider, tickers, start, end, *, log):
        sequence = count()
        ready = [(0.0, next(sequence), ticker, 1) for ticker in tickers]
        heapq.heapify(ready)
        started = {}
        pending = {}
        with ThreadPoolExecutor(max_workers=self.options.workers) as pool:
            try:
                while ready or pending:
                    now = time.monotonic()
                    while (ready and len(pending) < self.workers
                           and now >= max(ready[0][0], self.pause_until)):
                        _, _, ticker, attempt = heapq.heappop(ready)
                        started.setdefault(ticker, now)
                        future = pool.submit(lambda symbol=ticker: list(provider.fetch_prices(symbol, start, end)))
                        pending[future] = (ticker, attempt)
                    delay = None
                    if ready and len(pending) < self.workers:
                        delay = max(0.0, max(ready[0][0], self.pause_until) - time.monotonic())
                    if not pending:
                        time.sleep(delay)
                        continue
                    done, _ = wait(pending, timeout=delay, return_when=FIRST_COMPLETED)
                    results = []
                    # Inspect ALL completed errors before submitting more work.
                    for future in done:
                        ticker, attempt = pending.pop(future)
                        bars, error = None, None
                        try:
                            bars = future.result()
                        except Exception as exc:
                            error = exc
                            if isinstance(exc, RateLimitError):
                                self.workers = min(self.workers, 2)
                                self.pause_until = max(self.pause_until, time.monotonic() + self.options.rate_limit_delay)
                                log(f"{ticker}: rate limited; pause {self.options.rate_limit_delay:g}s, workers={self.workers}")
                            if isinstance(exc, (TransientDownloadError, ConnectionError, TimeoutError)) and attempt < self.options.attempts:
                                retry_at = time.monotonic() + self.options.retry_delay * 2 ** (attempt - 1)
                                heapq.heappush(ready, (retry_at, next(sequence), ticker, attempt + 1))
                                log(f"{ticker}: retry {attempt + 1}/{self.options.attempts} ({type(exc).__name__}: {exc})")
                                continue
                        results.append(DownloadResult(ticker, bars, error, attempt, time.monotonic() - started[ticker]))
                    yield from results
            finally:
                for future in pending:
                    future.cancel()
