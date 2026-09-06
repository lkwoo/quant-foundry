import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import Mock
from quantfoundry.stock import Database, PriceBar, update_price, update_stock, replace_stock_snapshots
from quantfoundry.jobs.update_all import refresh_stock
from quantfoundry.storage.database import APPLICATION_ID


class StockSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Database(Path(self.tmp.name)/"test.db");self.db.initialize()

    def stock(self):
        with self.db.connection() as c:return [tuple(r) for r in c.execute("SELECT * FROM stock ORDER BY market,ticker")]

    def test_full_replacement_preserves_old_prices(self):
        replace_stock_snapshots(self.db,{"NASDAQ":["OLD"],"NYSE":["OLD2"]})
        update_price(self.db,"NASDAQ",[PriceBar("OLD","2024-01-02",100)],source="fixture")
        result=replace_stock_snapshots(self.db,{"NASDAQ":["NEW"],"NYSE":["NEW2"]})
        self.assertEqual(result,{"NASDAQ":1,"NYSE":1})
        self.assertEqual([r[1] for r in self.stock()],["NEW","NEW2"])
        with self.db.connection() as c:
            self.assertEqual(c.execute("SELECT ticker FROM price").fetchone()[0],"OLD")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
        update_price(self.db,"NASDAQ",[PriceBar("OLD","2024-01-03",101)],source="fixture")
        self.assertEqual([r[1] for r in self.stock()],["NEW","NEW2"])

    def test_publish_failure_rolls_back_delete_and_every_market(self):
        replace_stock_snapshots(self.db,{"NASDAQ":["OLD"],"NYSE":["KEEP"]})
        before=self.stock()
        with self.db.transaction() as c:
            c.execute("CREATE TRIGGER fail_publish BEFORE INSERT ON stock WHEN NEW.ticker='BAD' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            replace_stock_snapshots(self.db,{"NASDAQ":["GOOD"],"NYSE":["BAD"]})
        self.assertEqual(self.stock(),before)
        with self.db.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM instruments WHERE ticker IN ('GOOD','BAD')").fetchone()[0],0)

    def test_fetch_or_empty_response_preserves_every_market(self):
        replace_stock_snapshots(self.db,{"NASDAQ":["OLD"],"NYSE":["KEEP"]})
        before=self.stock();provider=Mock()
        provider.list_tickers.side_effect=[["NEW"],ConnectionError("network failure")]
        with self.assertRaises(ConnectionError):refresh_stock(self.db,["NASDAQ","NYSE"],provider)
        self.assertEqual(before,self.stock())
        with self.assertRaises(ValueError):replace_stock_snapshots(self.db,{"NASDAQ":["NEW"],"NYSE":[]})
        self.assertEqual(before,self.stock())

    def test_single_market_does_not_delete_others(self):
        replace_stock_snapshots(self.db,{"NASDAQ":["A"],"NYSE":["B"]})
        update_stock(self.db,"NASDAQ",["C","C"])
        self.assertEqual([r[1] for r in self.stock()],["C","B"])

    def test_v1_migration_preserves_price_foreign_key(self):
        path=Path(self.tmp.name)/"v1.db"
        c=sqlite3.connect(path)
        c.executescript("""CREATE TABLE stock(market TEXT NOT NULL,ticker TEXT NOT NULL,update_time TEXT NOT NULL,in_current_listing INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(market,ticker));
        CREATE TABLE price(ticker TEXT,market TEXT,date TEXT,adj_close REAL,FOREIGN KEY(market,ticker) REFERENCES stock(market,ticker));
        INSERT INTO stock VALUES('NASDAQ','OLD','2024-01-01',0);
        INSERT INTO stock VALUES('NASDAQ','A','2024-01-01',1);
        INSERT INTO price VALUES('OLD','NASDAQ','2024-01-02',100);
        PRAGMA user_version=1;""")
        c.execute(f"PRAGMA application_id={APPLICATION_ID}");c.commit();c.close()
        db=Database(path);db.initialize();db.initialize()
        with db.connection() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT ticker FROM stock")},{"A","OLD"})
            self.assertEqual(c.execute("SELECT ticker FROM price").fetchone()[0],"OLD")
            self.assertEqual(c.execute("PRAGMA foreign_key_list(price)").fetchone()[2],"instruments")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
