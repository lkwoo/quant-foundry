import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from quantfoundry.cli import main
from quantfoundry.settings import load_settings
from quantfoundry.jobs.update_all import run_configured_update
from quantfoundry.stock import Database, PriceBar, update_price


class UpdateCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/"config").mkdir()
        self.config=self.root/"config/settings.toml"
        self.config.write_text('[storage]\npath="var/test.sqlite3"\n[update]\nlookback=2\nstart_date="2024-01-01"\n',encoding="utf-8")
        self.days=["2024-01-02","2024-01-03","2024-01-04"]

    def test_defaults_relative_to_project(self):
        settings=load_settings(self.config)
        self.assertEqual(settings.database,self.root/"var/test.sqlite3")
        self.assertEqual(len(settings.markets),4)

    def test_bare_cli_dispatch(self):
        with patch.object(sys,"argv",["quantfoundry","update-all"]), patch("quantfoundry.jobs.update_all.run_configured_update",return_value={"status":"SUCCESS"}) as run, contextlib.redirect_stdout(io.StringIO()):
            main()
        run.assert_called_once_with(config=None,database=None,market=None,sessions=None,lookback=None)

    def test_market_failure_does_not_stop_remaining_markets(self):
        def run(db, market, days, **kwargs):
            if market=="KOSDAQ":raise RuntimeError("fixture failure")
            return {"prices":{"status":"SUCCESS"},"details":0,"rs":{}}
        with patch("quantfoundry.jobs.update_all.completed_sessions",return_value=self.days) as calendar, patch("quantfoundry.jobs.update_all.refresh_stock"), patch("quantfoundry.jobs.update_all.run_daily",side_effect=run) as daily, contextlib.redirect_stderr(io.StringIO()):
            result=run_configured_update(config=self.config)
        self.assertEqual(result["status"],"PARTIAL")
        self.assertEqual(calendar.call_count,4)
        self.assertEqual(daily.call_count,4)
        self.assertEqual(result["markets"]["NYSE"]["status"],"SUCCESS")
        self.assertTrue((self.root/"var/test.sqlite3").exists())

    def test_explicit_sessions_need_single_market(self):
        with self.assertRaises(ValueError):run_configured_update(config=self.config,sessions=self.days)
        self.assertFalse((self.root/"var").exists())

    def test_earlier_existing_prices_extend_calendar(self):
        db=Database(self.root/"var/test.sqlite3");db.initialize()
        update_price(db,"NYSE",[PriceBar("A","2023-12-28",100)],source="fixture")
        with patch("quantfoundry.jobs.update_all.completed_sessions",return_value=self.days) as calendar, patch("quantfoundry.jobs.update_all.refresh_stock"), patch("quantfoundry.jobs.update_all.run_daily",return_value={"prices":{"status":"SUCCESS"}}), contextlib.redirect_stderr(io.StringIO()):
            run_configured_update(config=self.config,market="NYSE")
        calendar.assert_called_once_with("NYSE","2023-12-28")

    def test_short_calendar_does_not_create_db(self):
        with patch("quantfoundry.jobs.update_all.completed_sessions",return_value=self.days[:1]):
            with self.assertRaises(ValueError):run_configured_update(config=self.config)
        with Database(self.root/"var/test.sqlite3").connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM stock").fetchone()[0],0)
