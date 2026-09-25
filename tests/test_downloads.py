import contextlib
from collections import Counter
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from quantfoundry.data.downloads import DownloadOptions, PriceDownloader
from quantfoundry.data.updater import update_all, update_market_prices
from quantfoundry.providers.errors import DataUnavailableError, RateLimitError, TransientDownloadError
from quantfoundry.stock import Database, PriceBar, update_stock


class DownloadTests(unittest.TestCase):
    def fetch(self, downloader, provider, tickers, log=None):
        return list(downloader.fetch(provider, tickers, "2024-01-01", "2024-01-03", log=log or (lambda _: None)))

    def test_workers_overlap_and_never_exceed_limit(self):
        for workers in (1, 2, 4):
            with self.subTest(workers=workers):
                barrier = threading.Barrier(workers)
                lock = threading.Lock()
                active = peak = 0
                def fetch(ticker, start, end):
                    nonlocal active, peak
                    with lock:
                        active += 1
                        peak = max(peak, active)
                    try:
                        barrier.wait(timeout=5)
                        return [ticker]
                    finally:
                        with lock:
                            active -= 1
                provider = Mock(fetch_prices=fetch)
                results = self.fetch(PriceDownloader(DownloadOptions(workers=workers)), provider,
                                     [str(i) for i in range(workers * 3)])
                self.assertEqual(peak, workers)
                self.assertEqual(len(results), workers * 3)
                self.assertTrue(all(result.error is None for result in results))

    def test_slow_first_ticker_does_not_block_completed_results_or_new_work(self):
        release = threading.Event()
        def fetch(ticker, start, end):
            if ticker == "A":
                if not release.wait(timeout=5):
                    raise TimeoutError("test did not release A")
            return [ticker]
        downloader = PriceDownloader(DownloadOptions(workers=2))
        results = []
        try:
            for result in downloader.fetch(Mock(fetch_prices=fetch), ["A", "B", "C"], "start", "end", log=lambda _: None):
                results.append(result)
                if result.ticker == "C":
                    release.set()
        finally:
            release.set()
        self.assertEqual([r.ticker for r in results], ["B", "C", "A"])
        self.assertTrue(all(r.error is None for r in results))

    def test_retry_only_transient_errors_and_bound_attempts(self):
        calls = Counter()
        def fetch(ticker, start, end):
            calls[ticker] += 1
            if ticker == "MISSING":
                raise DataUnavailableError("no price data")
            if ticker == "INVALID":
                raise ValueError("bad response")
            if ticker == "BUG":
                raise RuntimeError("unexpected")
            if ticker == "OFFLINE":
                raise ConnectionError("offline")
            if ticker == "RECOVER" and calls[ticker] == 1:
                raise TransientDownloadError("timeout")
            return [ticker]
        downloader = PriceDownloader(DownloadOptions(attempts=2, retry_delay=0))
        results = {r.ticker: r for r in self.fetch(downloader, Mock(fetch_prices=fetch),
                   ["MISSING", "INVALID", "BUG", "OFFLINE", "RECOVER"])}
        self.assertEqual(calls, {"MISSING": 1, "INVALID": 1, "BUG": 1, "OFFLINE": 2, "RECOVER": 2})
        self.assertIsNone(results["RECOVER"].error)
        self.assertEqual(results["RECOVER"].attempts, 2)
        self.assertIsInstance(results["OFFLINE"].error, ConnectionError)

    def test_rate_limit_cooldown_and_reduced_concurrency_carry_across_markets(self):
        now = [0.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        downloader = PriceDownloader(DownloadOptions(workers=4, attempts=1, rate_limit_delay=30))
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = peak = 0
        starts = []
        def fetch(ticker, start, end):
            nonlocal active, peak
            with lock:
                starts.append(now[0])
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=5)
                return [ticker]
            finally:
                with lock:
                    active -= 1
        with patch("quantfoundry.data.downloads.time.monotonic", side_effect=lambda: now[0]), patch("quantfoundry.data.downloads.time.sleep", side_effect=sleep):
            failed = self.fetch(downloader, Mock(fetch_prices=Mock(side_effect=RateLimitError("429"))), ["A"])
            self.assertIsInstance(failed[0].error, RateLimitError)
            results = self.fetch(downloader, Mock(fetch_prices=fetch), ["B", "C", "D", "E"])
        self.assertEqual(downloader.workers, 2)
        self.assertEqual(sleeps, [30])
        self.assertTrue(all(start >= 30 for start in starts))
        self.assertEqual(peak, 2)
        self.assertTrue(all(r.error is None for r in results))

    def test_retry_wait_releases_worker_and_rate_limit_pauses_fresh_tickers(self):
        for error, delay in ((TransientDownloadError("timeout"), 2), (RateLimitError("429"), 30)):
            with self.subTest(error=type(error)):
                now = [0.0]
                calls = []
                def sleep(seconds):
                    now[0] += seconds
                def fetch(ticker, start, end):
                    calls.append((ticker, now[0]))
                    if len(calls) == 1:
                        raise error
                    return [ticker]
                downloader = PriceDownloader(DownloadOptions(workers=1))
                with patch("quantfoundry.data.downloads.time.monotonic", side_effect=lambda: now[0]), patch("quantfoundry.data.downloads.time.sleep", side_effect=sleep):
                    results = self.fetch(downloader, Mock(fetch_prices=fetch), ["A", "B"])
                self.assertEqual(calls, [("A", 0), ("B", 30 if isinstance(error, RateLimitError) else 0), ("A", delay)])
                self.assertTrue(all(r.error is None for r in results))
                self.assertEqual(downloader.workers, 1)

    def test_invalid_options(self):
        for name, value in (("workers", 0), ("workers", True), ("workers", 1.5),
                            ("attempts", 0), ("timeout", 0), ("timeout", float("nan")),
                            ("retry_delay", -1), ("retry_delay", float("inf")),
                            ("rate_limit_delay", 0), ("timeout", "10")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                DownloadOptions(**{name: value})


class ConcurrentUpdateTests(unittest.TestCase):
    def test_single_writer_no_data_is_not_failure_and_derived_updates_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.sqlite3")
            db.initialize()
            days = ["2024-01-01", "2024-01-02", "2024-01-03"]
            provider = Mock(source="fixture")
            provider.list_tickers.return_value = ["A", "B", "C", "D"]
            barrier = threading.Barrier(4)
            worker_threads = set()
            def fetch(ticker, start, end):
                worker_threads.add(threading.get_ident())
                barrier.wait(timeout=5)
                if ticker == "B":
                    raise DataUnavailableError("no data")
                return [PriceBar(ticker, day, 100) for day in days]
            provider.fetch_prices.side_effect = fetch
            connection_threads = []
            original = db.connection
            def connection(*args, **kwargs):
                connection_threads.append(threading.get_ident())
                return original(*args, **kwargs)
            output = io.StringIO()
            with patch.object(db, "connection", side_effect=connection), contextlib.redirect_stderr(output):
                result = update_all(db, "NYSE", days, provider=provider, lookback=2)
            self.assertEqual(set(connection_threads), {threading.get_ident()})
            self.assertEqual(len(worker_threads), 4)
            self.assertEqual(result["prices"]["status"], "SUCCESS")
            self.assertEqual(result["prices"]["no_data"], {"B": "no data"})
            self.assertEqual(result["prices"]["failed"], {})
            self.assertEqual(set(result["prices"]["succeeded"]), {"A", "C", "D"})
            self.assertEqual(provider.fetch_prices.call_count, 4)
            self.assertEqual(result["details"], 9)
            self.assertEqual(result["rs"]["ranked"], 3)
            self.assertEqual(result["rs"]["excluded"], {"B": "no_price_data"})
            self.assertIn("4/4", output.getvalue())
            self.assertIn("B NO_DATA:", output.getvalue())
            with db.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM price").fetchone()[0], 9)
                self.assertEqual(conn.execute("SELECT status FROM update_runs").fetchone()[0], "SUCCESS")

    def test_validation_failure_does_not_retry_or_write_bad_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.sqlite3")
            db.initialize()
            update_stock(db, "NYSE", ["A"])
            provider = Mock(source="fixture")
            provider.fetch_prices.return_value = [PriceBar("A", "2024-01-01", 100), PriceBar("A", "2024-01-02", 100)]
            with contextlib.redirect_stderr(io.StringIO()):
                report = update_market_prices(db, "NYSE", ["2024-01-01", "2024-01-03"], provider=provider)
            provider.fetch_prices.assert_called_once()
            self.assertEqual(report.status, "FAILED")
            with db.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM price").fetchone()[0], 0)
