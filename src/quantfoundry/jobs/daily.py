"""Daily data job. Strategy screening will be added after its persistence exists."""
from datetime import date
from ..data.validation import market_name, session_dates
from ..data.updater import update_all


def run_daily(db, market, sessions, *, lookback=252, provider=None, refresh_listings=True):
    """Refresh stock, price, price_detail and RS for one market.

    Validate all inputs before any listing/database mutation. sessions are
    caller-supplied completed exchange sessions, covering retained price history.
    Database initialization remains an explicit separate operation.
    """
    market = market_name(market)
    sessions = session_dates(sessions)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
        raise ValueError("lookback must be a positive integer")
    if len(sessions) < lookback + 1:
        raise ValueError("Daily RS requires at least lookback+1 exchange sessions")
    if date.fromisoformat(sessions[-1]) >= date.today():
        raise ValueError("Only completed sessions before today's host date are supported")
    with db.connection() as conn:
        earliest = conn.execute("SELECT MIN(date) FROM price WHERE market=?", (market,)).fetchone()[0]
    if earliest and sessions[0] > earliest:
        raise ValueError("sessions must cover the earliest stored price date")
    return update_all(db, market, sessions, provider=provider, lookback=lookback, refresh_listings=refresh_listings)
