"""Optional network adapters. Imports stay lazy for offline SQLite use."""
from datetime import date, timedelta
from ..data.validation import market_name
from ..storage.updates import PriceBar
from .errors import (DataUnavailableError, RateLimitError, TransientDownloadError,
                     SymbolLookupError, ProviderResponseError)


class YahooProvider:
    source = "yahoo-adj-close-auto_adjust_false-v1"

    def __init__(self, *, timeout=10):
        self.timeout = timeout

    def list_tickers(self, market):
        if market_name(market) == "NYSEARCA":
            raise ValueError("NYSEARCA full listings are unsupported; use update-haa for the fixed ETF universe")
        import FinanceDataReader as fdr
        market = market_name(market)
        column, suffix = ("Code", ".KS") if market == "KOSPI" else (("Code", ".KQ") if market == "KOSDAQ" else ("Symbol", ""))
        frame = fdr.StockListing(market)
        codes = frame[column].dropna().astype(str).str.strip()
        return list(dict.fromkeys(code + suffix for code in codes if code))

    def fetch_prices(self, ticker, start, end):
        import yfinance as yf
        from yfinance.exceptions import YFPricesMissingError, YFTzMissingError, YFRateLimitError
        from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError, Timeout, HTTPError
        # Public API uses inclusive dates; Yahoo end is exclusive.
        exclusive_end = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        # download() hides exceptions in empty frames. history() preserves error
        # types, with no nested worker pool or changes to yfinance global config.
        # Per-call raise_errors is deprecated but supported in pinned yfinance 1.7;
        # retain it rather than mutating global exception policy in worker threads.
        try:
            frame = yf.Ticker(ticker).history(start=start, end=exclusive_end, auto_adjust=False,
                                             actions=False, timeout=self.timeout, raise_errors=True)
        except YFPricesMissingError as exc:
            reason = getattr(exc, "yahoo_reason", None)
            # yfinance also uses this exception for malformed JSON responses.
            # Only Yahoo's explicit no-data response confirms absence of prices.
            if reason and ("no data found" in reason.lower() or "no price data" in reason.lower()):
                raise DataUnavailableError(str(exc)) from exc
            raise ProviderResponseError(f"Unconfirmed missing prices: {exc}") from exc
        except YFTzMissingError as exc:
            raise SymbolLookupError(str(exc)) from exc
        except YFRateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except (CurlConnectionError, Timeout) as exc:
            raise TransientDownloadError(str(exc)) from exc
        except HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 429:
                raise RateLimitError(str(exc)) from exc
            if status is not None and 500 <= status < 600:
                raise TransientDownloadError(str(exc)) from exc
            raise
        required = ("Close", "Adj Close", "Volume")
        if frame is None or any(c not in frame.columns for c in required):
            raise ProviderResponseError(f"Malformed price response: {ticker}")
        if frame.empty:
            raise DataUnavailableError(f"No prices in requested range: {ticker}")
        return [PriceBar(ticker, index.date().isoformat(), row["Adj Close"],
                         None if row["Volume"] != row["Volume"] else row["Volume"], row["Close"])
                for index, row in frame.iterrows()]
