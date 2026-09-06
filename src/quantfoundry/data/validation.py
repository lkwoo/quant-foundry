from datetime import date
from math import isfinite

MARKETS = ("KOSPI", "KOSDAQ", "NASDAQ", "NYSE")


def market_name(value):
    if not isinstance(value, str) or value.strip().upper() not in MARKETS:
        raise ValueError(f"market must be one of {MARKETS}")
    return value.strip().upper()


def iso_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("date must be YYYY-MM-DD")
    return value


def ticker_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ticker must be a nonempty string")
    return value.strip()


def number(value, name, positive=False, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name}: boolean is not numeric data")
    result = float(value)
    if not isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError(f"Invalid {name}: {value}")
    return result


def session_dates(values):
    dates = tuple(iso_date(value) for value in values)
    if not dates or list(dates) != sorted(set(dates)):
        raise ValueError("sessions must be nonempty, unique and ascending")
    return dates
