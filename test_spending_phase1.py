"""Phase-one acceptance checks; every database is temporary."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch

import db
import spending as sp


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'test.db')
        self.patcher = patch.object(db, 'DB_NAME', self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.clock = patch('spending.today', return_value=date(2026, 9, 10))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        db.init_db()

    def test_defaults_snapshot_disable_and_owner_isolation(self):
        self.assertEqual([p['name'] for p in sp.payment_sources('a')], ['未指定', '現金'])
        source = sp.add_payment_source('a', '街口支付')
        key = sp.add('a', '120.25', '餐飲', '午餐', payment_source_id=source)
        sp.rename_payment_source('a', source, '街口')
        self.assertEqual(sp.get_expense('a', key)['payment_source_name'], '街口支付')
        sp.disable_payment_source('a', source)
        self.assertNotIn(source, [p['id'] for p in sp.payment_sources('a')])
        self.assertIn(source, [p['id'] for p in sp.payment_sources('a', True)])
        for user in ('a', 'b'):
            with self.assertRaises(ValueError):
                sp.add(user, 1, '餐飲', '失敗', payment_source_id=source)
        for operation in (sp.rename_payment_source, sp.disable_payment_source):
            with self.assertRaises(ValueError):
                operation('b', source, '竄改') if operation is sp.rename_payment_source else operation('b', source)
        with self.assertRaises(ValueError):
            sp.get_expense('b', key)
        self.assertEqual(sp.month_report('a')['total'], 120.25)
        self.assertEqual(sp.month_report('b')['total'], 0)

    def test_edit_snapshot_undo_stale_form_and_inactive_category(self):
        first = sp.add_payment_source('a', '信用卡')
        second = sp.add_payment_source('a', '電子支付')
        key = sp.add('a', 100, '餐飲', '午餐', payment_source_id=first)
        original = sp.get_expense('a', key)
        sp.set_category('a', '餐飲', False)
        sp.disable_payment_source('a', first)
        sp.edit('a', key, 150, '餐飲', '晚餐', '2026-09-09')
        self.assertEqual(sp.get_expense('a', key)['payment_source_name'], '信用卡')
        with self.assertRaises(ValueError):
            sp.edit('a', key, 1, '餐飲', '過期', '2026-09-09', expected_revision=original['revision'])
        sp.edit('a', key, 200, '交通', '車票', '2026-08-31', payment_source_id=second)
        action, _ = sp.undo('a')
        sp.undo('a', action)
        row = sp.get_expense('a', key)
        self.assertEqual((row['cents'], row['payment_source_id'], row['payment_source_name']), (15000, first, '信用卡'))
        self.assertGreater(row['revision'], original['revision'])
        self.assertEqual(sp.month_report('a', '2026-08')['total'], 0)

    def test_only_valid_consumption_counts_and_six_full_months(self):
        source = sp.add_payment_source('a', '台新帳戶')
        sp.set_budget('a', '2026-09', '總額', 100)
        sp.add('a', 80, '交通', '車票', payment_source_id=source)
        sp.add('a', 25, '餐飲', '前月月底', '2026-08-31')
        sp.add('b', 999, '餐飲', '其他人')
        key = sp.add('a', 999, '餐飲', '撤銷')
        action, _ = sp.undo('a'); sp.undo('a', action)
        with sp.transaction() as conn:
            conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,kind) VALUES('a','2026-09-10',99900,'其他','排除','other')")
        report = sp.month_report('a')
        self.assertEqual(report['total'], 80)
        self.assertEqual(report['budgets'][0]['used_percent'], 80)
        data = sp.chart_data('a', '2026-09')
        self.assertEqual(data['payments'], [('台新帳戶', 8000)])
        self.assertEqual(data['categories'], [('交通', 8000)])
        self.assertEqual(data['months'], [('2026-04', 0), ('2026-05', 0), ('2026-06', 0), ('2026-07', 0), ('2026-08', 2500), ('2026-09', 8000)])
        self.assertEqual([r['id'] for r in sp.month_expenses('a', '2026-09')], [1])
        with self.assertRaises(ValueError):
            sp.get_expense('a', key)
        self.assertEqual(sp.chart_data('empty', '2026-09')['payments'], [])

    def test_names_validation_and_builtin_guarantees(self):
        for name in ('', '  ', 'x' * 31, 'bad\nname'):
            with self.assertRaises(ValueError):
                sp.add_payment_source('a', name)
        first = sp.add_payment_source('a', '信用卡')
        with self.assertRaises(ValueError):
            sp.add_payment_source('a', ' 信用卡 ')
        for source in sp.payment_sources('a')[:2]:
            with self.assertRaises(ValueError):
                sp.disable_payment_source('a', source['id'])
            with self.assertRaises(ValueError):
                sp.rename_payment_source('a', source['id'], '改名')
        with self.assertRaises(ValueError):
            sp.rename_payment_source('a', first, '現金')
        self.assertIsInstance(sp.add_payment_source('b', '信用卡'), int)

    def test_old_schema_migration_and_legacy_undo_are_repeatable(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute('DROP TABLE expenses')
            conn.execute("CREATE TABLE expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,spent_on TEXT NOT NULL,cents INTEGER NOT NULL,category TEXT NOT NULL,note TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'manual',recurring_id INTEGER,period TEXT,voided INTEGER NOT NULL DEFAULT 0,UNIQUE(recurring_id,period))")
            conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('old','2026-09-01',10000,'餐飲','舊資料')")
            old = dict(spent_on='2026-09-01', cents=5000, category='餐飲', note='之前', voided=0)
            conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', ('old', 1, json.dumps(old)))
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('old','2330',900,2)")
        conn.close()
        db.init_db(); db.init_db()
        row = sp.get_expense('old', 1)
        self.assertEqual((row['cents'], row['payment_source_name'], row['kind']), (10000, '未指定', 'consumption'))
        action, _ = sp.undo('old'); sp.undo('old', action)
        self.assertEqual(sp.get_expense('old', 1)['payment_source_name'], '未指定')
        self.assertEqual(sp.month_report('old')['total'], 50)
        self.assertEqual(sp.rows('SELECT shares FROM assets'), [{'shares': 2.0}])

    def test_concurrent_writes_and_clear_leave_investment_untouched(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            keys = list(pool.map(lambda n: sp.add('a', 1, '餐飲', str(n)), range(24)))
        self.assertEqual(len(set(keys)), 24)
        self.assertEqual(sp.month_report('a')['total'], 24)
        source = sp.add_payment_source('b', '保留')
        with sp.transaction() as conn:
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('a','2330',900,1)")
        sp.clear('a')
        self.assertEqual(sp.rows("SELECT * FROM payment_sources WHERE user_id='a'"), [])
        self.assertIn(source, [p['id'] for p in sp.payment_sources('b')])
        self.assertEqual(sp.rows('SELECT shares FROM assets'), [{'shares': 1.0}])

    def test_alerts_ignore_nonconsumption_and_recurring_keeps_date_rule(self):
        with sp.transaction() as conn:
            conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,kind) VALUES('a','2026-09-10',99900,'其他','排除','other')")
        sp.set_budget('a','2026-09','總額',100)
        self.assertEqual(sp.notices('a'),[])
        sp.add_recurring('a','訂閱','影音',50,'娛樂','2026-09')
        sp.sync_recurring('a')
        row=sp.month_expenses('a','2026-09')[0]
        self.assertEqual(row['payment_source_name'],'未指定')
        with self.assertRaises(ValueError):
            sp.edit('a',row['id'],40,'娛樂','影音','2026-09-02')
        source=sp.add_payment_source('a','信用卡')
        sp.edit('a',row['id'],40,'娛樂','影音','2026-09-01',payment_source_id=source)
        self.assertEqual(sp.month_report('a')['fixed'],40)
        self.assertEqual(sp.chart_data('a')['payments'],[('信用卡',4000)])
        self.assertEqual(sp.sync_recurring('a'),0)
