"""Concurrent downloads with validation and per-ticker commits on one writer."""
from dataclasses import asdict, dataclass, field
from datetime import date
import json
import sqlite3
import sys
import time
import uuid
from .validation import market_name, iso_date, session_dates
from .downloads import DownloadOptions, PriceDownloader
from ..providers.market import YahooProvider
from ..providers.errors import DataUnavailableError, SymbolLookupError, ProviderResponseError
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
    failure_categories: dict[str, str] = field(default_factory=dict)
    no_data: dict[str, str] = field(default_factory=dict)
    missing_sessions: dict[str, dict] = field(default_factory=dict)


def update_market_prices(db, market, sessions, *, provider=None, attempts=2, retry_delay=2,
                         workers=4, timeout=10, downloader=None, tickers=None):
    """Refresh FULL retained history with bounded downloads and serial DB writes.

    Caller supplies completed exchange sessions. New listings may have no prefix
    history; absent sessions are reported separately and never filled.
    Valid available rows are saved even when some expected dates are absent.
    Dropping previously stored dates still requires explicit reconciliation.
    Use a stable start date <= earliest retained price to reconcile adjusted prices.
    Explicit tickers refresh a fixed universe without replacing stock listings.
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
        if tickers is not None:
            from .validation import ticker_name
            tickers = tuple(dict.fromkeys(ticker_name(t) for t in tickers))
            earliest = min((r[0] for t in tickers for r in conn.execute(
                "SELECT MIN(date) FROM price WHERE market=? AND ticker=?", (market, t)) if r[0]), default=None)
        else:
            earliest = conn.execute("SELECT MIN(date) FROM price WHERE market=?", (market,)).fetchone()[0]
        if earliest and start > earliest:
            raise ValueError("Adjusted price refresh must cover the earliest stored date")
        if tickers is None:
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
            phase = "download"
            try:
                if download.error is not None:
                    raise download.error
                bars = download.bars
                if not bars:
                    raise DataUnavailableError("No prices in requested range")
                phase = "validation"
                if any(bar.ticker != ticker for bar in bars):
                    raise ValueError("Mismatched ticker")
                days = [iso_date(bar.date) for bar in bars]
                if len(days) != len(set(days)):
                    raise ValueError("Duplicate dates in provider response")
                if any(day < start or day > end for day in days):
                    raise ValueError("Provider returned out-of-range dates")
                expected = {day for day in sessions if day >= min(days)}
                missing = sorted(expected - set(days))
                off_calendar = sorted(set(days) - set(sessions))
                if off_calendar:
                    raise ValueError(f"Off-calendar sessions: count={len(off_calendar)}, sample={off_calendar[:10]}")
                if missing:
                    report.missing_sessions[ticker] = {"count": len(missing), "sample": missing[:10]}
                phase = "storage"
                with db.connection() as conn:
                    existing = {r[0] for r in conn.execute("SELECT date FROM price WHERE market=? AND ticker=? AND date BETWEEN ? AND ?", (market,ticker,start,end))}
                if not existing.issubset(days):
                    phase = "validation"
                    dropped = sorted(existing - set(days))
                    raise ValueError(f"Provider dropped previously stored dates; count={len(dropped)}, sample={dropped[:10]}; manual reconciliation required")
                phase = "validation"
                # update_price validates values and atomically rolls back bad rows.
                result = update_price(db, market, bars, source=provider.source)
                report.inserted += result.inserted
                report.revised += result.revised
                report.unchanged += result.unchanged
                report.succeeded.append(ticker)
            except DataUnavailableError as exc:
                report.no_data[ticker] = str(exc)
            except Exception as exc:
                if isinstance(exc, sqlite3.Error):
                    phase = "storage"
                elif isinstance(exc, SymbolLookupError):
                    phase = "symbol_lookup"
                elif isinstance(exc, ProviderResponseError):
                    phase = "provider_response"
                report.failed[ticker] = f"{type(exc).__name__}: {exc}"
                report.failure_categories[ticker] = phase
            completed = len(report.succeeded) + len(report.failed) + len(report.no_data)
            if ticker in report.failed:
                outcome = f"FAILED[{report.failure_categories[ticker]}]: {report.failed[ticker]}"
            elif ticker in report.no_data:
                outcome = f"NO_DATA: {report.no_data[ticker]}"
            elif ticker in report.missing_sessions:
                outcome = f"OK_WITH_GAPS: {report.missing_sessions[ticker]}"
            else:
                outcome = "OK"
            log(f"{completed}/{len(tickers)} {ticker} {outcome}; attempts={download.attempts}, "
                f"ticker={download.elapsed:.1f}s, elapsed={time.monotonic() - started:.1f}s, "
                f"success={len(report.succeeded)}, no_data={len(report.no_data)}, failed={len(report.failed)}")
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
    """Save available prices, rebuild successful tickers, rank the eligible subset."""
    market = market_name(market)
    sessions = session_dates(sessions)
    if len(sessions) < lookback + 1:
        raise ValueError("Insufficient sessions for requested RS lookback")
    downloader = downloader or PriceDownloader()
    provider = provider or YahooProvider(timeout=downloader.options.timeout)
    if refresh_listings:
        update_stock(db, market, provider.list_tickers(market))
    report = update_market_prices(db, market, sessions, provider=provider, downloader=downloader)
    detail_rows = update_price_detail(db, market, tickers=report.succeeded)
    excluded = {ticker: "price_update_failed" for ticker in report.failed}
    excluded.update({ticker: "no_price_data" for ticker in report.no_data})
    rs = update_rs_rating_history(db, market, sessions, lookback=lookback,
                                 missing_policy="exclude", excluded_tickers=excluded)
    return {"prices": asdict(report), "details": detail_rows, "rs": rs}
