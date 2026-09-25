"""Exercise installed yfinance parsing with HTTP fixtures, never live Yahoo."""
import copy
import importlib.util
import threading
import unittest
from unittest.mock import Mock, patch

from quantfoundry.data.downloads import PriceDownloader
from quantfoundry.providers.errors import (DataUnavailableError, RateLimitError, TransientDownloadError,
                                         SymbolLookupError, ProviderResponseError)
from quantfoundry.providers.market import YahooProvider


@unittest.skipUnless(importlib.util.find_spec("yfinance"), "market-data extra required")
class YahooIntegrationTests(unittest.TestCase):
    def test_concurrent_history_preserves_adjusted_prices_dates_and_volume(self):
        from yfinance.base import TickerBase
        from yfinance.data import YfData
        barrier = threading.Barrier(4)
        payload = {"chart": {"error": None, "result": [{
            "meta": {"instrumentType": "EQUITY", "exchangeTimezoneName": "America/New_York",
                     "currency": "USD", "validRanges": ["1mo"]},
            "timestamp": [1704205800, 1704292200],
            "indicators": {"quote": [{"open": [100, 110], "high": [112, 122],
                                       "low": [99, 109], "close": [110, 120], "volume": [0, 20]}],
                           "adjclose": [{"adjclose": [100, 108]}]},
        }]}}
        def get(*args, **kwargs):
            barrier.wait(timeout=5)
            return Mock(text="fixture", json=lambda: copy.deepcopy(payload))
        with patch.object(TickerBase, "_get_ticker_tz", return_value="America/New_York"), patch.object(YfData, "get", side_effect=get), patch.object(YfData, "cache_get", side_effect=get):
            results = list(PriceDownloader().fetch(YahooProvider(), ["A", "B", "C", "D"],
                           "2024-01-02", "2024-01-03", log=lambda _: None))
        self.assertEqual(len(results), 4)
        for result in results:
            self.assertIsNone(result.error)
            self.assertEqual([bar.date for bar in result.bars], ["2024-01-02", "2024-01-03"])
            self.assertEqual([bar.adj_close for bar in result.bars], [100, 108])
            self.assertEqual([bar.close for bar in result.bars], [110, 120])
            self.assertEqual([bar.volume for bar in result.bars], [0, 20])
            self.assertTrue(all(bar.ticker == result.ticker for bar in result.bars))

    def test_real_yahoo_errors_are_not_hidden_in_empty_frames(self):
        from curl_cffi.requests.exceptions import Timeout
        from yfinance.base import TickerBase
        from yfinance.data import YfData
        from yfinance.exceptions import YFRateLimitError
        response = Mock(text="fixture", json=lambda: {"chart": {"result": None,
                        "error": {"description": "No data found, symbol may be delisted"}}})
        for cause, expected in ((response, DataUnavailableError), (YFRateLimitError(), RateLimitError),
                                (Mock(text="fixture", json=lambda: {}), ProviderResponseError),
                                (Timeout("timeout"), TransientDownloadError)):
            def get(*args, **kwargs):
                if isinstance(cause, Exception):
                    raise cause
                return cause
            with self.subTest(expected=expected), patch.object(TickerBase, "_get_ticker_tz", return_value="America/New_York"), patch.object(YfData, "get", side_effect=get), patch.object(YfData, "cache_get", side_effect=get):
                with self.assertRaises(expected):
                    YahooProvider().fetch_prices("A", "2024-01-02", "2024-01-03")
        with patch.object(TickerBase, "_get_ticker_tz", return_value=None), patch.object(YfData, "get") as get, patch.object(YfData, "cache_get") as cached:
            with self.assertRaises(SymbolLookupError):
                YahooProvider().fetch_prices("A", "2024-01-02", "2024-01-03")
            get.assert_not_called()
            cached.assert_not_called()
