import contextlib
from datetime import date, timedelta
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantfoundry.cli import main
from quantfoundry.indicators.daily import VERSION
from quantfoundry.jobs.ma import query_ma
from quantfoundry.storage.database import Database
from quantfoundry.storage.updates import PriceBar, update_price, update_stock
from quantfoundry.strategies.ma import classify, rank_returns


class MARulesTests(unittest.TestCase):
    def test_six_permutations_and_tie_boundary(self):
        permutations = [(3, 2, 1), (2, 3, 1), (1, 3, 2), (1, 2, 3), (2, 1, 3), (3, 1, 2)]
        for number, values in enumerate(permutations, 1):
            row = dict(zip(('ema_5', 'ema_20', 'ema_40'), values), stage=number, calculation_version=VERSION)
            result, error = classify(row, 40)
            self.assertIsNone(error)
            self.assertEqual(result['stage'], number)
            self.assertFalse(result['boundary'])
        row.update(ema_5=1, ema_20=1, ema_40=1, stage=1)
        self.assertTrue(classify(row, 40)[0]['boundary'])
        self.assertIsNone(classify(row, 39)[0])
        row['ema_5'] = float('nan')
        self.assertIsNone(classify(row, 40)[0])

    def test_percentile_ties_and_singleton(self):
        self.assertEqual(rank_returns({'A': -0.1, 'B': 0.2, 'C': 0.2, 'D': 0.4}),
                         {'A': 0, 'B': 33, 'C': 33, 'D': 99})
        self.assertEqual(rank_returns({'A': 1}), {'A': 0})
        self.assertEqual(rank_returns({}), {})


class MAQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'ma.sqlite3')
        self.db.initialize()
        days, cursor = [], date(2024, 1, 2)
        while len(days) < 253:
            if cursor.weekday() < 5:
                days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        self.days = days

    def seed(self, ticker, *, market='NYSE', number=1, gain=0.1, days=None):
        days = days or self.days
        selected = list(dict.fromkeys([days[0], *days[-40:]]))
        update_price(self.db, market, [PriceBar(ticker, day, 100 if day != days[-1] else 100*(1+gain))
                                      for day in selected], source='fixture')
        values = {1: (110, 105, 100), 4: (100, 105, 110), 6: (110, 100, 105)}[number]
        with self.db.transaction() as conn:
            conn.execute('''INSERT INTO price_detail(ticker,market,date,adj_close,stage,ema_5,ema_20,ema_40,calculation_version)
                VALUES(?,?,?,?,?,?,?,?,?)''', (ticker, market, days[-1], 100*(1+gain), number, *values, VERSION))

    def query(self, target):
        return query_ma(target, database=self.db.path, sessions=self.days)

    def test_two_top_tens_and_full_market_rs_denominator(self):
        tickers = [f'A{i:02}' for i in range(12)] + [f'B{i:02}' for i in range(12)] + ['STRONGEST']
        update_stock(self.db, 'NYSE', tickers)
        for i, ticker in enumerate(tickers):
            self.seed(ticker, number=1 if ticker.startswith('A') else 6 if ticker.startswith('B') else 4, gain=i/10)
        before = self.db.path.read_bytes()
        output, ok = self.query('nyse')
        self.assertTrue(ok)
        self.assertIn(f'판단 기준일: {self.days[-1]}', output)
        self.assertIn('RS 비교 대상: 25/25', output)
        sections = output.split('Stage 6 Top 10')
        self.assertLess(sections[0].index('A11'), sections[0].index('A10'))
        self.assertNotIn('A00', output)
        self.assertNotIn('B00', output)
        self.assertEqual(output.count('표시 10개 / 순위 가능 12개'), 2)
        # The strongest Stage 4 is in the denominator but never a Top 10 candidate.
        self.assertNotIn('STRONGEST', output)
        self.assertIn('B11             95', output)
        self.assertEqual(before, self.db.path.read_bytes())

    def test_korean_suffix_omission_case_and_ambiguity(self):
        self.seed('005930.KS', market='KOSPI')
        self.assertEqual(self.query('005930'), self.query('005930.ks'))
        self.assertIn('현재 Stage: 1', self.query('005930')[0])
        self.seed('005930.KQ', market='KOSDAQ', number=6)
        with self.assertRaisesRegex(ValueError, '여러 시장'):
            self.query('005930')
        self.assertIn('현재 Stage: 6', self.query('kosdaq:005930')[0])
        self.seed('35320K.KS', market='KOSPI')
        self.assertEqual(self.query('35320k'), self.query('35320k.ks'))

    def test_same_returns_sort_by_ticker_and_missing_details_fail(self):
        update_stock(self.db, 'NYSE', ['Z', 'A'])
        self.seed('Z')
        self.seed('A')
        output, ok = self.query('NYSE')
        self.assertTrue(ok)
        rows = [line.split()[1] for line in output.splitlines() if line.split() and line.split()[0].isdigit()]
        self.assertEqual(rows, ['A', 'Z'])
        with self.db.transaction() as conn:
            conn.execute('DELETE FROM price_detail')
        output, ok = self.query('NYSE')
        self.assertFalse(ok)
        self.assertIn('최신일 price_detail 없음 2개', output)

    def test_stale_ticker_displays_own_date_and_no_old_detail_fallback(self):
        self.seed('OLD', days=self.days[:-1])
        self.seed('NEW')
        output, ok = self.query('old')
        self.assertTrue(ok)
        self.assertIn(f'판단 기준일: {self.days[-2]}', output)
        self.assertIn('오래되었습니다', output)
        update_price(self.db, 'NYSE', [PriceBar('OLD', self.days[-1], 115)], source='fixture')
        output, ok = self.query('OLD')
        self.assertFalse(ok)
        self.assertIn(f'판단 기준일: {self.days[-1]}', output)
        self.assertIn('price_detail 없음', output)
        self.assertNotIn('현재 Stage:', output)

    def test_missing_endpoint_not_filled_and_old_rs_not_used(self):
        update_stock(self.db, 'NYSE', ['A', 'SHORT', 'STALE'])
        self.seed('A')
        self.seed('SHORT', days=self.days[1:])
        self.seed('STALE', days=self.days[:-1])
        output, ok = self.query('NYSE')
        self.assertTrue(ok)
        self.assertIn('RS 비교 대상: 1/3', output)
        self.assertIn('252거래일 전 가격 없음 1개', output)
        self.assertIn('최신 가격 없음 1개', output)
        self.assertIn('표시 1개 / 순위 가능 1개', output)

    def test_detail_version_and_boundaries_excluded(self):
        update_stock(self.db, 'NYSE', ['A', 'TIE'])
        self.seed('A')
        self.seed('TIE')
        with self.db.transaction() as conn:
            conn.execute("UPDATE price_detail SET calculation_version='old' WHERE ticker='A'")
            conn.execute("UPDATE price_detail SET ema_5=100,ema_20=100,ema_40=100 WHERE ticker='TIE'")
        output, _ = self.query('NYSE')
        self.assertIn('EMA 동률 경계 1개', output)
        self.assertIn('지표 계산 버전 불일치 1개', output)
        self.assertIn('표시 0개', output)
        self.assertIn('동률 경계', self.query('TIE')[0])

    def test_cli_valid_missing_target_and_empty_db(self):
        self.seed('005930.KS', market='KOSPI')
        with patch('sys.argv', ['quantfoundry', '-q', 'ma', '005930', '--db', str(self.db.path)]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        self.assertIn('KOSPI:005930.KS', output.getvalue())
        with patch('sys.argv', ['quantfoundry', '-q', 'ma']), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as exc:
            main()
        self.assertEqual(exc.exception.code, 2)
        output, ok = self.query('NYSE')
        self.assertFalse(ok)
        self.assertIn('판단 기준일: 없음', output)


if __name__ == '__main__':
    unittest.main()
