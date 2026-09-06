import importlib.util
import unittest
from datetime import date, datetime, timezone
from quantfoundry.data.calendar import completed_sessions


@unittest.skipUnless(importlib.util.find_spec("exchange_calendars"), "market-data extra required")
class CalendarTests(unittest.TestCase):
    def test_us_holiday_and_early_close(self):
        for market in ("NASDAQ","NYSE"):
            days=completed_sessions(market,"2024-07-01",now=datetime(2024,7,5,1,tzinfo=timezone.utc),host_today=date(2024,7,5))
            self.assertEqual(days,["2024-07-01","2024-07-02","2024-07-03"])

    def test_korean_holiday(self):
        for market in ("KOSPI","KOSDAQ"):
            days=completed_sessions(market,"2024-08-14",now=datetime(2024,8,16,1,tzinfo=timezone.utc),host_today=date(2024,8,16))
            self.assertEqual(days,["2024-08-14"])

    def test_publication_buffer(self):
        days=completed_sessions("NYSE","2024-07-01",now=datetime(2024,7,3,18,tzinfo=timezone.utc),host_today=date(2024,7,4))
        self.assertEqual(days[-1],"2024-07-02")
