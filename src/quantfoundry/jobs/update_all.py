"""Configuration-driven four-table updates, with independent market reports."""
import sys
from dataclasses import replace
from ..storage.database import Database
from ..settings import load_settings
from ..data.calendar import completed_sessions
from ..data.validation import market_name
from .daily import run_daily
from ..providers.market import YahooProvider
from ..storage.updates import replace_stock_snapshots
from ..data.downloads import PriceDownloader


def refresh_stock(db, markets, provider=None):
    provider = provider or YahooProvider()
    # No DB writes until every requested market has a response.
    listings = {market: provider.list_tickers(market) for market in markets}
    return replace_stock_snapshots(db, listings)


def run_stock_update(*, config=None, database=None, market=None, provider=None):
    settings = load_settings(config)
    markets = (market_name(market),) if market else settings.markets
    db = Database(database or settings.database)
    db.initialize()
    counts = refresh_stock(db, markets, provider)
    return {"status": "SUCCESS", "database": str(db.path), "stock": counts}


def run_configured_update(*, config=None, database=None, market=None, sessions=None,
                          lookback=None, provider=None, workers=None, timeout=None, attempts=None):
    settings = load_settings(config)
    overrides = {name: value for name, value in
                 (("workers", workers), ("timeout", timeout), ("attempts", attempts)) if value is not None}
    downloader = PriceDownloader(replace(settings.download, **overrides))
    provider = provider or YahooProvider(timeout=downloader.options.timeout)
    markets = (market_name(market),) if market else settings.markets
    if sessions is not None and market is None:
        raise ValueError("--sessions requires --market; markets have different holidays")
    lookback = settings.lookback if lookback is None else lookback
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
        raise ValueError("lookback must be positive")
    db = Database(database or settings.database)
    db.initialize()
    # Build all plans before listing/API data writes.
    plans = {}
    for name in markets:
        start = settings.start
        if db.path.exists():
            with db.connection() as conn:
                earliest = conn.execute("SELECT MIN(date) FROM price WHERE market=?", (name,)).fetchone()[0]
                if earliest:
                    start = min(start, earliest)
        days = list(sessions) if sessions is not None else completed_sessions(name, start)
        if len(days) < lookback + 1:
            raise ValueError(f"{name}: at least {lookback+1} sessions are required")
        plans[name] = days
    refresh_stock(db, markets, provider)
    results = {}
    for name, days in plans.items():
        print(f"[{name}] updating stock, price, price_detail, rs_rating_history through {days[-1]}", file=sys.stderr, flush=True)
        try:
            result = run_daily(db, name, days, lookback=lookback, provider=provider,
                               refresh_listings=False, downloader=downloader)
            status = result["prices"]["status"]
            results[name] = {"status": status, **result}
        except Exception as exc:
            results[name] = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        print(f"[{name}] {results[name]['status']}", file=sys.stderr, flush=True)
    statuses = [r["status"] for r in results.values()]
    status = "SUCCESS" if all(s == "SUCCESS" for s in statuses) else ("FAILED" if all(s == "FAILED" for s in statuses) else "PARTIAL")
    return {"status": status, "database": str(db.path), "markets": results}
