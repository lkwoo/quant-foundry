import sys
import types
import unittest
from unittest.mock import Mock, patch
from datetime import date
from quantfoundry.providers.market import YahooProvider


class ProviderTests(unittest.TestCase):
    def test_exclusive_end_and_explicit_adjustment(self):
        class Index:
            def date(self): return date(2024, 1, 3)
        class Frame:
            empty = False
            columns = ("Close", "Adj Close", "Volume")
            def iterrows(self):
                yield Index(), {"Close": 110, "Adj Close": 100, "Volume": 0}
        download = Mock(return_value=Frame())
        with patch.dict(sys.modules, {"yfinance": types.SimpleNamespace(download=download)}):
            bars=YahooProvider().fetch_prices("A","2024-01-01","2024-01-03")
        self.assertEqual(download.call_args.kwargs["end"],"2024-01-04")
        self.assertFalse(download.call_args.kwargs["auto_adjust"])
        self.assertFalse(download.call_args.kwargs["threads"])
        self.assertEqual(bars[0].adj_close,100)
        self.assertEqual(bars[0].close,110)
        self.assertEqual(bars[0].volume,0)

    def test_empty_response_is_error(self):
        with patch.dict(sys.modules, {"yfinance": types.SimpleNamespace(download=Mock(return_value=None))}):
            with self.assertRaises(ValueError):
                YahooProvider().fetch_prices("A","2024-01-01","2024-01-03")
