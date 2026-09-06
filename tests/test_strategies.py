import unittest
from datetime import date
from quantfoundry.domain.models import Snapshot, Verdict
from quantfoundry.strategies.trend import TrendStrategy
from quantfoundry.screening.engine import is_candidate


class StrategyTests(unittest.TestCase):
    def evaluate(self, price, average, quality=True):
        snapshot = Snapshot("NASDAQ", "TEST", date(2026, 9, 4), "fixture-v1",
                            {"adj_close": price, "sma_200": average}, quality)
        return TrendStrategy().evaluate(snapshot)

    def test_above_and_equal(self):
        self.assertTrue(is_candidate(self.evaluate(101, 100)))
        self.assertFalse(is_candidate(self.evaluate(100, 100)))

    def test_missing_invalid_and_quality(self):
        for price in (None, float("nan"), float("inf")):
            self.assertEqual(self.evaluate(price, 100)[0].verdict, Verdict.UNKNOWN)
        self.assertFalse(is_candidate(self.evaluate(101, 100, False)))
        self.assertFalse(is_candidate(()))

    def test_zero_is_not_missing(self):
        self.assertEqual(self.evaluate(0, 100)[0].verdict, Verdict.FAIL)
