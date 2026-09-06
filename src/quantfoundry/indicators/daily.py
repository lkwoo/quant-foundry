"""Streaming legacy-compatible indicators, with explicit zero/seed semantics."""
from collections import deque
from .spec import SMA_PERIODS, EMA_PERIODS, MACD_PAIRS

VERSION = "daily-v1-first-close-seed"


def ema(previous, value, period):
    return value if previous is None else previous + 2 / (period + 1) * (value - previous)


def stage(a, b, c):
    # Preserve legacy tie precedence; readiness must be checked by strategies.
    for index, matches in enumerate((a >= b >= c, b >= a >= c, b >= c >= a,
                                      c >= b >= a, c >= a >= b, a >= c >= b), 1):
        if matches:
            return index
    return None


def compute_details(rows):
    """Input: ascending, validated prices for ONE instrument. O(200) memory.

    SMA uses N observations; first price seeds every EMA, first MACD seeds signal.
    Zero MACD/signal is a valid value. Initial Stage is not a readiness guarantee.
    """
    window = deque(maxlen=max(SMA_PERIODS))
    averages, signals = {}, {}
    for row in rows:
        price = row["adj_close"]
        window.append(price)
        for period in EMA_PERIODS:
            averages[period] = ema(averages.get(period), price, period)
        macds = {suffix: averages[short] - averages[long] for short, long, suffix in MACD_PAIRS}
        for suffix, value in macds.items():
            signals[suffix] = ema(signals.get(suffix), value, 9)
        values = list(window)
        yield (row["ticker"], row["market"], row["date"], price, row["volume"],
               stage(averages[5], averages[20], averages[40]),
               *(sum(values[-n:]) / n if len(values) >= n else None for n in SMA_PERIODS),
               *(averages[n] for n in EMA_PERIODS), *macds.values(), *signals.values(), VERSION)
