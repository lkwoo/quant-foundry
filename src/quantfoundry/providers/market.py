"""Optional network adapters. Imports stay lazy for offline SQLite use."""
from datetime import date, timedelta
from ..data.validation import market_name
from ..storage.updates import PriceBar


class YahooProvider:
    source = "yahoo-adj-close-auto_adjust_false-v1"

    def list_tickers(self, market):
        import FinanceDataReader as fdr
        market = market_name(market)
        column, suffix = ("Code", ".KS") if market == "KOSPI" else (("Code", ".KQ") if market == "KOSDAQ" else ("Symbol", ""))
        frame = fdr.StockListing(market)
        codes = frame[column].dropna().astype(str).str.strip()
        return list(dict.fromkeys(code + suffix for code in codes if code))

    def fetch_prices(self, ticker, start, end):
        import yfinance as yf
        # Public API uses inclusive dates; Yahoo end is exclusive.
        exclusive_end = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        frame = yf.download(ticker, start=start, end=exclusive_end, auto_adjust=False,
                            threads=False, progress=False, timeout=20, multi_level_index=False)
        required = ("Close", "Adj Close", "Volume")
        if frame is None or frame.empty or any(c not in frame.columns for c in required):
            raise ValueError(f"Empty or malformed price response: {ticker}")
        return [PriceBar(ticker, index.date().isoformat(), row["Adj Close"],
                         None if row["Volume"] != row["Volume"] else row["Volume"], row["Close"])
                for index, row in frame.iterrows()]
