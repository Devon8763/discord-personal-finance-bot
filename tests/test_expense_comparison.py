"""Expense comparison uses only isolated SQLite and OAuth doubles."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import db
import life_ledger_service as service
import spending as sp
from tests.test_web_auth import _WebLedgerTestFixture
from web import routes


class ExpenseComparisonCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = patch.object(db, 'DB_NAME', str(Path(self.temp.name) / 'comparison.sqlite3'))
        self.clock = patch.object(sp, 'today', return_value=date(2026, 9, 24))
        self.database.start(); self.today_mock = self.clock.start()
        db.init_db()

    def tearDown(self):
        self.clock.stop(); self.database.stop(); self.temp.cleanup()

    def add(self, on, amount, category='餐飲', note='午餐', user='42', **changes):
        key = sp.add(user, amount, category, note, on)
        if changes:
            with sp.transaction() as conn:
                for field, value in changes.items():
                    assert field in ('source', 'kind', 'voided', 'category')
                    conn.execute(f'UPDATE expenses SET {field}=? WHERE id=?', (value, key))
        return key

    def compare(self, a_start='2026-09-01', a_end='2026-09-30', b_start='2026-08-01', b_end='2026-08-31', **filters):
        self.assertTrue(callable(getattr(sp, 'expense_comparison', None)), 'core comparison interface missing')
        return sp.expense_comparison('42', a_start, a_end, b_start, b_end, **filters)

    def snapshot(self):
        return {r['name']:sp.rows(f"SELECT * FROM {r['name']} ORDER BY rowid")
                for r in sp.rows("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_precise_totals_count_difference_percentage_and_category_union(self):
        self.add('2026-09-01', '.29'); self.add('2026-09-24', '1000.01', '交通')
        self.add('2026-08-31', '.1', '居住'); self.add('2026-08-01', '.3')
        result = self.compare()
        self.assertEqual((result['a']['total_cents'], result['b']['total_cents']), (100030, 40))
        self.assertEqual((result['a']['record_count'], result['b']['record_count']), (2, 2))
        self.assertEqual(result['difference_cents'], 99990)
        self.assertEqual(result['change_percent'], Decimal('249975'))
        cats = {r['category']:r for r in result['categories']}
        self.assertEqual((cats['餐飲']['a_cents'], cats['餐飲']['b_cents'], cats['餐飲']['difference_cents']), (29,30,-1))
        self.assertEqual((cats['交通']['a_cents'],cats['交通']['b_cents']), (100001,0))
        self.assertEqual((cats['居住']['a_cents'],cats['居住']['b_cents']), (0,10))
        for period in ('a','b'):
            self.assertEqual(sum(r[period+'_cents'] for r in cats.values()), result[period]['total_cents'])

    def test_bounds_nonadjacent_half_year_unequal_and_overlapping_periods(self):
        for on, amount in [('2025-12-31',1),('2026-01-01',2),('2026-06-30',3),('2026-07-01',4),('2026-08-01',5)]:
            self.add(on,amount)
        result=self.compare('2026-01-01','2026-06-30','2025-12-31','2026-01-01')
        self.assertEqual((result['a']['total_cents'],result['b']['total_cents']), (500,300))
        self.assertTrue(result['periods_differ'])
        result=self.compare('2026-06-30','2026-07-01','2026-07-01','2026-08-01')
        self.assertEqual((result['a']['total_cents'],result['b']['total_cents']), (700,900))
        result=self.compare('2026-01-01','2026-01-01','2026-08-01','2026-08-01')
        self.assertFalse(result['periods_differ'])
        self.assertEqual(result['difference_cents'],-300)

    def test_started_unfinished_and_future_are_distinct_and_share_one_today(self):
        self.add('2026-09-24',1)
        with sp.transaction() as conn:
            conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('42','2026-09-25',900,'餐飲','未來資料')")
        self.today_mock.reset_mock()
        result=self.compare('2026-07-01','2026-12-31','2026-09-25','2027-01-01')
        self.assertEqual(self.today_mock.call_count,1)
        self.assertEqual(result['as_of'],'2026-09-24')
        self.assertEqual((result['a']['actual_start'],result['a']['actual_end'],result['a']['total_cents']),('2026-07-01','2026-09-24',100))
        self.assertTrue(result['a']['unfinished'])
        self.assertFalse(result['b']['started'])
        self.assertIsNone(result['b']['total_cents']); self.assertIsNone(result['b']['record_count'])
        self.assertIsNone(result['b']['actual_start']); self.assertIsNone(result['b']['actual_end'])
        self.assertIsNone(result['difference_cents']); self.assertIsNone(result['change_percent'])
        self.assertIsNone(result['categories'][0]['b_cents']); self.assertIsNone(result['categories'][0]['difference_cents'])
        both=self.compare('2026-10-01','2026-12-31','2027-01-01','2027-06-30')
        self.assertEqual(both['categories'],[]); self.assertIsNone(both['difference_cents'])

    def test_empty_zero_baseline_a_zero_both_zero_and_same_amount(self):
        empty=self.compare('2026-01-01','2026-01-01','2026-02-01','2026-02-01')
        self.assertEqual(empty['a']['total_cents'],0); self.assertEqual(empty['difference_cents'],0)
        self.assertIsNone(empty['change_percent']); self.assertEqual(empty['categories'],[])
        self.add('2026-01-01',1)
        more=self.compare('2026-01-01','2026-01-01','2026-02-01','2026-02-01')
        self.assertEqual(more['difference_cents'],100); self.assertIsNone(more['change_percent'])
        less=self.compare('2026-02-01','2026-02-01','2026-01-01','2026-01-01')
        self.assertEqual((less['difference_cents'],less['change_percent']),(-100,Decimal('-100')))
        same=self.compare('2026-01-01','2026-01-01','2026-01-01','2026-01-01')
        self.assertEqual((same['difference_cents'],same['change_percent']),(0,Decimal(0)))

    def test_literal_keyword_category_and_filters_apply_to_both_ranges(self):
        self.add('2026-09-01',1,'餐飲','Coffee 100%_.*')
        self.add('2026-09-02',2,'交通','Coffee 100%_.*')
        self.add('2026-08-01',3,'餐飲','coffee 100%_.*')
        self.add('2026-08-02',4,'餐飲','coffee 100xxx')
        by_cat=self.compare(category='餐飲')
        self.assertEqual((by_cat['a']['total_cents'],by_cat['b']['total_cents']),(100,700))
        keyword=self.compare(keyword='  COFFEE 100%_.*  ')
        self.assertEqual((keyword['a']['total_cents'],keyword['b']['total_cents']),(300,300))
        both=self.compare(category='餐飲',keyword='COFFEE 100%_.*')
        self.assertEqual((both['a']['total_cents'],both['b']['total_cents']),(100,300))
        self.assertEqual([r['category'] for r in both['categories']],['餐飲'])
        self.assertEqual(self.compare(keyword="' OR 1=1 --")['a']['total_cents'],0)

    def test_inactive_and_history_only_categories_remain_owner_scoped(self):
        self.add('2026-09-01',1,'居住')
        key=self.add('2026-08-01',2)
        with sp.transaction() as conn: conn.execute("UPDATE expenses SET category='舊分類' WHERE id=?",(key,))
        key=self.add('2026-08-01',3,user='99')
        with sp.transaction() as conn: conn.execute("UPDATE expenses SET category='他人分類' WHERE id=?",(key,))
        sp.set_category('42','居住',False)
        result=self.compare(category='居住')
        options={r['name']:r['state'] for r in result['category_options']}
        self.assertEqual(options['居住'],'inactive'); self.assertEqual(options['舊分類'],'historical')
        self.assertNotIn('他人分類',options)
        self.assertEqual(self.compare(category='舊分類')['b']['total_cents'],200)
        self.assertNotIn('居住',sp.category_names('42'))

    def test_all_origins_isolation_voided_nonconsumption_and_full_result(self):
        for origin in ('manual','固定','訂閱','分期'):
            self.add('2026-09-01','.29',source=origin)
        self.add('2026-09-01',100,user='99')
        self.add('2026-09-01',100,voided=1)
        for kind in ('income','investment','transfer'):
            self.add('2026-09-01',100,kind=kind)
        with sp.transaction() as conn:
            conn.executemany("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('42','2026-09-01',1,'餐飲','大量')", [()] * 10001)
        before=self.snapshot()
        result=self.compare()
        self.assertEqual((result['a']['record_count'],result['a']['total_cents']),(10005,10117))
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(len(sp.search_expenses('42',keyword='午餐',start='2026-09-01')),1)

    def test_add_edit_soft_void_refresh_comparison_without_rewriting_schedule(self):
        key=self.add('2026-09-01','.29')
        self.assertEqual(self.compare()['a']['total_cents'],29)
        sp.edit('42',key,'.3','交通','變更','2026-08-31',expected_revision=0)
        result=self.compare()
        self.assertEqual((result['a']['total_cents'],result['b']['total_cents']),(0,30))
        sp.void_expense('42',key,expected_revision=1)
        self.assertEqual(self.compare()['b']['total_cents'],0)
        self.assertEqual(sp.rows('SELECT voided FROM expenses')[0]['voided'],1)

    def test_invalid_dates_fail_before_any_connection_and_filter_before_stats(self):
        self.assertTrue(callable(getattr(sp,'expense_comparison',None)))
        for dates in [('bad','2026-09-01','2026-08-01','2026-08-31'),
                      ('2026-02-30','2026-09-01','2026-08-01','2026-08-31'),
                      ('20260901','2026-09-01','2026-08-01','2026-08-31'),
                      ('2026-09-02','2026-09-01','2026-08-01','2026-08-31'),
                      ('2026-09-01','2026-09-01','','2026-08-31'),
                      ('2026-09-01','2026-09-01','2026-08-31','2026-08-01')]:
            with patch.object(sp,'get_conn',side_effect=AssertionError('invalid input queried ledger')):
                with self.assertRaises(ValueError): self.compare(*dates)
        with patch.object(sp,'_list_expenses_between',side_effect=AssertionError('invalid category queried statistics')):
            with self.assertRaises(ValueError): self.compare(category='他人的分類')

    def test_read_only_single_connection_snapshot_survives_interleaved_write(self):
        self.assertTrue(callable(getattr(sp,'expense_comparison',None)))
        self.add('2026-09-01',1); key=self.add('2026-08-01',2)
        with closing(sqlite3.connect(db.DB_NAME)) as conn: conn.execute('PRAGMA journal_mode=WAL')
        original=sp._list_expenses_between
        calls=[]
        def query(*args,**kwargs):
            result=original(*args,**kwargs); calls.append(kwargs['conn'])
            if len(calls)==1:
                with closing(sqlite3.connect(db.DB_NAME)) as writer:
                    writer.execute('UPDATE expenses SET cents=999 WHERE id=?',(key,))
                    writer.commit()
            return result
        with patch.object(sp,'_list_expenses_between',side_effect=query),patch.object(sp,'get_conn',wraps=sp.get_conn) as connection:
            result=self.compare()
        self.assertEqual(connection.call_count,1)
        self.assertIs(calls[0],calls[1])
        self.assertEqual(result['b']['total_cents'],200)
        self.assertEqual(result['categories'][0]['b_cents'],200)
        self.assertEqual(self.compare()['b']['total_cents'],999)

    def test_no_sync_no_payment_initialization_no_write_sql(self):
        self.assertTrue(callable(getattr(sp,'expense_comparison',None)))
        sp.add_recurring('42','固定','已到期未補記',1,'居住','2026-09')
        before=self.snapshot(); statements=[]; connect=sp.get_conn
        def traced():
            conn=connect(); conn.set_trace_callback(statements.append); return conn
        with patch.object(sp,'get_conn',side_effect=traced),patch.object(sp,'sync_recurring',side_effect=AssertionError('no sync')):
            self.compare()
        self.assertEqual(self.snapshot(),before)
        self.assertTrue(statements)
        self.assertTrue(all(sql.lstrip().split()[0].upper() in ('SELECT','BEGIN') for sql in statements),statements)

    def test_service_uses_core_today_and_normalizes_owner_without_identity_in_result(self):
        self.assertTrue(callable(getattr(service,'get_expense_comparison',None)))
        self.add('2024-02-29',1)
        self.today_mock.reset_mock()
        result=service.get_expense_comparison(42,'2024-02-01','2024-02-29','2023-12-31','2024-01-01')
        self.assertEqual(result['a']['total_cents'],100)
        self.assertEqual(self.today_mock.call_count,1)
        self.assertNotIn('user_id',json.dumps(result,default=str))
        with patch.object(sp,'today',side_effect=AssertionError('today must be reused')):
            self.assertEqual(service.get_expense_comparison(42,'2024-02-01','2024-02-29','2024-01-01','2024-01-31',as_of=date(2024,2,29))['as_of'],'2024-02-29')


class ExpenseComparisonWebTests(_WebLedgerTestFixture, unittest.TestCase):
    def query(self, **changes):
        values=dict(a_start='2026-09-01',a_end='2026-09-30',b_start='2026-08-01',b_end='2026-08-31',category='',keyword='')
        values.update(changes)
        return self.client.get('/compare?'+urlencode(values,doseq=True))

    def add(self,on,amount,category='餐飲',note='測試消費',user=None):
        return service.add_expense(user or self.user_id,amount,category,note,on)

    def snapshot(self):
        return {r['name']:sp.rows(f"SELECT * FROM {r['name']} ORDER BY rowid")
                for r in sp.rows("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_login_required_and_home_entry_and_default_single_read_only_form(self):
        with patch.object(sp,'get_conn',side_effect=AssertionError('unauthorized ledger read')):
            self.assertEqual(self.client.get('/compare').status_code,403)
        self._login()
        home=self.client.get('/')
        self.assertIn('href="/compare"',home.text)
        self.assertIn('支出比較',home.text)
        before=self.snapshot()
        with patch.object(routes,'taiwan_today',side_effect=AssertionError('use core date')),patch.object(service,'get_today',wraps=service.get_today) as clock:
            page=self.client.get('/compare')
        self.assertEqual(page.status_code,200); self.assertEqual(clock.call_count,1)
        self.assertEqual(page.headers['cache-control'],'no-store')
        self.assertEqual(page.text.count('<form '),1)
        self.assertIn('method="get"',page.text)
        for field in ('a_start','a_end','b_start','b_end','category','keyword'):
            self.assertIn('name="'+field+'"',page.text)
        self.assertIn('value="2026-09-30"',page.text)
        self.assertIn('value="2026-08-31"',page.text)
        self.assertIn('尚未結束',page.text); self.assertIn('截至 2026-09-24',page.text)
        self.assertIn('期間長度或完整程度不同',page.text)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.client.post('/compare',data={}).status_code,405)

    def test_default_ranges_cross_year_and_leap_year_reuse_today(self):
        self._login()
        for day,a_end,b_start,b_end in [(date(2026,1,3),'2026-01-31','2025-12-01','2025-12-31'),
                                       (date(2024,3,2),'2024-03-31','2024-02-01','2024-02-29')]:
            with patch.object(service,'get_today',return_value=day) as clock,patch.object(sp,'today',side_effect=AssertionError('shared today only')):
                page=self.client.get('/compare')
            self.assertEqual(page.status_code,200); self.assertEqual(clock.call_count,1)
            for value in (a_end,b_start,b_end): self.assertIn('value="'+value+'"',page.text)

    def test_bar_scale_exact_money_percent_basis_union_and_accessibility(self):
        self._login()
        self.add('2026-09-01','1000.29','居住')
        self.add('2026-08-01','500','交通')
        page=self.query(a_start='2026-09-01',a_end='2026-09-01',b_start='2026-08-01',b_end='2026-08-01')
        self.assertEqual(page.status_code,200)
        self.assertIn('1,000.29 元',page.text); self.assertIn('500 元',page.text)
        self.assertNotIn('500.00',page.text)
        self.assertIn('+500.29 元',page.text)
        self.assertIn('期間 B 為基準',page.text); self.assertIn('100.06%',page.text)
        self.assertEqual(page.text.count('<progress '),2)
        self.assertEqual(page.text.count('max="100029"'),2)
        self.assertIn('value="100029"',page.text); self.assertIn('value="50000"',page.text)
        self.assertIn('aria-label="期間 A',page.text); self.assertIn('aria-label="期間 B',page.text)
        self.assertIn('<caption>分類比較',page.text); self.assertIn('scope="col"',page.text)
        self.assertNotIn('期間長度或完整程度不同',page.text)
        self.assertIn('/static/vendor/chartjs-4.5.1/chart.umd.min.js',page.text)
        self.assertIn('/static/comparison_charts.js',page.text)
        rows={r['category']:r for r in page.context['comparison']['categories']}
        self.assertEqual((rows['居住']['a_cents'],rows['居住']['b_cents']), (100029,0))
        self.assertEqual((rows['交通']['a_cents'],rows['交通']['b_cents']), (0,50000))

    def test_category_and_keyword_and_disabled_history_choices(self):
        self._login()
        self.add('2026-09-01',1,'居住','Coffee 100%_')
        self.add('2026-09-01',9,'交通','Coffee 100%_')
        self.add('2026-08-01',2,'居住','coffee 100%_')
        self.add('2026-08-01',9,'居住','別的用途')
        key=self.add('2026-08-01',3)
        with sp.transaction() as conn: conn.execute("UPDATE expenses SET category='歷史分類' WHERE id=?",(key,))
        sp.set_category(self.user_id,'居住',False)
        before=self.snapshot()
        page=self.query(category='居住',keyword='COFFEE 100%_')
        self.assertEqual(page.status_code,200)
        self.assertIn('居住（已停用）',page.text)
        self.assertIn('歷史分類（歷史）',page.text)
        result=page.context['comparison']
        self.assertEqual((result['a']['total_cents'],result['b']['total_cents']),(100,200))
        self.assertEqual([r['category'] for r in result['categories']],['居住'])
        self.assertIn('−1 元',page.text)
        self.assertEqual(self.snapshot(),before)

    def test_future_is_not_zero_and_zero_baseline_or_empty_is_explained(self):
        self._login()
        future=self.query(a_start='2026-10-01',a_end='2026-12-31',b_start='2027-01-01',b_end='2027-06-30')
        self.assertEqual(future.status_code,200)
        self.assertIn('期間尚未開始',future.text)
        self.assertIsNone(future.context['comparison']['difference_cents'])
        self.assertIsNone(future.context['comparison']['difference_text'])
        self.assertNotIn('沒有符合條件的消費',future.text)
        self.assertEqual(future.text.count('<progress '),0)
        empty=self.query(a_start='2026-09-01',a_end='2026-09-01',b_start='2026-08-01',b_end='2026-08-01')
        self.assertEqual(empty.context['comparison']['difference_text'], '0 元')
        self.assertIn('無法計算百分比',empty.text)
        self.assertIn('沒有符合條件的消費',empty.text)
        self.assertEqual(empty.text.count('max="1"'),2)
        self.assertEqual(empty.text.count('value="0"'),2)
        self.add('2026-09-01','.29')
        only_a=self.query()
        self.assertIn('+0.29 元',only_a.text)
        self.assertIn('無法計算百分比',only_a.text)
        self.assertNotIn('NaN',only_a.text); self.assertNotIn('Infinity',only_a.text)
        only_b=self.query(a_start='2026-08-01',a_end='2026-08-01',b_start='2026-09-01',b_end='2026-09-01')
        self.assertIn('−0.29 元',only_b.text); self.assertIn('100%',only_b.text)

    def test_partial_future_selected_range_actual_range_and_overlap(self):
        self._login(); self.add('2026-09-24',1)
        page=self.query(a_start='2026-07-01',a_end='2026-12-31',b_start='2026-09-01',b_end='2026-09-24')
        self.assertEqual(page.status_code,200)
        self.assertIn('2026-07-01 ～ 2026-12-31',page.text)
        self.assertIn('2026-07-01 ～ 2026-09-24',page.text)
        self.assertIn('尚未結束／截至 2026-09-24',page.text)
        self.assertIn('期間長度或完整程度不同',page.text)
        self.assertEqual(page.context['comparison']['difference_cents'],0)
        self.assertNotIn('更節省',page.text); self.assertNotIn('花費速度',page.text)

    def test_invalid_date_inputs_are_safe_preserved_and_never_query_statistics(self):
        self._login()
        for changes in ({'a_start':'2026-02-30'},{'a_start':'20260901'}, {'a_start':'<script>bad</script>'},
                        {'a_start':''},{'a_start':['2026-09-01','2026-09-02']},
                        {'a_start':'2026-09-24','a_end':'2026-09-01'},
                        {'b_start':'2026-08-31','b_end':'2026-08-01'}, {'keyword':'x'*201}):
            before=self.snapshot()
            with patch.object(service,'get_expense_comparison',side_effect=AssertionError('invalid statistics')):
                page=self.query(**changes)
            self.assertEqual(page.status_code,400,changes)
            self.assertIn('日期',page.text)
            self.assertNotIn('<script>',page.text)
            self.assertIsNone(page.context['comparison'])
            self.assertEqual(self.snapshot(),before)
        page=self.query(a_start='bad',keyword='保留項目')
        self.assertIn('value="bad"',page.text); self.assertIn('保留項目',page.text)
        with patch.object(sp,'_list_expenses_between',side_effect=AssertionError('invalid category stats')):
            page=self.query(category='他人的分類')
        self.assertEqual(page.status_code,400)

    def test_session_isolation_all_origins_full_count_no_sync_or_writes(self):
        self._login()
        self.add('2026-09-01',100,user='999999999999999999')
        for origin in ('manual','固定','訂閱','分期'):
            key=self.add('2026-09-01','.29')
            with sp.transaction() as conn: conn.execute('UPDATE expenses SET source=? WHERE id=?',(origin,key))
        key=self.add('2026-09-01',100); service.void_expense(self.user_id,key)
        with sp.transaction() as conn:
            conn.executemany("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES(?,'2026-09-01',1,'餐飲','大量')",[(self.user_id,)]*10001)
        sp.add_recurring(self.user_id,'固定','不因比較頁而入帳',1,'居住','2026-09')
        before=self.snapshot()
        with patch.object(sp,'sync_recurring',side_effect=AssertionError('no sync')),patch.object(service,'sync_fixed_recurring',side_effect=AssertionError('no sync')):
            page=self.query(user_id='999999999999999999',limit=1,as_of='2099-01-01')
        self.assertEqual(page.status_code,200)
        self.assertEqual(page.context['comparison']['a']['record_count'],10005)
        self.assertEqual(page.context['comparison']['a']['total_cents'],10117)
        self.assertEqual(self.snapshot(),before)

    def test_core_provider_errors_are_fixed_safe_and_do_not_show_partial_results(self):
        self._login()
        with patch.object(service,'get_expense_comparison',side_effect=sqlite3.OperationalError('SECRET_PATH')):
            page=self.query(keyword='保留篩選')
        self.assertEqual(page.status_code,503)
        self.assertNotIn('SECRET_PATH',page.text)
        self.assertIn('保留篩選',page.text)
        self.assertIsNone(page.context['comparison'])
        self.assertEqual(page.headers['cache-control'],'no-store')

    def test_existing_add_edit_and_soft_delete_are_reflected_after_reload(self):
        self._login(); key=self.add('2026-09-01','.29')
        first=self.query(); self.assertEqual(first.status_code,200)
        self.assertEqual(first.context['comparison']['a']['total_cents'],29)
        service.update_expense(self.user_id,key,'.30','交通','變更','2026-08-31',expected_revision=0)
        page=self.query()
        self.assertEqual((page.context['comparison']['a']['total_cents'],page.context['comparison']['b']['total_cents']),(0,30))
        service.void_expense(self.user_id,key,expected_revision=1)
        self.assertEqual(self.query().context['comparison']['b']['total_cents'],0)


if __name__ == "__main__":
    unittest.main()
