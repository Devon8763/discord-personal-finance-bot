"""Chart statistics and HTML use synthetic SQLite and OAuth identities only."""
import hashlib
import json
import re
import sqlite3
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import db
import life_ledger_service as service
import spending as sp
from tests import test_expense_comparison as comparison_tests
from tests.test_web_auth import _WebLedgerTestFixture


class ComparisonChartCoreTests(unittest.TestCase):
    setUp = comparison_tests.ExpenseComparisonCoreTests.setUp
    tearDown = comparison_tests.ExpenseComparisonCoreTests.tearDown
    add = comparison_tests.ExpenseComparisonCoreTests.add
    compare = comparison_tests.ExpenseComparisonCoreTests.compare
    snapshot = comparison_tests.ExpenseComparisonCoreTests.snapshot

    def series(self, period):
        self.assertIn('daily', period, 'daily statistics missing')
        return period['daily']

    def test_daily_gaps_keep_exact_amount_and_sum_to_total(self):
        self.add('2026-09-01', '.29'); self.add('2026-09-01', '.01')
        self.add('2026-09-03', '.3', '交通')
        result = self.compare('2026-09-01', '2026-09-04', '2026-08-01', '2026-08-01')
        points = self.series(result['a'])
        self.assertEqual(points, [dict(day=1, date='2026-09-01', cents=30),
                                 dict(day=2, date='2026-09-02', cents=0),
                                 dict(day=3, date='2026-09-03', cents=30),
                                 dict(day=4, date='2026-09-04', cents=0)])
        self.assertEqual(self.series(result['b']), [dict(day=1, date='2026-08-01', cents=0)])
        self.assertEqual(sum(p['cents'] for p in points), result['a']['total_cents'])
        self.assertEqual(sum(row['a_cents'] for row in result['categories']), 60)

    def test_daily_is_not_cumulative_for_700_60_zero(self):
        self.add('2026-09-01', 700); self.add('2026-09-02', 60)
        result = self.compare('2026-09-01', '2026-09-03', '2026-08-01', '2026-08-01')
        self.assertEqual([p['cents'] for p in self.series(result['a'])], [70000, 6000, 0])
        self.assertEqual(result['a']['total_cents'], 76000)

    def test_calendar_boundaries_overlap_and_unequal_lengths_stop_at_own_end(self):
        self.add('2024-02-29', '.29'); self.add('2025-12-31', '.1')
        self.add('2026-01-01', '.2'); self.add('2026-09-02', '.3')
        cases = [
            ('2024-02-28', '2024-03-01', '2025-12-31', '2026-01-01',
             ['2024-02-28', '2024-02-29', '2024-03-01'], [0, 29, 0], [10, 20]),
            ('2026-09-01', '2026-09-03', '2026-09-02', '2026-09-02',
             ['2026-09-01', '2026-09-02', '2026-09-03'], [0, 30, 0], [30]),
        ]
        for a_start, a_end, b_start, b_end, dates, a_cents, b_cents in cases:
            with self.subTest(a_start=a_start):
                result = self.compare(a_start, a_end, b_start, b_end)
                a, b = self.series(result['a']), self.series(result['b'])
                self.assertEqual([p['date'] for p in a], dates)
                self.assertEqual([p['day'] for p in a], [1, 2, 3])
                self.assertEqual([p['cents'] for p in a], a_cents)
                self.assertEqual([p['cents'] for p in b], b_cents)

    def test_future_days_have_no_points_and_zero_started_days_do(self):
        self.add('2026-09-24', '.29')
        with sp.transaction() as conn:
            conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('42','2026-09-25',999,'餐飲','future')")
        result = self.compare('2026-09-23', '2026-12-31', '2026-09-25', '2027-01-01')
        self.assertEqual(self.series(result['a']), [dict(day=1, date='2026-09-23', cents=0),
                                                  dict(day=2, date='2026-09-24', cents=29)])
        self.assertEqual(self.series(result['b']), [])
        self.assertIsNone(result['b']['total_cents'])
        both = self.compare('2026-10-01', '2026-12-31', '2027-01-01', '2027-06-30')
        self.assertEqual(self.series(both['a']), []); self.assertEqual(self.series(both['b']), [])

    def test_same_filtered_four_sources_feed_totals_categories_and_series(self):
        for i, origin in enumerate(('manual', '固定', '訂閱', '分期'), 1):
            self.add(f'2026-09-0{i}', '.29', '居住', 'Coffee %_', source=origin)
        self.add('2026-09-01', 9, '交通', 'Coffee %_')
        self.add('2026-09-01', 9, '居住', 'not matching')
        self.add('2026-09-01', 9, '居住', 'Coffee %_', user='99')
        self.add('2026-09-01', 9, '居住', 'Coffee %_', voided=1)
        for kind in ('income', 'investment', 'transfer'):
            self.add('2026-09-01', 9, '居住', 'Coffee %_', kind=kind)
        self.add('2026-08-01', '.3', '居住', 'coffee %_')
        sp.set_category('42', '居住', False)
        result = self.compare('2026-09-01', '2026-09-04', '2026-08-01', '2026-08-01', category='居住', keyword='COFFEE %_')
        self.assertEqual([p['cents'] for p in self.series(result['a'])], [29, 29, 29, 29])
        self.assertEqual(result['a']['record_count'], 4)
        self.assertEqual(result['categories'][0]['a_cents'], 116)
        self.assertEqual(sum(p['cents'] for p in self.series(result['b'])), 30)
        self.assertIn('a_percent', result['categories'][0], 'core share missing')
        self.assertEqual(result['categories'][0]['a_percent'], Decimal(100))

    def test_category_share_uses_own_period_total_and_zero_baseline_is_absent(self):
        self.add('2026-09-01', '.29'); self.add('2026-09-01', '.71', '交通')
        self.add('2026-08-01', '.58')
        result = self.compare()
        food = next(row for row in result['categories'] if row['category'] == '餐飲')
        self.assertIn('a_percent', food, 'own-period category share missing')
        self.assertEqual((food['a_percent'], food['b_percent']), (Decimal(29), Decimal(100)))
        zero = self.compare('2026-09-01', '2026-09-01', '2026-07-01', '2026-07-01')
        self.assertIsNone(zero['categories'][0]['b_percent'])

    def test_full_daily_result_reuses_single_read_snapshot(self):
        self.add('2026-09-01', '.29'); key = self.add('2026-08-01', '.3')
        with sp.transaction() as conn:
            conn.executemany("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('42','2026-09-02',1,'餐飲','bulk')", [()] * 10001)
        with closing(sqlite3.connect(db.DB_NAME)) as conn: conn.execute('PRAGMA journal_mode=WAL')
        original = sp._list_expenses_between
        calls = []
        def query(*args, **kwargs):
            rows = original(*args, **kwargs); calls.append(kwargs['conn'])
            if len(calls) == 1:
                with closing(sqlite3.connect(db.DB_NAME)) as writer:
                    writer.execute('UPDATE expenses SET cents=999 WHERE id=?', (key,)); writer.commit()
            return rows
        with patch.object(sp, '_list_expenses_between', side_effect=query): result = self.compare()
        self.assertEqual(len(calls), 2); self.assertIs(calls[0], calls[1])
        self.assertEqual(sum(p['cents'] for p in self.series(result['a'])), 10030)
        self.assertEqual(sum(p['cents'] for p in self.series(result['b'])), 30)
        self.assertEqual(result['a']['record_count'], 10002)
        self.assertEqual(result['categories'][0]['b_cents'], 30)

    def test_edit_and_soft_void_refresh_series_and_shares(self):
        key = self.add('2026-09-01', '.29')
        self.assertEqual(sum(p['cents'] for p in self.series(self.compare()['a'])), 29)
        service.update_expense('42', key, '.3', '交通', 'edited', '2026-08-31', expected_revision=0)
        result = self.compare()
        self.assertEqual(sum(p['cents'] for p in self.series(result['a'])), 0)
        self.assertEqual(self.series(result['b'])[-1]['cents'], 30)
        service.void_expense('42', key, expected_revision=1)
        self.assertEqual(sum(p['cents'] for p in self.series(self.compare()['b'])), 0)

    def test_chart_statistics_are_read_only_and_share_today_once(self):
        sp.add_recurring('42', '固定', 'not synced', 1, '居住', '2026-09')
        before = self.snapshot(); self.today_mock.reset_mock()
        with patch.object(sp, 'sync_recurring', side_effect=AssertionError('no sync')):
            result = self.compare()
        self.series(result['a'])
        self.assertEqual(self.snapshot(), before); self.assertEqual(self.today_mock.call_count, 1)


class ComparisonChartWebTests(_WebLedgerTestFixture, unittest.TestCase):
    query = comparison_tests.ExpenseComparisonWebTests.query
    add = comparison_tests.ExpenseComparisonWebTests.add
    snapshot = comparison_tests.ExpenseComparisonWebTests.snapshot

    def payload(self, page):
        self.assertEqual(page.status_code, 200)
        self.assertIn('chart_data', page.context, 'minimal chart payload missing')
        return page.context['chart_data']

    def test_payload_preserves_exact_amounts_without_raw_records_or_identity(self):
        self._login(); self.add('2026-09-01', '.29', note='private note')
        self.add('2026-08-01', '.1', '交通')
        page = self.query()
        data = self.payload(page)
        encoded = json.dumps(data)
        for secret in ('private note', self.user_id, 'oauth', 'payment', 'expense_id', 'record_count', 'category_options'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(set(data), {'periods', 'categories', 'single_category', 'keyword_filtered'})
        self.assertEqual(page.context['comparison']['a']['total_cents'], 29)
        self.assertEqual(data['periods'][0]['daily'][0]['cents'], '29')
        self.assertEqual(data['periods'][0]['daily'][0]['amount'], '0.29')
        self.assertEqual(data['periods'][1]['daily'][0]['amount'], '0.1')
        self.assertNotIn('total_cents', data['periods'][0])
        self.assertNotIn('change_percent', data)

    def test_malicious_history_category_is_safe_json_and_never_executable_html(self):
        self._login(); key = self.add('2026-09-01', '.29')
        evil = '</script><img src=x onerror=alert(1)>"&'
        with sp.transaction() as conn: conn.execute('UPDATE expenses SET category=? WHERE id=?', (evil, key))
        page = self.query()
        data = self.payload(page)
        self.assertEqual(data['categories'][0]['name'], evil)
        match = re.search(r'<script id="comparison-chart-data" type="application/json">(.*?)</script>', page.text, re.S)
        self.assertIsNotNone(match, 'safe JSON container missing')
        self.assertNotIn('<', match[1]); self.assertNotIn('<img src=x', page.text)
        self.assertEqual(json.loads(match[1]), data)

    def test_assets_are_local_compare_only_and_text_fallback_is_retained(self):
        self._login(); self.add('2026-09-01', 1)
        page = self.query()
        self.payload(page)
        for title in ('每日支出比較', '分類支出比較', '分類支出占比'):
            self.assertIn(title, page.text)
        self.assertIn('以各期間開始日對齊，並非相同日曆日期', page.text)
        self.assertIn('<noscript>', page.text)
        self.assertIn('圖表未載入', page.text)
        self.assertIn('role="img"', page.text); self.assertIn('aria-describedby=', page.text)
        self.assertIn('<caption>分類比較', page.text); self.assertIn('<progress ', page.text)
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', page.text)
        self.assertEqual(len(scripts), 2)
        for url in scripts:
            self.assertTrue(url.startswith('http://testserver/static/'), url)
            self.assertEqual(self.client.get(url).status_code, 200)
        for path in ('/', '/search', '/settings', '/calendar'):
            other = self.client.get(path)
            self.assertNotIn('comparison_charts.js', other.text)
            self.assertNotIn('chart.umd.min.js', other.text)

    def test_single_category_keyword_zero_and_future_have_explicit_empty_states(self):
        self._login(); self.add('2026-09-01', 1, note='Coffee')
        filtered = self.query(category='餐飲', keyword='Coffee')
        data = self.payload(filtered)
        self.assertTrue(data['single_category']); self.assertTrue(data['keyword_filtered'])
        self.assertIn('目前只比較單一分類，占比固定為 100%', filtered.text)
        self.assertIn('占比僅針對符合關鍵字的消費', filtered.text)
        self.assertNotIn('id="comparison-pie-a"', filtered.text)
        empty = self.query(keyword='nothing matches')
        data = self.payload(empty)
        self.assertEqual(empty.context['comparison']['a']['total_cents'], 0)
        self.assertIn('沒有符合條件的消費', empty.text)
        self.assertNotIn('id="comparison-pie-a"', empty.text)
        future = self.query(a_start='2026-10-01', a_end='2026-12-31', b_start='2027-01-01', b_end='2027-06-30')
        data = self.payload(future)
        self.assertIsNone(future.context['comparison']['a']['total_cents'])
        self.assertEqual(data['periods'][0]['daily'], [])
        self.assertNotIn('<canvas', future.text)

    def test_session_invalid_input_and_read_only_contract_remains(self):
        with patch.object(sp, 'get_conn', side_effect=AssertionError('unauthorized read')):
            self.assertEqual(self.client.get('/compare').status_code, 403)
        self._login(); self.add('2026-09-01', '.29')
        self.add('2026-09-01', 999, user='other')
        before = self.snapshot()
        page = self.query(user_id='other', as_of='2099-01-01', limit=1)
        self.assertEqual(page.context['comparison']['a']['total_cents'], 29)
        self.assertEqual(self.payload(page)['periods'][0]['daily'][0]['cents'], '29')
        self.assertEqual(self.snapshot(), before)
        invalid = self.query(a_start='bad', keyword='draft')
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('draft', invalid.text); self.assertNotIn('comparison-chart-data', invalid.text)

    def test_chart_canvases_do_not_duplicate_existing_form_ids(self):
        self._login(); self.add('2026-09-01', '.29')
        page = self.query()
        ids = re.findall(r'\bid="([^"]+)"', page.text)
        self.assertEqual(len(ids), len(set(ids)), 'chart target collides with existing form control')

    def test_chart_boxes_stay_hidden_until_script_can_draw(self):
        self._login(); self.add('2026-09-01', '.29')
        page = self.query()
        boxes = re.findall(r'<div class="comparison-chart-box[^"]*"([^>]*)><canvas', page.text)
        self.assertEqual(len(boxes), page.text.count('<canvas'))
        self.assertTrue(boxes)
        self.assertTrue(all('hidden' in attrs.split() for attrs in boxes), 'no-JS fallback leaves empty chart boxes')

    def test_daily_table_aligns_periods_without_future_padding_and_keeps_no_js_rows(self):
        self._login(); self.add('2026-09-01', 700); self.add('2026-09-02', 60)
        self.add('2026-08-02', '.29')
        page = self.query(a_end='2026-09-03', b_end='2026-08-04')
        self.assertEqual(page.status_code, 200)
        rows = page.context['comparison']['daily_rows']
        self.assertEqual([(r['day'], r['a']['cents'] if r['a'] else None,
                           r['b']['cents'] if r['b'] else None, r['nonzero']) for r in rows],
                         [(1, 70000, 0, True), (2, 6000, 29, True),
                          (3, 0, 0, False), (4, None, 0, False)])
        details = re.search(r'<details id="comparison-daily-data"([^>]*)>(.*?)</details>', page.text, re.S)
        self.assertIsNotNone(details); self.assertNotIn('open', details[1])
        for text in ('查看每日數據', '顯示所有日期', '日序', 'A 日期', 'A 支出', 'B 日期', 'B 支出',
                     '2026-09-01 ～ 2026-09-03', '2026-08-01 ～ 2026-08-04',
                     '— 表示尚未發生或不在該期間內。', '09/01', '08/04'):
            self.assertIn(text, details[2])
        tags = re.findall(r'<tr data-daily-row[^>]*>', details[2])
        self.assertEqual(len(tags), 4)
        self.assertTrue(all('hidden' not in tag for tag in tags), 'no-JS loses zero dates')
        data = self.payload(page)
        self.assertEqual([p['cents'] for p in data['periods'][0]['daily']], ['70000', '6000', '0'])
        self.assertEqual(len(data['periods'][1]['daily']), 4)
        partial = self.query(a_start='2026-09-23', a_end='2027-12-31', b_start='2026-10-01', b_end='2028-01-01')
        self.assertEqual(len(partial.context['comparison']['daily_rows']), 2)
        self.assertTrue(all(row['b'] is None for row in partial.context['comparison']['daily_rows']))

    def test_daily_empty_and_all_future_do_not_invent_statistics(self):
        self._login()
        empty = self.query(a_end='2026-09-03', b_end='2026-08-02')
        self.assertIn('沒有符合的非零支出', empty.text)
        self.assertEqual(len(empty.context['comparison']['daily_rows']), 3)
        future = self.query(a_start='2026-10-01', a_end='2027-01-01', b_start='2027-01-01', b_end='2028-01-01')
        self.assertEqual(future.context['comparison']['daily_rows'], [])
        self.assertNotIn('id="comparison-daily-table"', future.text)
        self.assertIn('期間尚未開始', future.text)

    def test_redundant_total_chart_removed_but_category_charts_remain(self):
        self._login(); self.add('2026-09-01', 1); self.add('2026-09-01', 2, '交通')
        page = self.query()
        self.assertIn('每日支出比較', page.text)
        self.assertNotIn('累積支出', page.text)
        self.assertNotIn('id="comparison-total"', page.text)
        self.assertIn('id="comparison-category-chart"', page.text)
        self.assertIn('id="comparison-category-table"', page.text)
        self.assertEqual(page.text.count('id="comparison-pie-'), 1)
        # Pie canvases are outside details; only the text tables are collapsed.
        for body in re.findall(r'<details\b[^>]*>(.*?)</details>', page.text, re.S):
            self.assertNotIn('<canvas', body)

    def test_share_order_exact_denominator_and_one_decimal_tiny_positive_labels(self):
        self._login()
        self.add('2026-09-01', '9000', '居住'); self.add('2026-09-01', '1000', '交通')
        self.add('2026-09-01', '.01', '餐飲')
        self.add('2026-08-01', '900', '餐飲'); self.add('2026-08-01', '50', '交通')
        self.add('2026-08-01', '50', '居住')
        page = self.query(); data = self.payload(page)
        a, b = data['periods']
        self.assertEqual([data['categories'][i]['name'] for i in a['share_order']], ['居住', '交通', '餐飲'])
        self.assertEqual([data['categories'][i]['name'] for i in b['share_order']], ['餐飲', '交通', '居住'])
        cats = {r['name']: r for r in data['categories']}
        self.assertEqual(cats['餐飲']['a_percent'], '<0.1')
        self.assertEqual(cats['居住']['a_percent'], '90')
        self.assertEqual(cats['餐飲']['b_percent'], '90')
        self.assertEqual(cats['交通']['b_percent'], '5')
        self.assertEqual(cats['居住']['a_cents'], '900000')
        for key in ('a', 'b'):
            details = re.search(r'<details id="comparison-share-'+key+r'"([^>]*)>(.*?)</details>', page.text, re.S)
            self.assertIsNotNone(details); self.assertNotIn('open', details[1])
        self.assertIn('居住　9,000 元 · 90%', page.text)
        self.assertIn('餐飲　0.01 元 · &lt;0.1%', page.text)
        self.add('2026-09-01', '.03', '其他')
        rounded = self.payload(self.query())
        self.assertEqual(next(r for r in rounded['categories'] if r['name'] == '其他')['a_percent'], '<0.1')

    def test_signed_difference_is_exact_and_not_repeated_prose(self):
        self._login(); self.add('2026-09-01', '1000.29'); self.add('2026-08-01', '.29')
        page = self.query()
        self.assertEqual(page.context['comparison']['difference_text'], '+1,000 元')
        self.assertIn('差額（A − B）', page.text)
        self.assertEqual(page.text.count('正數表示 A 較多，負數表示 A 較少。'), 1)
        self.assertNotIn('多花', page.text); self.assertNotIn('少花', page.text)
        reverse = self.query(a_start='2026-08-01', a_end='2026-08-31', b_start='2026-09-01', b_end='2026-09-30')
        self.assertEqual(reverse.context['comparison']['difference_text'], '−1,000 元')
        same = self.query(b_start='2026-09-01', b_end='2026-09-30')
        self.assertEqual(same.context['comparison']['difference_text'], '0 元')
        future = self.query(b_start='2026-10-01', b_end='2026-10-31')
        self.assertIsNone(future.context['comparison']['difference_text'])


class ChartDistributionTests(unittest.TestCase):
    def test_official_bundle_map_and_both_licenses_are_served_locally(self):
        root = Path(__file__).resolve().parents[1]
        folder = root / 'web/static/vendor/chartjs-4.5.1'
        for name in ('chart.umd.min.js', 'chart.umd.min.js.map', 'LICENSE.md', 'kurkle-color-LICENSE.md'):
            self.assertTrue((folder/name).is_file(), 'local official asset/license missing: '+name)
        manifest = json.loads((folder/'provenance.json').read_text(encoding='utf-8'))
        for name, digest in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(), digest)
        self.assertEqual(manifest['version'], '4.5.1')
        self.assertEqual(manifest['bundled_color_version'], '0.3.2')
        for name in ('LICENSE.md', 'kurkle-color-LICENSE.md'):
            license_text = (folder/name).read_text(encoding='utf-8')
            self.assertIn('Permission is hereby granted', license_text)
            self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)


if __name__ == '__main__':
    unittest.main()
