"""Exchange sessions for automatic end-of-day updates."""
from datetime import date, datetime, timedelta, timezone
from .validation import market_name, iso_date

CALENDARS = {"KOSPI": "XKRX", "KOSDAQ": "XKRX", "NASDAQ": "NASDAQ", "NYSE": "XNYS"}


def completed_sessions(market, start, *, now=None, host_today=None, delay_hours=2):
    """Use exchange holidays and close times, with a provider publication buffer.

    Keep the current updater's conservative policy: host-today is excluded even
    after close. An aware UTC clock and host date may be injected for testing.
    """
    import exchange_calendars as xcals
    market = market_name(market)
    start = iso_date(start)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    last_day = ((host_today or date.today()) - timedelta(days=1)).isoformat()
    if start > last_day:
        raise ValueError("No completed sessions in the requested period")
    calendar = xcals.get_calendar(CALENDARS[market], start=start, end=last_day)
    cutoff = now - timedelta(hours=delay_hours)
    result = [session.strftime("%Y-%m-%d") for session in calendar.sessions
              if calendar.session_close(session).to_pydatetime() <= cutoff]
    if not result:
        raise ValueError(f"No completed exchange sessions for {market}")
    return result
