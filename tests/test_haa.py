import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from quantfoundry.cli import main
from quantfoundry.jobs.haa import query_haa, update_haa
from quantfoundry.storage.database import Database
from quantfoundry.storage.updates import PriceBar, update_price, update_stock, update_rs_rating_history
from quantfoundry.strategies.haa import TICKERS, MARKETS, allocate, evaluate


# Explicit month ends, including weekend/holiday adjustments (May 2024, August 2024).
ENDS = ['2024-01-31', '2024-02-29', '2024-03-28', '2024-04-30',
        '2024-05-31', '2024-06-28', '2024-07-31', '2024-08-30',
        '2024-09-30', '2024-10-31', '2024-11-29', '2024-12-31',
        '2025-01-31', '2025-02-28']


class HaaRuleTests(unittest.TestCase):
    def test_zero_canary_and_negative_defensives_still_select_best(self):
        scores = dict.fromkeys(TICKERS, 1.0)
        scores.update(TIP=0, BIL=-0.02, IEF=-0.01)
        self.assertEqual(allocate(scores)['weights'], {'IEF': 1.0})
        scores.update(TIP=-0.1, BIL=0, IEF=0)
        self.assertEqual(allocate(scores)['weights'], {'BIL': 1.0})

    def test_top_four_and_nonpositive_substitution_aggregate_ief(self):
        scores = dict.fromkeys(TICKERS, -0.4)
        scores.update(TIP=0.01, SPY=0.3, IEF=0.2, IWM=0, VEA=-0.1, BIL=0.1)
        self.assertEqual(allocate(scores)['weights'], {'SPY': 0.25, 'IEF': 0.75})

    def test_equal_positive_scores_use_universe_order(self):
        result = allocate(dict.fromkeys(TICKERS, 0.1))
        self.assertEqual(result['weights'], dict.fromkeys(('SPY', 'IWM', 'VEA', 'VWO'), 0.25))

    def test_known_returns_and_month_end_anchors(self):
        prices = {t: dict.fromkeys(ENDS, 100.0) for t in TICKERS}
        prices['SPY'].update({'2025-02-28': 120, '2025-01-31': 100,
                              '2024-11-29': 80, '2024-08-30': 60, '2024-02-29': 40})
        signal = evaluate(prices, '2025-02-28', ENDS)
        self.assertTrue(signal['month_end'])
        # 20%, 50%, 100%, 200% => 92.5%; not a weighted 13612 score.
        self.assertAlmostEqual(signal['details']['SPY']['momentum'], 0.925)
        self.assertEqual(signal['anchors'][6], '2024-08-30')

    def test_intramonth_preview_no_future_prices_or_fallback(self):
        prices = {t: dict.fromkeys(ENDS, 100.0) for t in TICKERS}
        for series in prices.values():
            series['2025-02-14'] = 110
            series['2025-02-28'] = 9999
        sessions = sorted([*ENDS, '2025-02-14'])
        signal = evaluate(prices, '2025-02-14', sessions)
        self.assertFalse(signal['month_end'])
        self.assertAlmostEqual(signal['details']['TIP']['momentum'], 0.1)
        del prices['TIP']['2024-08-30']
        prices['TIP']['2024-08-29'] = 100
        with self.assertRaisesRegex(ValueError, 'TIP: 2024-08-30'):
            evaluate(prices, '2025-02-14', sessions)


class HaaQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'test.sqlite3')
        self.db.initialize()
        for ticker in TICKERS:
            update_price(self.db, MARKETS[ticker], [PriceBar(ticker, d, 100 + i)
                         for i, d in enumerate(ENDS[:-1])], source='fixture')

    def test_query_uses_etfs_latest_not_unrelated_stock_and_is_readonly(self):
        update_price(self.db, 'NASDAQ', [PriceBar('UNRELATED', '2025-02-28', 100)], source='fixture')
        before = self.db.path.read_bytes()
        output, ok = query_haa(database=self.db.path, sessions=ENDS)
        self.assertTrue(ok)
        self.assertIn('데이터 최신 기준일: 2025-01-31', output)
        self.assertIn('월말 확정 신호', output)
        self.assertEqual(before, self.db.path.read_bytes())
        with self.db.connection(readonly=True) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM price')

    def test_one_newer_etf_does_not_silently_rewind(self):
        update_price(self.db, MARKETS['TIP'], [PriceBar('TIP', '2025-02-28', 120)], source='fixture')
        output, ok = query_haa(database=self.db.path, sessions=ENDS)
        self.assertFalse(ok)
        self.assertIn('데이터 최신 기준일: 2025-02-28', output)
        self.assertIn('SPY: 2025-02-28', output)
        self.assertNotIn('목표 비중:', output)

    def test_preview_also_shows_last_month_holdings(self):
        for ticker in TICKERS:
            update_price(self.db, MARKETS[ticker], [PriceBar(ticker, '2025-02-14', 120)], source='fixture')
        output, ok = query_haa(database=self.db.path, sessions=sorted([*ENDS, '2025-02-14']))
        self.assertTrue(ok)
        self.assertIn('최신 잠정 신호: 2025-02-14', output)
        self.assertIn('최근 월말 보유 목표 기준일: 2025-01-31', output)

    def test_cli_and_missing_data_exit(self):
        with patch('sys.argv', ['quantfoundry', '-q', 'haa', '--db', str(self.db.path)]), \
                patch('quantfoundry.jobs.haa.calendar_sessions', return_value=ENDS), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        self.assertIn('목표 비중:', output.getvalue())
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM price WHERE ticker='TIP'")
        with patch('sys.argv', ['quantfoundry', '-q', 'haa', '--db', str(self.db.path)]), \
                patch('quantfoundry.jobs.haa.calendar_sessions', return_value=ENDS), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main()
        self.assertEqual(exc.exception.code, 1)

    def test_etf_refresh_does_not_replace_stock_or_download_unrelated(self):
        update_stock(self.db, 'NASDAQ', ['EXISTING'])
        from quantfoundry.settings import Settings
        from quantfoundry.data.downloads import DownloadOptions
        class Provider:
            source = 'fixture'
            def fetch_prices(self, ticker, start, end):
                return [PriceBar(ticker, d, 100 + i) for i, d in enumerate(ENDS[:-1])]
        settings = Settings(self.db.path, ('NASDAQ',), '2024-01-01', 252, DownloadOptions(workers=2))
        with patch('quantfoundry.jobs.haa.load_settings', return_value=settings), \
                patch('quantfoundry.providers.market.YahooProvider', return_value=Provider()), \
                patch('quantfoundry.data.calendar.completed_sessions', return_value=ENDS[:-1]), \
                contextlib.redirect_stderr(io.StringIO()):
            result = update_haa(database=self.db.path)
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(sum(len(r['succeeded']) for r in result['markets'].values()), 10)
        with self.db.connection() as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT market,ticker FROM stock')],
                             [('NASDAQ', 'EXISTING')])

    def test_unlisted_etf_preserves_stock_rs_but_historical_member_invalidates(self):
        days = ['2024-01-30', '2024-01-31']
        update_stock(self.db, 'NASDAQ', ['EXISTING'])
        update_price(self.db, 'NASDAQ', [PriceBar('EXISTING', d, 100) for d in days], source='fixture')
        update_rs_rating_history(self.db, 'NASDAQ', days, lookback=1)
        update_price(self.db, 'NASDAQ', [PriceBar('IEF', days[0], 99)], source='fixture')
        with self.db.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM rs_rating_history').fetchone()[0], 1)
        update_stock(self.db, 'NASDAQ', ['NEW'])
        update_price(self.db, 'NASDAQ', [PriceBar('EXISTING', days[0], 98)], source='fixture')
        with self.db.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM rs_rating_history').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
