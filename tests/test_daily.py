import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from quantfoundry.cli import main
from quantfoundry.jobs.daily import run_daily
from quantfoundry.stock import Database, PriceBar


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/"test.sqlite3")
        self.db.initialize()
        self.sessions=["2024-01-02","2024-01-03","2024-01-04"]

    def test_daily_updates_four_tables(self):
        provider=Mock(source="fixture")
        provider.list_tickers.return_value=["A"]
        provider.fetch_prices.return_value=[PriceBar("A",d,100+i) for i,d in enumerate(self.sessions)]
        result=run_daily(self.db,"nasdaq",self.sessions,lookback=2,provider=provider)
        self.assertEqual(result["prices"]["status"],"SUCCESS")
        self.assertEqual(result["details"],3)
        self.assertEqual(result["rs"]["ranked"],1)

    def test_invalid_input_does_not_fetch_or_write(self):
        provider=Mock()
        for days, lookback in [(self.sessions,0),(self.sessions,252),(list(reversed(self.sessions)),2)]:
            with self.assertRaises(ValueError):run_daily(self.db,"NASDAQ",days,lookback=lookback,provider=provider)
        provider.list_tickers.assert_not_called()
        with self.db.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM stock").fetchone()[0],0)

    def test_cli_dispatch_and_partial_exit(self):
        session_file=Path(self.temp.name)/"sessions.json"
        session_file.write_text(json.dumps(self.sessions),encoding="utf-8")
        args=["quantfoundry","daily","--db",str(self.db.path),"--market","NASDAQ","--sessions",str(session_file),"--lookback","2"]
        with patch.object(sys,"argv",args), patch("quantfoundry.jobs.daily.run_daily") as job, contextlib.redirect_stdout(io.StringIO()):
            job.return_value={"prices":{"status":"SUCCESS"}}
            main()
            self.assertEqual(job.call_args.args[1:],("NASDAQ",self.sessions))
            self.assertEqual(job.call_args.kwargs,{"lookback":2})
            job.return_value={"prices":{"status":"PARTIAL"}}
            with self.assertRaises(SystemExit) as raised:main()
            self.assertEqual(raised.exception.code,1)

    def test_no_command_shows_help(self):
        output=io.StringIO()
        with patch.object(sys,"argv",["quantfoundry"]), contextlib.redirect_stdout(output):
            main()
        self.assertIn("daily",output.getvalue())
