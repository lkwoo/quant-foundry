import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from quantfoundry.data.downloads import DownloadOptions, PriceDownloader
from quantfoundry.data.updater import update_all, update_market_prices
from quantfoundry.providers.errors import DataUnavailableError, SymbolLookupError, ProviderResponseError
from quantfoundry.stock import Database, PriceBar, update_stock, update_price


class DataAvailabilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "test.sqlite3")
        self.db.initialize()
        self.days = ["2024-01-02", "2024-01-03", "2024-01-04"]
        self.provider = Mock(source="fixture")
        self.downloader = PriceDownloader(DownloadOptions(attempts=1))

    def bars(self, ticker, days=None):
        return [PriceBar(ticker, day, 100 + i) for i, day in enumerate(days or self.days)]

    def run_update(self, responses):
        self.provider.list_tickers.return_value = list(responses)
        def fetch(ticker, start, end):
            value = responses[ticker]
            if isinstance(value, Exception):
                raise value
            return value
        self.provider.fetch_prices.side_effect = fetch
        with contextlib.redirect_stderr(io.StringIO()):
            return update_all(self.db, "NYSE", self.days, provider=self.provider,
                              lookback=2, downloader=self.downloader)

    def test_gaps_load_valid_rows_without_filling_and_exclude_incomplete_rs(self):
        result = self.run_update({"A": self.bars("A"), "GAP": self.bars("GAP", self.days[::2])})
        self.assertEqual(result["prices"]["status"], "SUCCESS")
        self.assertEqual(result["prices"]["missing_sessions"], {"GAP": {"count": 1, "sample": ["2024-01-03"]}})
        self.assertEqual(result["prices"]["failed"], {})
        self.assertEqual(result["details"], 5)
        self.assertEqual(result["rs"]["ranked"], 1)
        self.assertEqual(result["rs"]["excluded"]["GAP"], "incomplete_or_off_calendar_window")
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM price WHERE ticker='GAP'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM price_detail WHERE ticker='GAP'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT calculation_version FROM rs_rating_history").fetchone()[0], "rs-v3-run-eligible-session-window")

    def test_missing_recent_date_is_not_download_failure(self):
        result = self.run_update({"A": self.bars("A", self.days[:-1])})
        self.assertEqual(result["prices"]["failed"], {})
        self.assertEqual(result["details"], 2)
        self.assertEqual(result["rs"]["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["rs"]["excluded"], {"A": "missing_as_of_price"})

    def test_later_gap_repair_rebuilds_indicators_and_restores_rs_eligibility(self):
        self.run_update({"A": self.bars("A", self.days[::2])})
        result = self.run_update({"A": self.bars("A")})
        self.assertEqual(result["prices"]["inserted"], 1)
        self.assertEqual(result["prices"]["missing_sessions"], {})
        self.assertEqual(result["details"], 3)
        self.assertEqual(result["rs"]["ranked"], 1)
        self.assertEqual(result["rs"]["excluded"], {})

    def test_no_data_keeps_prices_but_clears_stale_target_date_ranking(self):
        self.run_update({"A": self.bars("A")})
        result = self.run_update({"A": []})
        self.assertEqual(result["prices"]["status"], "SUCCESS")
        self.assertEqual(result["rs"]["ranked"], 0)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM price").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT count(*) FROM rs_rating_history").fetchone()[0], 0)

    def test_all_no_data_is_nonfatal_and_report_is_persisted(self):
        result = self.run_update({"EMPTY": [], "NONE": DataUnavailableError("No data found")})
        self.assertEqual(result["prices"]["status"], "SUCCESS")
        self.assertEqual(set(result["prices"]["no_data"]), {"EMPTY", "NONE"})
        self.assertEqual(result["prices"]["failed"], {})
        self.assertEqual(result["details"], 0)
        self.assertEqual(result["rs"]["status"], "INSUFFICIENT_DATA")
        with self.db.connection() as conn:
            report = json.loads(conn.execute("SELECT result_json FROM update_runs").fetchone()[0])
            self.assertEqual(report["no_data"], result["prices"]["no_data"])

    def test_failed_and_no_data_tickers_cannot_rank_from_stale_stored_prices(self):
        update_price(self.db, "NYSE", self.bars("MISSING") + self.bars("BROKEN"), source="fixture")
        result = self.run_update({"A": self.bars("A"), "MISSING": DataUnavailableError("No data found"),
                                  "BROKEN": ConnectionError("offline")})
        self.assertEqual(result["prices"]["status"], "PARTIAL")
        self.assertEqual(result["prices"]["failure_categories"], {"BROKEN": "download"})
        self.assertEqual(result["details"], 3)
        self.assertEqual(result["rs"]["excluded"], {"MISSING": "no_price_data", "BROKEN": "price_update_failed"})
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM price").fetchone()[0], 9)
            self.assertEqual([row[0] for row in conn.execute("SELECT ticker FROM rs_rating_history")], ["A"])

    def test_lookup_and_malformed_response_remain_distinct_failures(self):
        result = self.run_update({"LOOKUP": SymbolLookupError("no timezone"),
                                  "BAD": ProviderResponseError("missing columns")})
        self.assertEqual(result["prices"]["status"], "FAILED")
        self.assertEqual(result["prices"]["no_data"], {})
        self.assertEqual(result["prices"]["failure_categories"], {"LOOKUP": "symbol_lookup", "BAD": "provider_response"})
        self.assertEqual(result["rs"]["ranked"], 0)

    def test_lost_stored_date_still_rejects_entire_batch_without_rewriting_prices(self):
        update_stock(self.db, "NYSE", ["A"])
        update_price(self.db, "NYSE", self.bars("A"), source="fixture")
        self.provider.fetch_prices.return_value = [PriceBar("A", self.days[0], 500), PriceBar("A", self.days[-1], 700)]
        with contextlib.redirect_stderr(io.StringIO()):
            report = update_market_prices(self.db, "NYSE", self.days, provider=self.provider)
        self.assertEqual(report.failure_categories, {"A": "validation"})
        self.assertIn("2024-01-03", report.failed["A"])
        with self.db.connection() as conn:
            self.assertEqual([row[0] for row in conn.execute("SELECT adj_close FROM price ORDER BY date")], [100, 101, 102])
