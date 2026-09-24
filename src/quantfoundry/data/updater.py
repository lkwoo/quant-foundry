"""Concurrent downloads with validation and per-ticker commits on one writer."""
from dataclasses import asdict, dataclass, field
from datetime import date
import json
import sys
import time
import uuid
from .validation import market_name, iso_date, session_dates
from .downloads import DownloadOptions, PriceDownloader
from ..providers.market import YahooProvider
from ..storage.updates import update_stock, update_price, update_price_detail, update_rs_rating_history


@dataclass
class UpdateReport:
    run_id: str
    status: str = "RUNNING"
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0
    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def update_market_prices(db, market, sessions, *, provider=None, attempts=2, retry_delay=2,
                         workers=4, timeout=10, downloader=None):
    """Refresh FULL retained history with bounded downloads and serial DB writes.

    Caller supplies completed exchange sessions. New listings may have no prefix
    history; gaps after the first returned date are errors, never silently filled.
    Use a stable start date <= earliest retained price to reconcile adjusted prices.
    Only network retrieval is retried; validation/DB errors are reported per ticker.
    """
    market = market_name(market)
    sessions = session_dates(sessions)
    start, end = sessions[0], sessions[-1]
    if date.fromisoformat(end) >= date.today():
        raise ValueError("Use a completed session before today's host date; intraday updates are unsupported")
    downloader = downloader or PriceDownloader(DownloadOptions(workers=workers, timeout=timeout,
                                                               attempts=attempts, retry_delay=retry_delay))
    provider = provider or YahooProvider(timeout=downloader.options.timeout)
    with db.connection() as conn:
        earliest = conn.execute("SELECT MIN(date) FROM price WHERE market=?", (market,)).fetchone()[0]
        if earliest and start > earliest:
            raise ValueError("Adjusted price refresh must cover the earliest stored date")
        tickers = [r[0] for r in conn.execute("SELECT ticker FROM stock WHERE market=? AND in_current_listing=1 ORDER BY ticker", (market,))]
    if not tickers:
        raise ValueError("Update stock listings before downloading prices")
    report = UpdateReport(uuid.uuid4().hex)
    with db.transaction() as conn:
        conn.execute("INSERT INTO update_runs(id,market,start_date,as_of,status) VALUES(?,?,?,?,?)", (report.run_id,market,start,end,report.status))
    started = time.monotonic()
    def log(message):
        print(f"[{market}] {message}", file=sys.stderr, flush=True)
    log(f"prices: {len(tickers)} tickers, workers={downloader.workers}, attempts={downloader.options.attempts}")
    try:
        for download in downloader.fetch(provider, tickers, start, end, log=log):
            ticker = download.ticker
            try:
                if download.error is not None:
                    raise download.error
                bars = download.bars
                if not bars or any(bar.ticker != ticker for bar in bars):
                    raise ValueError("Empty response or mismatched ticker")
                days = [iso_date(bar.date) for bar in bars]
                if len(days) != len(set(days)):
                    raise ValueError("Duplicate dates in provider response")
                if any(day < start or day > end for day in days):
                    raise ValueError("Provider returned out-of-range dates")
                expected = {day for day in sessions if day >= min(days)}
                if set(days) != expected:
                    raise ValueError("Missing or off-calendar sessions")
                with db.connection() as conn:
                    existing = {r[0] for r in conn.execute("SELECT date FROM price WHERE market=? AND ticker=? AND date BETWEEN ? AND ?", (market,ticker,start,end))}
                if not existing.issubset(days):
                    raise ValueError("Provider dropped previously stored dates; manual reconciliation required")
                result = update_price(db, market, bars, source=provider.source)
                report.inserted += result.inserted
                report.revised += result.revised
                report.unchanged += result.unchanged
                report.succeeded.append(ticker)
            except Exception as exc:
                report.failed[ticker] = f"{type(exc).__name__}: {exc}"
            completed = len(report.succeeded) + len(report.failed)
            outcome = f"FAILED: {report.failed[ticker]}" if ticker in report.failed else "OK"
            log(f"{completed}/{len(tickers)} {ticker} {outcome}; attempts={download.attempts}, "
                f"ticker={download.elapsed:.1f}s, elapsed={time.monotonic() - started:.1f}s, "
                f"success={len(report.succeeded)}, failed={len(report.failed)}")
        report.status = "PARTIAL" if report.failed and report.succeeded else ("FAILED" if report.failed else "SUCCESS")
    except BaseException:
        report.status = "FAILED"
        raise
    finally:
        with db.transaction() as conn:
            conn.execute("UPDATE update_runs SET status=?,result_json=?,finished_at=CURRENT_TIMESTAMP WHERE id=?", (report.status,json.dumps(asdict(report)),report.run_id))
    return report


def update_all(db, market, sessions, *, provider=None, lookback=252, refresh_listings=True,
               downloader=None):
    """Update all four tables. Skip derived updates on partial price retrieval."""
    market = market_name(market)
    sessions = session_dates(sessions)
    if len(sessions) < lookback + 1:
        raise ValueError("Insufficient sessions for requested RS lookback")
    downloader = downloader or PriceDownloader()
    provider = provider or YahooProvider(timeout=downloader.options.timeout)
    if refresh_listings:
        update_stock(db, market, provider.list_tickers(market))
    report = update_market_prices(db, market, sessions, provider=provider, downloader=downloader)
    if report.status != "SUCCESS":
        return {"prices": asdict(report), "details": None, "rs": None}
    detail_rows = update_price_detail(db, market)
    rs = update_rs_rating_history(db, market, sessions, lookback=lookback)
    return {"prices": asdict(report), "details": detail_rows, "rs": rs}
