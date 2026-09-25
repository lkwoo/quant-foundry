"""Transactional update functions for the four core tables."""
import json
from dataclasses import dataclass
from ..indicators.spec import FEATURE_COLUMNS
from ..data.validation import market_name, ticker_name, iso_date, number, session_dates
from ..indicators.daily import compute_details, VERSION


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    date: str
    adj_close: float
    volume: float | None = None
    close: float | None = None


@dataclass(frozen=True)
class PriceUpdate:
    inserted: int
    revised: int
    unchanged: int


def replace_stock_snapshots(db, listings):
    """Stage complete market snapshots and atomically replace selected markets.

    Every API response must be collected BEFORE calling. Any staging/publish/commit
    failure rolls back the entire replacement. Other markets and price history stay.
    Passing all four markets is equivalent to replacing the full stock table.
    """
    if not listings:
        raise ValueError("No market snapshots supplied")
    snapshots = {}
    for market, tickers in listings.items():
        name = market_name(market)
        if name in snapshots:
            raise ValueError("Duplicate normalized market")
        if isinstance(tickers, (str, bytes)):
            raise ValueError("tickers must be a list, not text")
        symbols = tuple(dict.fromkeys(ticker_name(t) for t in tickers))
        if not symbols:
            raise ValueError(f"Empty listing for {name}; old snapshot retained")
        snapshots[name] = symbols
    with db.transaction() as conn:
        conn.execute("CREATE TEMP TABLE stock_stage (market TEXT NOT NULL,ticker TEXT NOT NULL,update_time TEXT NOT NULL,PRIMARY KEY(market,ticker))")
        conn.executemany("INSERT INTO stock_stage VALUES(?,?,CURRENT_TIMESTAMP)",
                         ((market,ticker) for market,tickers in snapshots.items() for ticker in tickers))
        expected = sum(len(tickers) for tickers in snapshots.values())
        if conn.execute("SELECT COUNT(*) FROM stock_stage").fetchone()[0] != expected:
            raise ValueError("Staged listing count mismatch")
        conn.execute("INSERT INTO instruments(market,ticker,update_time) SELECT market,ticker,update_time FROM stock_stage WHERE true ON CONFLICT(market,ticker) DO NOTHING")
        conn.executemany("DELETE FROM stock WHERE market=?", ((market,) for market in snapshots))
        conn.execute("INSERT INTO stock(market,ticker,update_time,in_current_listing) SELECT market,ticker,update_time,1 FROM stock_stage")
    return {market:len(tickers) for market,tickers in snapshots.items()}


def update_stock(db, market, tickers):
    """Atomically replace one market's current snapshot; retain other markets."""
    market = market_name(market)
    return replace_stock_snapshots(db, {market:tickers})[market]


def update_price(db, market, bars, *, source):
    """Atomic batch upsert. Record revisions and invalidate dependent derived data.

    On error the ENTIRE batch rolls back, including stock rows and invalidation.
    Mixing price providers is rejected; conversion must be explicit.
    """
    market = market_name(market)
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A price source/adjustment policy is required")
    inserted = revised = unchanged = 0
    seen, dirty, checked_sources = set(), {}, set()
    with db.transaction() as conn:
        for bar in bars:
            ticker, day = ticker_name(bar.ticker), iso_date(bar.date)
            key = ticker, day
            if key in seen:
                raise ValueError(f"Duplicate input price: {key}")
            seen.add(key)
            values = (number(bar.adj_close, "adj_close", positive=True),
                      number(bar.volume, "volume", nullable=True),
                      number(bar.close, "close", positive=True, nullable=True), source)
            if ticker not in checked_sources:
                sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM price WHERE market=? AND ticker=?", (market,ticker))}
                if sources and sources != {source}:
                    raise ValueError(f"Price source mismatch for {market}:{ticker}")
                checked_sources.add(ticker)
            conn.execute("INSERT INTO instruments(market,ticker,update_time) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING", (market,ticker))
            old = conn.execute("SELECT adj_close,volume,close,source FROM price WHERE ticker=? AND market=? AND date=?", (ticker,market,day)).fetchone()
            if old is not None and old["source"] != source:
                raise ValueError(f"Price source mismatch for {market}:{ticker}")
            if old is not None and tuple(old) == values:
                unchanged += 1
                continue
            if old is not None:
                conn.execute("INSERT INTO price_revisions(ticker,market,date,old_json,new_json) VALUES(?,?,?,?,?)", (ticker,market,day,json.dumps(dict(old)),json.dumps(dict(zip(("adj_close","volume","close","source"),values)))))
                revised += 1
            else:
                inserted += 1
            conn.execute("INSERT INTO price(ticker,market,date,adj_close,volume,close,source) VALUES(?,?,?,?,?,?,?) ON CONFLICT(ticker,market,date) DO UPDATE SET adj_close=excluded.adj_close,volume=excluded.volume,close=excluded.close,source=excluded.source,insert_time=CURRENT_TIMESTAMP", (ticker,market,day,*values))
            dirty[ticker] = min(day, dirty.get(ticker, day))
        for ticker, day in dirty.items():
            conn.execute("DELETE FROM price_detail WHERE ticker=? AND market=? AND date>=?", (ticker,market,day))
        if dirty:
            conn.execute("DELETE FROM rs_rating_history WHERE market=? AND date>=?", (market,min(dirty.values())))
    return PriceUpdate(inserted, revised, unchanged)


def update_price_detail(db, market, tickers=None):
    """Rebuild changed instruments from their first stored price, streaming rows.

    A per-instrument transaction publishes all its indicators together. This
    correctness-first approach handles inserted gaps and historical corrections.
    Unchanged instruments are skipped. All instruments, including inactive, can rebuild.
    """
    market = market_name(market)
    with db.connection() as conn:
        symbols = ([ticker_name(t) for t in tickers] if tickers is not None else
                   [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM price WHERE market=? ORDER BY ticker", (market,))])
    count = 0
    columns = ("ticker", "market", "date", "adj_close", "volume", *FEATURE_COLUMNS, "calculation_version")
    sql = "INSERT INTO price_detail(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")"
    for ticker in dict.fromkeys(symbols):
        with db.transaction() as conn:
            missing = conn.execute("SELECT 1 FROM price p LEFT JOIN price_detail d ON (p.ticker=d.ticker AND p.market=d.market AND p.date=d.date) WHERE p.market=? AND p.ticker=? AND (d.date IS NULL OR d.calculation_version<>?) LIMIT 1", (market,ticker,VERSION)).fetchone()
            if not missing:
                continue
            conn.execute("DELETE FROM price_detail WHERE market=? AND ticker=?", (market,ticker))
            rows = conn.execute("SELECT * FROM price WHERE market=? AND ticker=? ORDER BY date", (market,ticker))
            conn.executemany(sql, compute_details(rows))
            count += conn.execute("SELECT count(*) FROM price_detail WHERE market=? AND ticker=?", (market,ticker)).fetchone()[0]
    return count


def update_rs_rating_history(db, market, sessions, *, lookback=252, missing_policy="error",
                             excluded_tickers=None):
    """Compute the LAST supplied completed session for the current listing universe.

    sessions must be an authoritative exchange calendar (ascending ISO dates).
    Exactly lookback+1 complete observations are required. Short-history stocks
    are excluded explicitly; missing interior/current observations abort ranking.
    Opt-in missing_policy="exclude" ranks only complete instruments and returns
    every exclusion reason. Its distinct calculation_version records this policy.
    No eligible instruments means no fabricated ranks and INSUFFICIENT_DATA.
    excluded_tickers explicitly prevents stale prices from failed/no-data downloads
    entering a current run's ranking; only supported with missing_policy="exclude".
    Percentile follows legacy PERCENT_RANK: floor(100*(rank-1)/(n-1)), capped at 99.
    Ties share the lowest rank; a singleton ranks zero. This is current-universe
    screening, not a point-in-time historical-universe backtest.
    """
    market = market_name(market)
    sessions = session_dates(sessions)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1 or len(sessions) < lookback + 1:
        raise ValueError("At least lookback+1 exchange sessions are required")
    window = sessions[-lookback-1:]
    start, end = window[0], window[-1]
    if missing_policy not in ("error", "exclude"):
        raise ValueError("missing_policy must be error or exclude")
    if excluded_tickers is not None and missing_policy != "exclude":
        raise ValueError("excluded_tickers requires missing_policy=exclude")
    run_exclusions = {} if excluded_tickers is None else dict(excluded_tickers)
    if any(not isinstance(reason, str) or not reason for reason in run_exclusions.values()):
        raise ValueError("Every excluded ticker must have a reason")
    excluded, returns, rejected = [], [], {}
    version = "rs-v1-session-window" if missing_policy == "error" else "rs-v2-eligible-session-window"
    if excluded_tickers is not None:
        version = "rs-v3-run-eligible-session-window"
    with db.transaction() as conn:
        tickers = [r[0] for r in conn.execute("SELECT ticker FROM stock WHERE market=? AND in_current_listing=1 ORDER BY ticker", (market,))]
        if not tickers:
            raise ValueError("No current listing universe")
        for ticker in tickers:
            if ticker in run_exclusions:
                rejected[ticker] = run_exclusions[ticker]
                continue
            prices = {r[0]: r[1] for r in conn.execute("SELECT date,adj_close FROM price WHERE market=? AND ticker=? AND date BETWEEN ? AND ? ORDER BY date", (market,ticker,start,end))}
            if end not in prices:
                if missing_policy == "exclude":
                    rejected[ticker] = "missing_as_of_price"
                    continue
                raise ValueError(f"Missing current price: {ticker}:{end}")
            first = conn.execute("SELECT MIN(date) FROM price WHERE market=? AND ticker=?", (market,ticker)).fetchone()[0]
            if first > start:
                excluded.append(ticker)
                rejected[ticker] = "insufficient_history"
                continue
            if set(prices) != set(window):
                if missing_policy == "exclude":
                    rejected[ticker] = "incomplete_or_off_calendar_window"
                    continue
                raise ValueError(f"Incomplete or off-calendar price window: {ticker}")
            returns.append((ticker, prices[end] / prices[start] - 1))
        if not returns:
            if missing_policy == "error":
                raise ValueError("No instruments have sufficient history")
            conn.execute("DELETE FROM rs_rating_history WHERE market=? AND date=?", (market,end))
            return {"as_of": end, "ranked": 0, "excluded_short_history": excluded,
                    "excluded": rejected, "universe_total": len(tickers), "status": "INSUFFICIENT_DATA"}
        returns.sort(key=lambda item: (item[1], item[0]))
        universe_json = json.dumps(sorted(t for t, _ in returns))
        conn.execute("DELETE FROM rs_rating_history WHERE market=? AND date=?", (market,end))
        previous, rank = None, 0
        for index, (ticker, value) in enumerate(returns):
            if previous is None or value != previous:
                rank = index
            percentile = min(99, (100 * rank) // (len(returns)-1)) if len(returns)>1 else 0
            conn.execute("INSERT INTO rs_rating_history(ticker,market,date,rs_percentile,return_12m,lookback,universe_size,universe_json,calculation_version) VALUES(?,?,?,?,?,?,?,?,?)", (ticker,market,end,percentile,value,lookback,len(returns),universe_json,version))
            previous = value
    return {"as_of": end, "ranked": len(returns), "excluded_short_history": excluded,
            "excluded": rejected, "universe_total": len(tickers),
            "status": "PARTIAL_UNIVERSE" if rejected else "COMPLETE"}
