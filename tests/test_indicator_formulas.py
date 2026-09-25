"""Independent closed-form references for every stored price_detail feature."""
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import tempfile
import unittest

from quantfoundry.indicators.daily import stage
from quantfoundry.stock import Database, PriceBar, update_price, update_price_detail


def weighted_ema(values, period):
    """Expand the weighted sum directly; do not reuse the production recurrence."""
    alpha = Decimal(2) / Decimal(period + 1)
    decay = Decimal(1) - alpha
    weights = [decay ** t for t in range(len(values))]
    return [values[0] * weights[t] + alpha * sum(
        (values[j] * weights[t - j] for j in range(1, t + 1)), Decimal(0))
        for t in range(len(values))]


class IndicatorFormulaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "indicators.sqlite3")
        self.db.initialize()

    def store(self, values, ticker="A", market="NYSE"):
        # Sparse dates deliberately test observation-based periods, not elapsed days.
        bars = [PriceBar(ticker, (date(2024, 1, 1) + timedelta(days=2*i)).isoformat(),
                         float(value), None if i % 3 == 0 else i - 1, float(value) * 3)
                for i, value in enumerate(values)]
        update_price(self.db, market, reversed(bars), source="fixture")
        update_price_detail(self.db, market)
        with self.db.connection() as conn:
            return bars, [dict(row) for row in conn.execute(
                "SELECT * FROM price_detail WHERE market=? AND ticker=? ORDER BY date", (market, ticker))]

    def test_all_numeric_columns_against_decimal_weighted_sums_and_sma_boundaries(self):
        with localcontext() as ctx:
            ctx.prec = 50
            prices = [Decimal(100 + (i * 37) % 79 + 2 * (i // 11)) / Decimal(8) for i in range(260)]
            emas = {n: weighted_ema(prices, n) for n in (5, 12, 20, 26, 40)}
            pairs = {"": (12, 26), "_5_20": (5, 20), "_5_40": (5, 40), "_20_40": (20, 40)}
            macds = {suffix: [a-b for a, b in zip(emas[short], emas[long])]
                     for suffix, (short, long) in pairs.items()}
            signals = {suffix: weighted_ema(values, 9) for suffix, values in macds.items()}
            bars, rows = self.store(prices)
            expected_columns = {"ticker", "market", "date", "adj_close", "volume", "stage",
                                "calculation_version", "insert_time",
                                *(f"sma_{n}" for n in (50, 120, 150, 200)),
                                *(f"ema_{n}" for n in (5, 12, 20, 26, 40)),
                                *("macd" + suffix for suffix in pairs),
                                *("signal" + suffix for suffix in pairs)}
            for i, row in enumerate(rows):
                self.assertEqual(set(row), expected_columns)
                self.assertEqual((row["ticker"], row["market"], row["date"]), ("A", "NYSE", bars[i].date))
                self.assertEqual(row["adj_close"], float(prices[i]))
                self.assertEqual(row["volume"], bars[i].volume)
                self.assertEqual(row["calculation_version"], "daily-v1-first-close-seed")
                datetime.fromisoformat(row["insert_time"])
                for n in (50, 120, 150, 200):
                    if i + 1 < n:
                        self.assertIsNone(row[f"sma_{n}"])
                    else:
                        expected = sum(prices[i+1-n:i+1]) / Decimal(n)
                        self.assertAlmostEqual(row[f"sma_{n}"], float(expected), delta=1e-11)
                for n in emas:
                    self.assertAlmostEqual(row[f"ema_{n}"], float(emas[n][i]), delta=1e-11)
                for suffix in pairs:
                    self.assertAlmostEqual(row["macd" + suffix], float(macds[suffix][i]), delta=1e-11)
                    self.assertAlmostEqual(row["signal" + suffix], float(signals[suffix][i]), delta=1e-11)
            self.assertEqual(update_price_detail(self.db, "NYSE"), 0)

    def test_constant_prices_preserve_zero_macd_signals_and_initial_stage(self):
        _, rows = self.store([Decimal(100)] * 201)
        for row in rows:
            self.assertEqual(row["stage"], 1)
            for n in (5, 12, 20, 26, 40):
                self.assertEqual(row[f"ema_{n}"], 100)
            for suffix in ("", "_5_20", "_5_40", "_20_40"):
                self.assertEqual(row["macd" + suffix], 0)
                self.assertEqual(row["signal" + suffix], 0)

    def test_falling_prices_produce_negative_macd_and_signals(self):
        _, rows = self.store([100, 90, 80, 70])
        self.assertEqual(rows[-1]["stage"], 4)
        for suffix in ("", "_5_20", "_5_40", "_20_40"):
            self.assertLess(rows[-1]["macd" + suffix], 0)
            self.assertLess(rows[-1]["signal" + suffix], 0)
        # Hand calculation: first EMA5=100; second=100+(90-100)/3.
        self.assertAlmostEqual(rows[1]["ema_5"], 100 - 10/3)
        self.assertAlmostEqual(rows[1]["signal"], (-20/13 + 20/27)/5)

    def test_stage_all_six_orders_and_tie_precedence(self):
        cases = [((3,2,1),1), ((2,3,1),2), ((1,3,2),3),
                 ((1,2,3),4), ((2,1,3),5), ((3,1,2),6),
                 ((2,2,1),1), ((1,2,2),3), ((2,1,2),5),
                 ((1,1,2),4), ((1,2,1),2), ((2,1,1),1), ((1,1,1),1)]
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(stage(*values), expected)

    def test_initial_state_is_isolated_between_tickers_and_markets(self):
        self.store([100, 110, 120])
        for ticker, market, first in (("B", "NYSE", 800), ("A", "NASDAQ", 25)):
            _, rows = self.store([first, first+10], ticker=ticker, market=market)
            for n in (5, 12, 20, 26, 40):
                self.assertEqual(rows[0][f"ema_{n}"], first)
            self.assertEqual(rows[0]["macd"], 0)
            self.assertEqual(rows[0]["signal"], 0)
