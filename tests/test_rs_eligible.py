import tempfile
import unittest
from pathlib import Path
from quantfoundry.stock import Database,PriceBar,update_stock,update_price,update_rs_rating_history


class EligibleRSTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Database(Path(self.tmp.name)/"test.db");self.db.initialize()
        self.days=["2024-01-02","2024-01-03","2024-01-04"]
        update_stock(self.db,"NYSE",["A","B","SHORT","GAP","MISSING"])
        bars=[PriceBar(t,d,p) for t,end in [("A",120),("B",150)] for d,p in zip(self.days,[100,110,end])]
        bars += [PriceBar("SHORT",self.days[-1],100),PriceBar("GAP",self.days[0],100),PriceBar("GAP",self.days[-1],110)]
        update_price(self.db,"NYSE",bars,source="fixture")

    def test_explicit_subset_records_universe_and_reasons(self):
        result=update_rs_rating_history(self.db,"NYSE",self.days,lookback=2,missing_policy="exclude")
        self.assertEqual(result["ranked"],2)
        self.assertEqual(result["excluded"],{"SHORT":"insufficient_history","GAP":"incomplete_or_off_calendar_window","MISSING":"missing_as_of_price"})
        with self.db.connection() as c:
            rows=c.execute("SELECT ticker,rs_percentile,universe_size,universe_json,calculation_version FROM rs_rating_history ORDER BY ticker").fetchall()
        self.assertEqual([r[1] for r in rows],[0,99])
        self.assertTrue(all(r[2]==2 and r[3]=='["A", "B"]' and r[4]=='rs-v2-eligible-session-window' for r in rows))

    def test_strict_policy_remains_default(self):
        with self.assertRaises(ValueError):update_rs_rating_history(self.db,"NYSE",self.days,lookback=2)
        with self.db.connection() as c:self.assertEqual(c.execute("SELECT count(*) FROM rs_rating_history").fetchone()[0],0)

    def test_no_valid_instruments_produces_no_fake_ranks(self):
        update_stock(self.db,"NYSE",["SHORT","MISSING"])
        result=update_rs_rating_history(self.db,"NYSE",self.days,lookback=2,missing_policy="exclude")
        self.assertEqual(result["status"],"INSUFFICIENT_DATA")
        self.assertEqual(result["ranked"],0)
        with self.db.connection() as c:self.assertEqual(c.execute("SELECT count(*) FROM rs_rating_history").fetchone()[0],0)
