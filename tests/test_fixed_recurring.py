"""Fixed-rule management and scheduled posting, only on isolated SQLite."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch

import db
import spending as sp


class FixedRecurringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'fixed.db'
        self.database = patch.object(db, 'DB_NAME', str(self.path))
        self.clock = patch.object(sp, 'today', return_value=date(2026, 9, 24))
        self.database.start()
        self.clock.start()
        db.init_db()

    def tearDown(self):
        self.clock.stop()
        self.database.stop()
        self.temp.cleanup()

    def snapshot(self):
        tables = sp.rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return {row['name']: sp.rows(f"SELECT * FROM {row['name']} ORDER BY rowid") for row in tables}

    def add(self, **changes):
        values = dict(kind='固定', name='房租', amount='100.29', cat='居住', start='2026-09', due_day=28)
        values.update(changes)
        return sp.add_recurring('a', **values)

    def test_all_origins_post_only_when_due_and_keep_scheduled_date(self):
        for kind in ('固定', '訂閱', '分期'):
            self.add(kind=kind, periods=2 if kind == '分期' else 0)
        self.assertEqual(sp.sync_recurring('a', date(2026, 9, 27)), 0)
        self.assertEqual(sp.month_report('a')['record_count'], 0)
        self.assertEqual(sp.sync_recurring('a', date(2026, 9, 28)), 3)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 27)), 0)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 31)), 3)
        self.assertEqual([r['spent_on'] for r in sp.rows('SELECT * FROM expenses ORDER BY id')],
                         ['2026-09-28'] * 3 + ['2026-10-28'] * 3)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 31)), 0)
        self.assertEqual(sp.sync_recurring('a', date(2026, 11, 28)), 2)

    def test_short_month_leap_day_and_year_boundary(self):
        with patch.object(sp, 'today', return_value=date(2028, 1, 15)):
            self.add(start='2028-01', due_day=31)
        self.assertEqual(sp.sync_recurring('a', date(2028, 2, 28)), 1)
        self.assertEqual(sp.sync_recurring('a', date(2028, 2, 29)), 1)
        self.assertEqual(sp.sync_recurring('a', date(2028, 4, 30)), 2)
        self.assertEqual([r['spent_on'] for r in sp.rows('SELECT * FROM expenses ORDER BY id')],
                         ['2028-01-31', '2028-02-29', '2028-03-31', '2028-04-30'])
        with patch.object(sp, 'today', return_value=date(2026, 12, 15)):
            sp.add_recurring('b', '固定', '跨年', 1, '居住', '2026-12', due_day=31)
        self.assertEqual(sp.sync_recurring('b', date(2027, 2, 28)), 3)
        self.assertEqual([r['spent_on'] for r in sp.rows("SELECT * FROM expenses WHERE user_id='b' ORDER BY id")],
                         ['2026-12-31', '2027-01-31', '2027-02-28'])

    def test_legacy_upgrade_preserves_posted_rows_and_seeds_original_settings(self):
        with sp.transaction() as conn:
            conn.execute('DROP TABLE IF EXISTS recurring_expense_versions')
            conn.execute('DROP TABLE recurring_expenses')
            conn.execute("CREATE TABLE recurring_expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,name TEXT NOT NULL,cents INTEGER NOT NULL,category TEXT NOT NULL,kind TEXT NOT NULL,start_month TEXT NOT NULL,periods INTEGER NOT NULL,due_day INTEGER,active INTEGER NOT NULL DEFAULT 1)")
            conn.execute("INSERT INTO recurring_expenses VALUES(1,'a','舊固定',1029,'居住','固定','2026-07',0,28,1)")
            conn.execute("INSERT INTO recurring_expenses VALUES(2,'b','無付款日',100,'居住','固定','2026-08',0,NULL,1)")
            conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,source,recurring_id,period) VALUES('a','2026-07-01',999,'其他','已入帳原樣','固定',1,'2026-07')")
        posted = sp.rows('SELECT * FROM expenses')
        db.init_db()
        versions = sp.rows('SELECT * FROM recurring_expense_versions ORDER BY recurring_id')
        self.assertEqual([(r['effective_month'], r['cents'], r['due_day']) for r in versions],
                         [('2026-07', 1029, 28), ('2026-08', 100, 1)])
        self.assertEqual(sp.rows('SELECT * FROM expenses'), posted)
        db.init_db()
        self.assertEqual(sp.rows('SELECT * FROM recurring_expense_versions ORDER BY recurring_id'), versions)
        self.assertEqual(sp.sync_recurring('a'), 1)
        self.assertEqual(sp.rows('SELECT * FROM expenses ORDER BY id')[0], posted[0])
        self.assertEqual(sp.rows('SELECT * FROM expenses ORDER BY id')[1]['spent_on'], '2026-08-28')
        self.assertEqual(sp.sync_recurring('b'), 2)
        self.assertEqual([r['spent_on'] for r in sp.rows("SELECT * FROM expenses WHERE user_id='b' ORDER BY id")],
                         ['2026-08-01', '2026-09-01'])

    def test_next_month_change_and_same_month_last_success_wins(self):
        key = self.add()
        self.assertEqual(sp.update_fixed_recurring('a', key, '新房租', '200.50', '交通', 3, 0), '2026-10')
        self.assertEqual(sp.update_fixed_recurring('a', key, '最後設定', '.29', '購物', 5, 1), '2026-10')
        state = sp.get_fixed_recurring('a', key)
        self.assertEqual((state['name'], state['cents'], state['due_day'], state['revision']), ('房租', 10029, 28, 2))
        self.assertEqual((state['pending']['name'], state['pending']['effective_month']), ('最後設定', '2026-10'))
        self.assertEqual(len(sp.rows('SELECT * FROM recurring_expense_versions')), 2)
        self.assertEqual(sp.sync_recurring('a', date(2026, 9, 28)), 1)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 4)), 0)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 5)), 1)
        self.assertEqual([(r['spent_on'], r['cents'], r['category'], r['note']) for r in sp.rows('SELECT * FROM expenses ORDER BY id')],
                         [('2026-09-28', 10029, '居住', '房租'), ('2026-10-05', 29, '購物', '最後設定')])
        with patch.object(sp, 'today', return_value=date(2026, 10, 6)):
            self.assertEqual(sp.get_fixed_recurring('a', key)['name'], '最後設定')
            self.assertIsNone(sp.get_fixed_recurring('a', key)['pending'])

    def test_history_catchup_uses_each_month_setting_not_latest(self):
        key = self.add()
        sp.update_fixed_recurring('a', key, '十月', 200, '交通', 12, 0)
        with patch.object(sp, 'today', return_value=date(2026, 10, 2)):
            sp.update_fixed_recurring('a', key, '十一月', 300, '購物', 30, 1)
        self.assertEqual(sp.sync_recurring('a', date(2026, 11, 29)), 2)
        self.assertEqual(sp.sync_recurring('a', date(2026, 12, 2)), 1)
        self.assertEqual([(r['spent_on'], r['cents'], r['note']) for r in sp.rows('SELECT * FROM expenses ORDER BY id')],
                         [('2026-09-28', 10029, '房租'), ('2026-10-12', 20000, '十月'), ('2026-11-30', 30000, '十一月')])

    def test_owner_source_and_revision_checks_have_no_side_effects(self):
        own = self.add()
        foreign = sp.add_recurring('b', '固定', '他人', 1, '居住', '2026-09')
        other = [self.add(kind=kind, periods=2 if kind == '分期' else 0) for kind in ('訂閱', '分期')]
        before = self.snapshot()
        for key in (foreign, *other, 999):
            for operation in (lambda: sp.get_fixed_recurring('a', key),
                              lambda: sp.update_fixed_recurring('a', key, '改', 1, '交通', 1, 0),
                              lambda: sp.stop_fixed_recurring('a', key, 0)):
                with self.assertRaises(sp.RecurringUnavailableError): operation()
                self.assertEqual(self.snapshot(), before)
        sp.update_fixed_recurring('a', own, '新', 1, '交通', 1, 0)
        before = self.snapshot()
        for operation in (lambda: sp.update_fixed_recurring('a', own, '舊表單', 1, '居住', 1, 0),
                          lambda: sp.stop_fixed_recurring('a', own, 0)):
            with self.assertRaises(sp.RecurringRevisionConflictError): operation()
            self.assertEqual(self.snapshot(), before)

    def test_stop_catches_due_only_in_same_transaction_and_retains_pending_history(self):
        due = self.add(due_day=20)
        future = self.add(name='尚未到期', due_day=28)
        sp.update_fixed_recurring('a', due, '下月', 2, '交通', 1, 0)
        sp.stop_fixed_recurring('a', due, 1)
        self.assertEqual([(r['spent_on'], r['cents']) for r in sp.rows('SELECT * FROM expenses')], [('2026-09-20', 10029)])
        sp.stop_recurring('a', future)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')), 1)
        self.assertEqual(sp.sync_recurring('a', date(2026, 12, 31)), 0)
        self.assertEqual(len(sp.rows('SELECT * FROM recurring_expense_versions')), 3)
        self.assertEqual([r['active'] for r in sp.list_fixed_recurring('a')], [0, 0])

    def test_posting_and_stop_failure_fully_roll_back(self):
        key = self.add(due_day=20)
        before = self.snapshot()
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_stop BEFORE UPDATE OF active ON recurring_expenses BEGIN SELECT RAISE(ABORT,'SECRET'); END")
        with self.assertRaises(sqlite3.Error): sp.stop_fixed_recurring('a', key, 0)
        self.assertEqual(self.snapshot(), before)
        with sp.transaction() as conn:
            conn.execute('DROP TRIGGER fail_stop')
            conn.execute("CREATE TRIGGER fail_action BEFORE INSERT ON expense_actions BEGIN SELECT RAISE(ABORT,'SECRET'); END")
        with self.assertRaises(sqlite3.Error): sp.sync_recurring('a')
        self.assertEqual(self.snapshot(), before)

    def test_invalid_fields_rollback_and_inactive_existing_categories(self):
        key = self.add()
        for name, amount, cat, due in (('', 1, '居住', 1), ('x' * 101, 1, '居住', 1),
                                       ('名', 'NaN', '居住', 1), ('名', '1.001', '居住', 1),
                                       ('名', 0, '居住', 1), ('名', 1, '外部分類', 1),
                                       ('名', 1, '居住', 0), ('名', 1, '居住', 32), ('名', 1, '居住', 1.5)):
            before = self.snapshot()
            with self.assertRaises((ValueError, TypeError)): sp.update_fixed_recurring('a', key, name, amount, cat, due, 0)
            self.assertEqual(self.snapshot(), before)
        sp.set_category('a', '居住', False)
        sp.update_fixed_recurring('a', key, '保留停用分類', 1, '居住', 28, 0)
        sp.set_category('a', '交通', False)
        with self.assertRaises(ValueError): sp.update_fixed_recurring('a', key, '不可新選停用', 1, '交通', 28, 1)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 28)), 2)
        self.assertNotIn('居住', sp.category_names('a'))

    def test_voided_and_undone_automatic_entries_never_reappear(self):
        key = self.add(due_day=20)
        sp.sync_recurring('a')
        row = sp.rows('SELECT * FROM expenses')[0]
        sp.void_expense('a', row['id'])
        self.assertEqual(sp.sync_recurring('a'), 0)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')), 1)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 20)), 1)
        action, _ = sp.undo('a'); sp.undo('a', action)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 31)), 0)
        self.assertEqual([r['voided'] for r in sp.rows('SELECT * FROM expenses ORDER BY id')], [1, 1])
        self.assertEqual(sp.get_fixed_recurring('a', key)['active'], 1)

    def test_parallel_sync_uses_unique_rule_month_and_one_action(self):
        self.add(due_day=20)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: sp.sync_recurring('a', date(2026, 10, 20)), range(2)))
        self.assertEqual(sorted(results), [0, 2])
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')), 2)
        self.assertEqual(len(sp.rows('SELECT * FROM expense_actions')), 2)
        self.assertEqual(len(sp.rows("SELECT * FROM spending_notices WHERE notice_key LIKE 'auto:%'")), 2)

    def test_category_rename_updates_pending_versions_and_revision_atomically(self):
        key = self.add()
        sp.update_fixed_recurring('a', key, '下一月', 2, '交通', 2, 0)
        sp.rename_category('a', '交通', '新交通')
        rule = sp.get_fixed_recurring('a', key)
        self.assertEqual(rule['pending']['category'], '新交通')
        self.assertEqual(rule['revision'], 2)
        with self.assertRaises(sp.RecurringRevisionConflictError): sp.stop_fixed_recurring('a', key, 1)
        sp.sync_recurring('a', date(2026, 10, 2))
        self.assertEqual(sp.rows('SELECT * FROM expenses ORDER BY id')[-1]['category'], '新交通')

    def test_missing_or_noninteger_revision_never_bypasses_conflict_checks(self):
        key = self.add()
        before = self.snapshot()
        for revision in (None, True, '0', -1):
            for operation in (lambda: sp.update_fixed_recurring('a', key, '覆蓋', 1, '居住', 1, revision),
                              lambda: sp.stop_fixed_recurring('a', key, revision)):
                with self.assertRaises(ValueError): operation()
                self.assertEqual(self.snapshot(), before)

    def test_service_fixed_only_operations_normalize_session_owner(self):
        import life_ledger_service as service
        self.assertEqual(service.get_recurring_start_months(), ['2026-09', '2026-10'])
        key = service.add_fixed_recurring(42, '服務固定', '.29', '居住', '2026-09', 20)
        other = sp.add_recurring('42', '訂閱', '不可由Web管理', 1, '居住', '2026-09')
        self.assertEqual([r['id'] for r in service.get_fixed_recurring_rules(42)], [key])
        self.assertEqual(service.get_fixed_recurring(42, key)['user_id'], '42')
        with self.assertRaises(service.RecurringUnavailableError): service.get_fixed_recurring(42, other)
        self.assertEqual(service.sync_fixed_recurring(42), 1)
        self.assertEqual([r['source'] for r in sp.rows('SELECT * FROM expenses')], ['固定'])
        self.assertEqual(service.update_fixed_recurring(42, key, '次月', 1, '交通', 25, 0), '2026-10')
        service.stop_fixed_recurring(42, key, 1)
        self.assertEqual(service.get_fixed_recurring(42, key)['active'], 0)
        with patch.object(sp, 'today', return_value=date(2026, 12, 31)):
            self.assertEqual(service.get_recurring_start_months(), ['2026-12', '2027-01'])

    def test_scheduled_update_failure_rolls_back_version_and_revision(self):
        key = self.add()
        before = self.snapshot()
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_revision BEFORE UPDATE OF revision ON recurring_expenses BEGIN SELECT RAISE(ABORT,'SECRET'); END")
        with self.assertRaises(sqlite3.Error):
            sp.update_fixed_recurring('a', key, '不可留下部分設定', 2, '交通', 5, 0)
        self.assertEqual(self.snapshot(), before)

    def test_future_start_edit_survives_reinitialization_and_cross_year(self):
        key = self.add(start='2026-10')
        sp.update_fixed_recurring('a', key, '下月開始的新設定', 2, '交通', 5, 0)
        db.init_db()
        self.assertEqual(sp.get_fixed_recurring('a', key)['pending']['name'], '下月開始的新設定')
        self.assertEqual(sp.sync_recurring('a'), 0)
        self.assertEqual(sp.sync_recurring('a', date(2026, 10, 5)), 1)
        row = sp.rows('SELECT * FROM expenses')[0]
        self.assertEqual((row['spent_on'], row['cents'], row['category']), ('2026-10-05', 200, '交通'))
        with patch.object(sp, 'today', return_value=date(2026, 12, 31)):
            self.assertEqual(sp.update_fixed_recurring('a', key, '跨年設定', 3, '居住', 31, 1), '2027-01')
        self.assertEqual(sp.sync_recurring('a', date(2027, 1, 30)), 2)
        self.assertEqual(sp.sync_recurring('a', date(2027, 1, 31)), 1)
        row = sp.rows('SELECT * FROM expenses ORDER BY id DESC')[0]
        self.assertEqual((row['spent_on'], row['cents']), ('2027-01-31', 300))

    def test_fixed_burden_pending_summary_keeps_existing_identity_privacy(self):
        import json
        import life_ledger_service as service
        key = self.add()
        sp.update_fixed_recurring('a', key, '下月', 2, '交通', 5, 0)
        summary = service.get_fixed_burdens('a')
        self.assertNotIn('user_id', json.dumps(summary))
        self.assertEqual(summary['rules'][0]['pending']['cents'], 200)
