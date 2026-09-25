import sys
import types
import unittest
from unittest.mock import Mock, patch
from datetime import date
from quantfoundry.providers.market import YahooProvider
from quantfoundry.providers.errors import (DataUnavailableError, RateLimitError, TransientDownloadError,
                                         SymbolLookupError, ProviderResponseError)


class MissingPrices(Exception):
    def __init__(self, message, yahoo_reason=None):
        super().__init__(message)
        self.yahoo_reason = yahoo_reason


class MissingTimezone(Exception):
    pass


class Limited(Exception):
    pass


class HttpFailure(Exception):
    def __init__(self, status):
        self.response = types.SimpleNamespace(status_code=status)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.history = Mock()
        self.ticker = Mock(return_value=types.SimpleNamespace(history=self.history))
        modules = {
            "yfinance": types.SimpleNamespace(Ticker=self.ticker),
            "yfinance.exceptions": types.SimpleNamespace(YFPricesMissingError=MissingPrices,
                YFTzMissingError=MissingTimezone, YFRateLimitError=Limited),
            "curl_cffi.requests.exceptions": types.SimpleNamespace(ConnectionError=ConnectionError,
                Timeout=TimeoutError, HTTPError=HttpFailure),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_exclusive_end_and_explicit_adjustment(self):
        class Index:
            def date(self): return date(2024, 1, 3)
        class Frame:
            empty = False
            columns = ("Close", "Adj Close", "Volume")
            def iterrows(self):
                yield Index(), {"Close": 110, "Adj Close": 100, "Volume": 0}
        self.history.return_value = Frame()
        bars=YahooProvider(timeout=7).fetch_prices("A","2024-01-01","2024-01-03")
        self.ticker.assert_called_once_with("A")
        self.assertEqual(self.history.call_args.kwargs["end"],"2024-01-04")
        self.assertFalse(self.history.call_args.kwargs["auto_adjust"])
        self.assertTrue(self.history.call_args.kwargs["raise_errors"])
        self.assertEqual(self.history.call_args.kwargs["timeout"], 7)
        self.assertEqual(bars[0].adj_close,100)
        self.assertEqual(bars[0].close,110)
        self.assertEqual(bars[0].volume,0)

    def test_malformed_response_is_not_no_data(self):
        self.history.return_value = None
        with self.assertRaises(ProviderResponseError):
            YahooProvider().fetch_prices("A","2024-01-01","2024-01-03")

    def test_empty_valid_frame_is_no_data(self):
        self.history.return_value = types.SimpleNamespace(empty=True, columns=("Close", "Adj Close", "Volume"))
        with self.assertRaises(DataUnavailableError):
            YahooProvider().fetch_prices("A", "2024-01-01", "2024-01-03")

    def test_missing_columns_is_not_no_data(self):
        self.history.return_value = types.SimpleNamespace(empty=True, columns=())
        with self.assertRaises(ProviderResponseError):
            YahooProvider().fetch_prices("A", "2024-01-01", "2024-01-03")

    def test_error_classification_preserves_retry_and_cooldown_signals(self):
        for error, expected in ((MissingPrices("empty", "No data found, symbol may be delisted"), DataUnavailableError),
                                (MissingPrices("unknown"), ProviderResponseError),
                                (MissingPrices("auth", "Invalid Crumb"), ProviderResponseError),
                                (MissingTimezone("no timezone"), SymbolLookupError),
                                (Limited("429"), RateLimitError),
                                (HttpFailure(429), RateLimitError),
                                (HttpFailure(503), TransientDownloadError),
                                (TimeoutError("timeout"), TransientDownloadError),
                                (ConnectionError("offline"), TransientDownloadError),
                                (HttpFailure(403), HttpFailure),
                                (ValueError("bad input"), ValueError)):
            with self.subTest(error=type(error), expected=expected):
                self.history.side_effect = error
                with self.assertRaises(expected):
                    YahooProvider().fetch_prices("A", "2024-01-01", "2024-01-03")
