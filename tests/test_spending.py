import json
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
import db
import spending as sp


class SpendingTests(unittest.TestCase):
    def test_core_errors_have_no_discord_command_instructions(self):
        for action in (lambda: sp.category('不存在', 'a'),
                       lambda: sp.set_budget('a', '2026-09', '不存在', 1),
                       lambda: sp.add('a', 1, '餐飲', '用途', '2026-10-01'),
                       lambda: sp.set_reminders('a', [0]),
                       lambda: sp.month_report('a', '2026-10')):
            with self.subTest(action=action), self.assertRaises(ValueError) as caught:
                action()
            self.assertNotIn('!', str(caught.exception))
        key = sp.add('a', 1, '餐飲', '用途')
        action_id, _ = sp.undo('a')
        with self.assertRaises(ValueError) as caught:
            sp.undo('a', action_id+1)
        self.assertNotIn('!', str(caught.exception))
        self.assertEqual(sp.get_expense('a', key)['voided'], 0)

    def test_expense_range_reuses_scope_order_and_boundaries(self):
        with patch('spending.today', return_value=date(2026, 9, 24)):
            sp.add('a', 1, '餐飲', '前界外', '2026-08-30')
            first = sp.add('a', 1, '餐飲', '前界', '2026-08-31')
            for kind, periods in (('固定', 0), ('訂閱', 0), ('分期', 2)):
                sp.add_recurring('a', kind, kind, 1, '居住', '2026-09', periods)
            sp.add_recurring('a', '固定', '未入帳', 999, '居住', '2026-10')
            sp.sync_recurring('a', date(2026, 9, 24))
            last = [sp.add('a', 1, '餐飲', '後界', '2026-09-24') for _ in range(2)]
            sp.add('b', 999, '餐飲', '他人', '2026-09-24')
            removed = sp.add('a', 999, '餐飲', '撤銷', '2026-09-24')
            sp.void_expense('a', removed)
            for kind in ('income', 'transfer', 'investment'):
                key = sp.add('a', 999, '餐飲', kind, '2026-09-24')
                with sp.transaction() as conn:
                    conn.execute('UPDATE expenses SET kind=? WHERE id=?', (kind, key))
            with patch.object(sp, 'sync_recurring', side_effect=AssertionError('no sync')):
                result = sp.list_expenses_in_range('a', '2026-08-31', '2026-09-24')
            self.assertEqual(result['total'], 6)
            self.assertEqual([row['id'] for row in result['items'][:2]], last[::-1])
            self.assertEqual(result['items'][-1]['id'], first)
            self.assertEqual({row['source'] for row in result['items']}, {'manual', '固定', '訂閱', '分期'})
            monthly = sp.list_expenses('a', '2026-09')
            self.assertEqual(sp.list_expenses_in_range('a', '2026-09-01', '2026-09-24'), monthly)
            paged = sp.list_expenses('a', '2026-09', limit=2, offset=1)
            self.assertEqual(paged['items'], monthly['items'][1:3])
            self.assertEqual(paged['total'], monthly['total'])
            self.assertIn(removed, [row['id'] for row in sp.list_expenses('a', '2026-09', True)['items']])

    def test_expense_range_optional_bounds_and_keyword(self):
        keys = [sp.add('a', 1, '餐飲', note, on) for note, on in (
            ('Cafe %_\"', '2026-08-31'), ('cafe 早餐', '2026-09-01'), ('午餐', '2026-09-07'))]
        for start, end, keyword, expected in (
            (None, None, ' CAFE ', keys[1::-1]), (None, '2026-08-31', 'cafe', keys[:1]),
            ('2026-09-01', None, '', keys[:0:-1]), (None, None, '', keys[::-1]),
            (None, None, '%_', keys[:1]), (None, None, '\"', keys[:1]),
            (None, None, "' OR 1=1 --", []),
        ):
            with self.subTest(start=start, end=end, keyword=keyword):
                result = sp.list_expenses_in_range('a', start, end, keyword=keyword)
                self.assertEqual([row['id'] for row in result['items']], expected)
                self.assertEqual(result['total'], len(expected))

    def test_expense_range_rejects_invalid_dates(self):
        for start, end in (('', None), (None, ''), ('2026/09/01', None), ('20260901', None),
                           ('2026-9-01', None), ('2026-02-30', None), (None, '2026-09-08'),
                           ('2026-09-08', None), ('2026-09-07', '2026-09-01')):
            with self.subTest(start=start, end=end), patch.object(sp, 'rows') as query:
                with self.assertRaises(ValueError):
                    sp.list_expenses_in_range('a', start, end)
                query.assert_not_called()
        for on in ('2024-02-29', '2025-12-31', '2026-01-01'):
            sp.add('a', 1, '餐飲', on, on)
            self.assertEqual(sp.list_expenses_in_range('a', on, on)['total'], 1)
        self.assertEqual(sp.list_expenses_in_range('a', '2025-12-31', '2026-01-01')['total'], 2)
        self.assertEqual(sp.list_expenses('a', '2026-10')['items'], [])
        for args, message in (((-1, 0), '筆數'), ((None, -1), '起始位置')):
            with self.assertRaisesRegex(ValueError, message):
                sp.list_expenses('a', 'invalid', limit=args[0], offset=args[1])

    def test_month_summary_exact_cents_and_legacy_keys(self):
        sp.set_budget('a', '2026-09', '餐飲', 1)
        sp.add('a', '0.29', '餐飲', '小數')
        summary = sp.month_report('a', '2026-09')
        self.assertEqual(summary['total_cents'], 29)
        self.assertEqual(summary['categories']['餐飲']['amount_cents'], 29)
        budget = summary['budgets'][0]
        self.assertEqual((budget['budget_cents'], budget['spent_cents'], budget['remaining_cents']), (100, 29, 71))
        for row, key, expected in ((summary, 'total', .29), (summary['categories']['餐飲'], 'amount', .29),
                                   (budget, 'budget', 1.), (budget, 'spent', .29),
                                   (budget, 'remaining', .71), (budget, 'used_percent', 29.)):
            self.assertEqual(row[key], expected)
            self.assertIsInstance(row[key], float)
        sp.set_budget('a', '2026-09', '交通', 2)
        empty = next(row for row in sp.month_report('a')['budgets'] if row['category'] == '交通')
        self.assertEqual((empty['spent_cents'], empty['remaining_cents']), (0, 200))
        sp.add('a', 2, '餐飲', '超支')
        sp.add('a', 3, '其他', '無分類預算')
        summary = sp.month_report('a')
        self.assertEqual(summary['total_cents'], 529)
        self.assertEqual(next(row for row in summary['budgets'] if row['category'] == '餐飲')['remaining_cents'], -129)

        with sp.transaction() as conn:
            conn.execute('DELETE FROM expenses')
        # Seed many valid maximum-sized records in one isolated transaction; keep
        # core writes for the originals and the additional cent, without O(n²) alerts.
        for _ in range(2):
            sp.add('a', 1000000000, '餐飲', '大額')
        with sp.transaction() as conn:
            conn.executemany("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES(?,?,?,?,?)",
                             (('a', '2026-09-07', 100000000000, '餐飲', '大額') for _ in range(99998)))
        sp.add('a', '.01', '餐飲', '最後一分')
        summary = sp.month_report('a')
        expected = 100000 * 100000000000 + 1
        self.assertEqual(summary['total_cents'], expected)
        self.assertEqual(summary['categories']['餐飲']['amount_cents'], expected)
        self.assertEqual(next(row for row in summary['budgets'] if row['category'] == '餐飲')['spent_cents'], expected)
        self.assertNotEqual(int(summary['total'] * 100), expected)

    def test_month_summary_consumption_scope_and_inactive_budget(self):
        sp.set_budget('a', '2026-09', '居住', 100)
        sp.add('a', '.29', '餐飲', '手動')
        for kind, amount, periods in (('固定', 10, 0), ('訂閱', 20, 0), ('分期', 30, 2)):
            sp.add_recurring('a', kind, kind, amount, '居住', '2026-09', periods)
        sp.sync_recurring('a', date(2026, 9, 7))
        sp.add_recurring('a', '固定', '下月預測', 999, '居住', '2026-10')
        sp.add('b', 999, '居住', '他人')
        removed = sp.add('a', 999, '居住', '撤銷')
        sp.void_expense('a', removed)
        for kind in ('income', 'transfer', 'investment'):
            key = sp.add('a', 999, '居住', '排除')
            with sp.transaction() as conn:
                conn.execute('UPDATE expenses SET kind=? WHERE id=?', (kind, key))
        sp.set_category('a', '居住', False)
        with patch.object(sp, 'sync_recurring', side_effect=AssertionError('no sync')):
            summary = sp.month_report('a')
        self.assertEqual((summary['total_cents'], summary['record_count']), (6029, 4))
        self.assertEqual(summary['categories']['居住']['amount_cents'], 6000)
        self.assertEqual(summary['budgets'][0]['spent_cents'], 6000)
        self.assertNotIn('居住', sp.category_names('a'))

    def test_invalid_legacy_budget_read_is_safe(self):
        for cents in (-100, 1.5, "invalid"):
            with self.subTest(cents=cents):
                with sp.transaction() as conn:
                    conn.execute('INSERT OR REPLACE INTO budgets VALUES(?,?,?,?)', ('a', '2026-09', '總額', cents))
                before = sp.rows('SELECT * FROM budgets')
                with self.assertRaisesRegex(ValueError, '預算金額資料無效'):
                    sp.month_report('a')
                self.assertEqual(sp.rows('SELECT * FROM budgets'), before)
        sp.set_budget('a', '2026-09', '總額', 1)
        summary = sp.month_report('a')
        self.assertEqual(summary['total_cents'], 0)
        self.assertEqual(summary['budgets'][0]['remaining_cents'], 100)

    def test_category_rename_increments_only_affected_owner_revisions(self):
        for owner in ('a', 'b'):
            sp.set_category(owner, '舊分類', True)
        active = sp.add('a', 10, '舊分類', '本人')
        voided = sp.add('a', 10, '舊分類', '撤銷')
        sp.void_expense('a', voided)
        foreign = sp.add('b', 10, '舊分類', '他人')
        untouched = sp.add('a', 10, '交通', '不同分類')
        before = {row['id']: row['revision'] for row in sp.rows('SELECT id,revision FROM expenses')}
        sp.rename_category('a', '舊分類', '新分類')
        after = {row['id']: row['revision'] for row in sp.rows('SELECT id,revision FROM expenses')}
        self.assertEqual(after, {active: before[active]+1, voided: before[voided]+1,
                                 foreign: before[foreign], untouched: before[untouched]})
        for call in (lambda: sp.edit('a', active, 20, '交通', '舊表單', '2026-09-01', expected_revision=before[active]),
                     lambda: sp.void_expense('a', active, before[active])):
            with self.assertRaisesRegex(ValueError, '此筆帳目已變動'):
                call()

    def test_custom_categories_archive_restore_and_scope(self):
        sp.set_category('a','寵物',True)
        sp.add('a',300,'寵物','飼料')
        with self.assertRaises(ValueError):
            sp.add('b',300,'寵物','飼料')
        sp.set_category('a','寵物',False)
        with self.assertRaises(ValueError):
            sp.add('a',300,'寵物','飼料')
        self.assertEqual(sp.month_report('a')['categories']['寵物']['amount'],300)
        sp.set_category('a','寵物',True)
        self.assertIn('寵物',sp.category_names('a'))
        sp.set_category('a','餐飲',False)
        self.assertNotIn('餐飲',sp.category_names('a'))
        self.assertIn('餐飲',sp.category_names('b'))

    def test_custom_reminders_default_restore_and_no_repeat(self):
        self.assertEqual(sp.reminder_levels('a'),[80,100])
        sp.set_budget('a','2026-09','總額',1000)
        sp.set_reminders('a',[50,100,50])
        sp.add('a',600,'餐飲','test')
        self.assertEqual(len(sp.notices('a')),1)
        self.assertIn('50%',sp.notices('a')[0]['body'])
        sp.set_reminders('a',[75,100])
        self.assertEqual(sp.notices('a'),[])
        sp.add('a',200,'餐飲','test')
        notice = sp.notices('a')[0]
        sp.delivered(notice['id'],'a')
        sp.set_reminders('a',[75,100])
        self.assertEqual(sp.notices('a'),[])
        sp.set_reminders('a')
        self.assertEqual(sp.reminder_levels('a'),[80,100])
        self.assertIn('80%',sp.notices('a')[0]['body'])
        for levels in ([],[0],[1.5],[1001]):
            with self.assertRaises(ValueError):
                sp.set_reminders('a',levels)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db,'DB_NAME',str(Path(self.temp.name)/'test.db'))
        self.db_patch.start()
        self.clock = patch('spending.today',return_value=date(2026,9,7))
        self.clock.start()
        db.init_db()

    def tearDown(self):
        self.clock.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_expense_edit_undo_isolation_and_precision(self):
        key = sp.add('a','150.25','餐飲','午餐')
        with self.assertRaises(ValueError):
            sp.edit('b',key,'200','餐飲','test','2026-09-07')
        sp.edit('a',key,'200','購物','修正','2026-09-06')
        self.assertEqual(sp.month_report('a')['total'],200)
        action,_ = sp.undo('a')
        sp.undo('a',action)
        self.assertEqual(sp.month_report('a')['total'],150.25)
        with self.assertRaises(ValueError):
            sp.undo('a',action)
        action,_ = sp.undo('a')
        sp.undo('a',action)
        self.assertEqual(sp.month_report('a')['total'],0)
        self.assertEqual(sp.month_report('b')['record_count'],0)
        for value in ('NaN','Infinity','0','-10','1.001'):
            with self.assertRaises(ValueError):
                sp.add('a',value,'餐飲','bad')

    def test_budget_alerts_once_and_category_constraints(self):
        sp.set_budget('a','2026-09','總額','1000')
        sp.set_budget('a','2026-09','餐飲','600')
        with self.assertRaises(ValueError):
            sp.set_budget('a','2026-09','交通','500')
        with self.assertRaises(ValueError):
            sp.set_budget('a','2026-09','總額','500')
        sp.add('a','800','其他','test')
        self.assertEqual(len(sp.notices('a')),1)
        notice = sp.notices('a')[0]
        sp.delivered(notice['id'],'b')
        self.assertEqual(len(sp.notices('a')),1)
        sp.delivered(notice['id'],'a')
        sp.add('a','100','其他','test')
        self.assertEqual(sp.notices('a'),[])
        sp.add('a','200','其他','test')
        self.assertEqual(len(sp.notices('a')),1)
        self.assertIn('100%',sp.notices('a')[0]['body'])

    def test_budgets_require_positive_integer_twd_without_changing_expense_precision(self):
        sp.set_budget('a', '2026-09', '總額', '1000')
        self.assertEqual(sp.rows("SELECT cents FROM budgets WHERE user_id='a'"), [{'cents': 100000}])
        for amount in ('1000.5', '1000.00', '-1', '0', 'not-a-number'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                sp.set_budget('a', '2026-09', '總額', amount)
        self.assertEqual(sp.add('a', '12.34', '餐飲', '午餐') is not None, True)
        self.assertEqual(sp.month_report('a')['total'], 12.34)

    def test_budget_without_total_clear_and_category_sum(self):
        sp.set_budget('a', '2026-09', '餐飲', 300)
        self.assertEqual(
            sp.rows("SELECT category,cents FROM budgets WHERE user_id='a' ORDER BY category"),
            [{'category': '餐飲', 'cents': 30000}],
        )

        sp.set_budget('a', '2026-09', '總額', 500)
        sp.set_budget('a', '2026-09', '交通', 200)
        with self.assertRaises(ValueError):
            sp.set_budget('a', '2026-09', '總額', 499)
        with self.assertRaises(ValueError):
            sp.set_budget('a', '2026-09', '購物', 1)

        sp.clear_budget('a', '2026-09', '總額')
        self.assertEqual(
            {row['category'] for row in sp.rows(
                "SELECT category FROM budgets WHERE user_id='a' AND month='2026-09'"
            )},
            {'餐飲', '交通'},
        )
        sp.set_total_budget_to_category_sum('a', '2026-09')
        total = sp.rows(
            "SELECT cents FROM budgets WHERE user_id='a' AND month='2026-09' AND category='總額'"
        )
        self.assertEqual(total, [{'cents': 50000}])

        sp.set_category('a', '餐飲', False)
        with self.assertRaises(ValueError):
            sp.set_budget('a', '2026-09', '餐飲', 250)
        with self.assertRaises(ValueError):
            sp.set_budget('a', '2026-09', '總額', 199)
        sp.clear_budget('a', '2026-09', '餐飲')
        self.assertEqual(
            {row['category'] for row in sp.rows(
                "SELECT category FROM budgets WHERE user_id='a' AND month='2026-09'"
            )},
            {'交通', '總額'},
        )

        with self.assertRaises(ValueError):
            sp.clear_budget('a', '2026-09', '餐飲')
        sp.clear_budget('a', '2026-09', '交通')
        sp.clear_budget('a', '2026-09', '總額')
        with self.assertRaises(ValueError):
            sp.set_total_budget_to_category_sum('a', '2026-09')
        self.assertEqual(sp.rows("SELECT * FROM budgets WHERE user_id='a'"), [])

        sp.set_budget('b', '2026-09', '餐飲', 50)
        self.assertEqual(
            sp.rows("SELECT cents FROM budgets WHERE user_id='b' AND category='餐飲'"),
            [{'cents': 5000}],
        )

    def test_category_rename_updates_all_owned_references_and_undo(self):
        sp.set_category('a', '舊分類', True)
        sp.set_category('b', '舊分類', True)
        source = sp.add_payment_source('a', '測試卡')
        other_source = sp.add_payment_source('b', '他人卡')

        kept = sp.add('a', 10, '舊分類', '保留帳目', payment_source_id=source)
        voided = sp.add('a', 20, '舊分類', '撤銷帳目', payment_source_id=source)
        sp.void_expense('a', voided)
        edited = sp.add('a', 30, '舊分類', '待撤銷修改', payment_source_id=source)
        sp.edit('a', edited, 35, '交通', '修改後', '2026-09-07')
        other = sp.add('b', 99, '舊分類', '他人帳目', payment_source_id=other_source)

        sp.set_budget('a', '2026-08', '舊分類', 100)
        sp.set_budget('a', '2026-09', '舊分類', 200)
        sp.set_budget('b', '2026-09', '舊分類', 300)
        active_rule = sp.add_recurring('a', '固定', '房租', 50, '舊分類', '2026-09')
        inactive_rule = sp.add_recurring('a', '訂閱', '影音', 60, '舊分類', '2026-10')
        active_shortcut = sp.save_shortcut('a', '常用', '舊分類', source, '用途')
        inactive_shortcut = sp.save_shortcut('a', '停用常用', '舊分類', source, '用途')
        sp.disable_shortcut('a', inactive_shortcut)
        with sp.transaction() as conn:
            conn.execute('UPDATE recurring_expenses SET active=0 WHERE id=?', (inactive_rule,))

        sp.rename_category('a', '舊分類', '新分類')

        self.assertEqual(sp.get_expense('a', kept)['category'], '新分類')
        self.assertEqual(
            sp.rows('SELECT category FROM expenses WHERE id=?', (voided,)),
            [{'category': '新分類'}],
        )
        self.assertEqual(sp.get_expense('b', other)['category'], '舊分類')
        self.assertEqual(
            {row['category'] for row in sp.rows(
                "SELECT category FROM budgets WHERE user_id='a'"
            )},
            {'新分類'},
        )
        self.assertEqual(
            sp.rows("SELECT category FROM budgets WHERE user_id='b'"),
            [{'category': '舊分類'}],
        )
        self.assertEqual(
            sp.rows(
                'SELECT id,category FROM recurring_expenses WHERE user_id=? ORDER BY id',
                ('a',),
            ),
            [
                {'id': active_rule, 'category': '新分類'},
                {'id': inactive_rule, 'category': '新分類'},
            ],
        )
        self.assertEqual(
            sp.rows(
                'SELECT id,category FROM spending_shortcuts WHERE user_id=? ORDER BY id',
                ('a',),
            ),
            [
                {'id': active_shortcut, 'category': '新分類'},
                {'id': inactive_shortcut, 'category': '新分類'},
            ],
        )
        snapshots = [
            json.loads(row['before_json'])
            for row in sp.rows(
                "SELECT before_json FROM expense_actions WHERE user_id='a' AND before_json!='null'"
            )
        ]
        self.assertTrue(snapshots)
        self.assertNotIn('舊分類', {row['category'] for row in snapshots})

        action, expense_id = sp.undo('a')
        self.assertEqual(expense_id, edited)
        sp.undo('a', action)
        self.assertEqual(sp.get_expense('a', edited)['category'], '新分類')
        self.assertNotIn('舊分類', sp.category_names('a', True))
        self.assertIn('新分類', sp.category_names('a'))
        self.assertIn('舊分類', sp.category_names('b'))

    def test_builtin_category_rename_and_conflicts(self):
        sp.rename_category('a', '餐飲', '外食')
        self.assertNotIn('餐飲', sp.category_names('a'))
        self.assertIn('餐飲', sp.category_names('a', True))
        self.assertIn('外食', sp.category_names('a'))
        self.assertIn('餐飲', sp.category_names('b'))

        sp.set_category('a', '寵物', True)
        with sp.transaction() as conn:
            conn.execute(
                "INSERT INTO expenses(user_id,spent_on,cents,category,note) "
                "VALUES('a','2026-09-01',100,'歷史分類','舊資料')"
            )
        for old_name, new_name in (
            ('外食', '外食'),
            ('外食', '總額'),
            ('外食', '寵物'),
            ('交通', '歷史分類'),
        ):
            with self.subTest(old_name=old_name, new_name=new_name), self.assertRaises(ValueError):
                sp.rename_category('a', old_name, new_name)

        sp.set_category('a', '外食', False)
        with self.assertRaises(ValueError):
            sp.rename_category('a', '外食', '新外食')
        sp.set_category('a', '外食', True)
        self.assertEqual(sp.category_names('a').count('外食'), 1)

    def test_recurring_restart_catchup_finish_and_undo(self):
        sp.add_recurring('a','分期','筆電','3000','購物','2026-09',3,5)
        self.assertEqual(sp.sync_recurring('a'),1)
        self.assertEqual(sp.sync_recurring('a'),0)
        self.assertEqual(sp.month_report('a')['fixed'],3000)
        self.assertEqual(sp.sync_recurring('a',date(2026,12,5)),2)
        self.assertEqual(sp.sync_recurring('a',date(2027,1,1)),0)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')),3)
        action,_ = sp.undo('a')
        sp.undo('a',action)
        self.assertEqual(sp.sync_recurring('a',date(2027,1,1)),0)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses WHERE voided=0')),2)

    def test_stop_preserves_existing_and_future_start(self):
        key = sp.add_recurring('a','訂閱','影音','390','娛樂','2026-10')
        self.assertEqual(sp.sync_recurring('a'),0)
        with self.assertRaises(ValueError):
            sp.stop_recurring('b',key)
        sp.stop_recurring('a',key)
        self.assertEqual(sp.sync_recurring('a',date(2026,11,1)),0)
        for start in ('2026-08','2026-12'):
            with self.assertRaises(ValueError):
                sp.add_recurring('a','固定','房租','8000','居住',start)

    def test_trend_equal_days_and_share_not_amount(self):
        sp.add('a','100','餐飲','本月','2026-09-01')
        sp.add('a','100','其他','本月','2026-09-02')
        sp.add('a','100','餐飲','前月','2026-08-01')
        sp.add('a','300','其他','前月','2026-08-02')
        sp.add('a','999','其他','排除','2026-08-08')
        data = sp.trends('a','月',3)
        self.assertEqual(data['periods'][1]['end'],'2026-08-07')
        change = next(c for c in data['latest_changes'] if c['category']=='餐飲')
        self.assertEqual(change['amount_change'],0)
        self.assertEqual(change['share_percentage_point_change'],25)
        self.assertFalse(data['periods'][2]['has_records'])
        weekly = sp.trends('a','週',3)
        self.assertEqual(weekly['periods'][0]['start'],weekly['periods'][0]['end'])

    def test_short_month_and_no_records_unknown(self):
        with patch('spending.today',return_value=date(2026,3,31)):
            data = sp.trends('a','月',3)
        self.assertEqual(data['periods'][0]['end'],'2026-03-28')
        self.assertTrue(all(x['share_percentage_point_change'] is None for x in data['latest_changes']))

    def test_clear_is_independent_and_migration_preserves_data(self):
        sp.add('a',100,'餐飲','test')
        sp.add('b',200,'餐飲','test')
        with sp.transaction() as conn:
            conn.execute('CREATE TABLE ai_preferences(user_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL)')
            conn.execute("INSERT INTO ai_preferences VALUES('a',1)")
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('a','2330',900,10)")
        db.init_db()
        sp.clear('a')
        self.assertEqual(sp.month_report('a')['record_count'],0)
        self.assertEqual(sp.month_report('b')['total'],200)
        self.assertEqual(len(sp.rows('SELECT * FROM assets')),1)
        self.assertEqual(sp.rows("SELECT enabled FROM ai_preferences WHERE user_id='a'"),[{'enabled':1}])
