import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import Mock
from quantfoundry.stock import (Database, PriceBar, update_stock, update_price,
    update_price_detail, update_rs_rating_history, update_market_prices, update_all)
from quantfoundry.indicators.daily import ema, stage


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "prices.sqlite3")
        self.db.initialize()

    def rows(self, table):
        with self.db.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM " + table)]

    def seed(self, ticker="A", prices=(100, 110, 120), market="NASDAQ"):
        with self.db.connection() as c:
            listed=[row[0] for row in c.execute("SELECT ticker FROM stock WHERE market=?",(market,))]
        update_stock(self.db,market,listed+[ticker])
        bars = [PriceBar(ticker, (date(2024,1,1)+timedelta(days=i)).isoformat(), p, 0)
                for i,p in enumerate(prices)]
        return update_price(self.db, market, bars, source="fixture")

    def test_init_idempotent_and_foreign_keys(self):
        self.db.initialize()
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        legacy = Path(self.tmp.name) / "legacy.db"
        c = sqlite3.connect(legacy)
        c.execute("CREATE TABLE stock(ticker)")
        c.commit(); c.close()
        before = legacy.read_bytes()
        with self.assertRaises(ValueError): Database(legacy).initialize()
        self.assertEqual(before, legacy.read_bytes())
        with self.assertRaises(ValueError):
            update_stock(Database(legacy), "NYSE", ["A"])

    def test_listing_snapshot_preserves_history_and_reactivation(self):
        update_stock(self.db, " nasdaq ", ["A", "B", "A"])
        update_stock(self.db, "NASDAQ", ["B"])
        self.assertEqual({r["ticker"]:r["in_current_listing"] for r in self.rows("stock")}, {"B":1})
        with self.assertRaises(ValueError): update_stock(self.db, "NASDAQ", [])
        update_stock(self.db, "NASDAQ", ["A", "B"])
        self.assertTrue(all(r["in_current_listing"] for r in self.rows("stock")))

    def test_price_idempotent_isolation_and_bound_parameters(self):
        self.assertEqual(self.seed(ticker="A'B").inserted, 3)
        self.assertEqual(self.seed(ticker="A'B").unchanged, 3)
        self.seed(ticker="A'B", prices=(1,2,3), market="NYSE")
        self.assertEqual(len(self.rows("price")), 6)
        self.assertEqual(self.rows("price_revisions"), [])

    def test_invalid_batch_rolls_back_everything(self):
        bars = [PriceBar("A","2024-01-01",100), PriceBar("B","2024-01-02",float("nan"))]
        with self.assertRaises(ValueError): update_price(self.db,"NYSE",bars,source="fixture")
        self.assertEqual(self.rows("price"), [])
        self.assertEqual(self.rows("stock"), [])
        with self.assertRaises(ValueError): update_price(self.db,"NYSE",bars[:1]*2,source="fixture")
        self.assertEqual(self.rows("stock"), [])

    def test_source_mismatch_rolls_back(self):
        self.seed()
        with self.assertRaises(ValueError):
            update_price(self.db,"NASDAQ",[PriceBar("A","2024-01-01",90)],source="other")
        self.assertEqual(self.rows("price")[0]["adj_close"],100)
        with self.assertRaises(ValueError):
            update_price(self.db,"NASDAQ",[PriceBar("A","2024-01-04",90)],source="other")

    def test_revision_invalidates_and_rebuild_matches_clean(self):
        self.seed()
        update_price_detail(self.db,"NASDAQ")
        update_rs_rating_history(self.db,"NASDAQ",["2024-01-01","2024-01-02","2024-01-03"],lookback=2)
        self.assertEqual(self.seed(prices=(100,80,120)).revised,1)
        self.assertEqual(len(self.rows("price_detail")),1)
        self.assertEqual(self.rows("rs_rating_history"),[])
        self.assertEqual(len(self.rows("price_revisions")),1)
        update_price_detail(self.db,"NASDAQ")
        other = Database(Path(self.tmp.name)/"clean.sqlite3"); other.initialize()
        update_price(other,"NASDAQ",[PriceBar("A",f"2024-01-0{i}",p,0) for i,p in enumerate((100,80,120),1)],source="fixture")
        update_price_detail(other,"NASDAQ")
        with other.connection() as c:
            clean = [dict(r) for r in c.execute("SELECT * FROM price_detail ORDER BY date")]
        for actual, expected in zip(self.rows("price_detail"), clean):
            actual.pop("insert_time");expected.pop("insert_time")
            self.assertEqual(actual,expected)
        self.assertEqual(update_price_detail(self.db,"NASDAQ"),0)

    def test_incremental_and_full_indicators_match(self):
        self.seed(prices=(100,110))
        update_price_detail(self.db,"NASDAQ")
        self.seed()
        update_price_detail(self.db,"NASDAQ")
        rows=self.rows("price_detail")
        self.assertAlmostEqual(rows[1]["ema_5"], 100+20/6)
        self.assertAlmostEqual(rows[1]["macd"],20/13-20/27)
        self.assertAlmostEqual(rows[1]["signal"],(20/13-20/27)/5)
        self.assertAlmostEqual(rows[2]["ema_5"],(100+20/6)+(120-(100+20/6))/3)
        self.assertEqual(ema(10,0,9),8)
        self.assertEqual(ema(0,10,9),2)
        self.assertEqual(stage(1,1,1),1)
        self.assertIsNone(rows[2]["sma_50"])

    def test_sma_boundary(self):
        self.seed(prices=tuple(range(1,202)))
        update_price_detail(self.db,"NASDAQ")
        rows=self.rows("price_detail")
        self.assertIsNone(rows[198]["sma_200"])
        self.assertEqual(rows[199]["sma_200"],100.5)
        self.assertEqual(rows[200]["sma_200"],101.5)

    def test_rs_ties_and_singleton(self):
        self.seed("A",(100,110,120));self.seed("B",(100,110,120));self.seed("C",(100,120,150))
        result=update_rs_rating_history(self.db,"NASDAQ",["2024-01-01","2024-01-02","2024-01-03"],lookback=2)
        self.assertEqual(result["ranked"],3)
        self.assertEqual({r["ticker"]:r["rs_percentile"] for r in self.rows("rs_rating_history")},{"A":0,"B":0,"C":99})
        update_stock(self.db,"NASDAQ",["A"])
        update_rs_rating_history(self.db,"NASDAQ",["2024-01-01","2024-01-02","2024-01-03"],lookback=2)
        self.assertEqual(self.rows("rs_rating_history")[0]["rs_percentile"],0)

    def test_rs_missing_session_aborts_and_short_history_reported(self):
        self.seed()
        update_stock(self.db,"NASDAQ",["A","B"])
        update_price(self.db,"NASDAQ",[PriceBar("B","2024-01-03",10)],source="fixture")
        dates=["2024-01-01","2024-01-02","2024-01-03"]
        result=update_rs_rating_history(self.db,"NASDAQ",dates,lookback=2)
        self.assertEqual(result["excluded_short_history"],["B"])
        update_stock(self.db,"NASDAQ",["A","B","C"])
        update_price(self.db,"NASDAQ",[PriceBar("C","2024-01-01",10),PriceBar("C","2024-01-03",12)],source="fixture")
        with self.assertRaisesRegex(ValueError,"Incomplete"):
            update_rs_rating_history(self.db,"NASDAQ",dates,lookback=2)
        self.assertEqual(self.rows("rs_rating_history"),[])

    def provider(self):
        provider=Mock()
        provider.source="fixture"
        provider.list_tickers.return_value=["A","B"]
        provider.fetch_prices.side_effect=lambda t,s,e:[PriceBar(t,f"2024-01-0{i}",100+i) for i in range(1,4)]
        return provider

    def test_all_four_tables_and_repeat(self):
        provider=self.provider(); dates=["2024-01-01","2024-01-02","2024-01-03"]
        result=update_all(self.db,"NASDAQ",dates,provider=provider,lookback=2)
        self.assertEqual(result["prices"]["status"],"SUCCESS")
        self.assertEqual(result["details"],6)
        self.assertEqual(result["rs"]["ranked"],2)
        result=update_all(self.db,"NASDAQ",dates,provider=provider,lookback=2)
        self.assertEqual(result["prices"]["unchanged"],6)
        self.assertEqual(result["details"],0)

    def test_partial_run_is_recorded_and_retry_bounded(self):
        update_stock(self.db,"NASDAQ",["A","B"])
        provider=self.provider()
        def fetch(t,s,e):
            if t=="B":raise ConnectionError("offline")
            return [PriceBar(t,f"2024-01-0{i}",100) for i in range(1,4)]
        provider.fetch_prices.side_effect=fetch
        report=update_market_prices(self.db,"NASDAQ",["2024-01-01","2024-01-02","2024-01-03"],provider=provider,attempts=2,retry_delay=0)
        self.assertEqual(report.status,"PARTIAL")
        self.assertEqual(provider.fetch_prices.call_count,3)
        self.assertEqual(self.rows("update_runs")[0]["status"],"PARTIAL")
        self.assertEqual(self.rows("price_detail"),[])

    def test_dropped_history_and_missing_session_rejected(self):
        self.seed()
        provider=self.provider()
        provider.fetch_prices.side_effect=None
        provider.fetch_prices.return_value=[PriceBar("A","2024-01-03",100)]
        dates=["2024-01-01","2024-01-02","2024-01-03"]
        report=update_market_prices(self.db,"NASDAQ",dates,provider=provider,attempts=1)
        self.assertEqual(report.status,"FAILED")
        self.assertIn("dropped",report.failed["A"])
        with self.assertRaises(ValueError):update_market_prices(self.db,"NASDAQ",dates[1:],provider=provider)
        provider.fetch_prices.return_value=[PriceBar("A","2024-01-01",100),PriceBar("A","2024-01-03",100)]
        report=update_market_prices(self.db,"NASDAQ",dates,provider=provider,attempts=1)
        self.assertIn("dropped",report.failed["A"])
        self.assertEqual(report.missing_sessions["A"], {"count": 1, "sample": ["2024-01-02"]})
