"""Historical zero budgets: synthetic owners and disposable SQLite only."""
import unittest

import life_ledger_service as service
import spending as sp
import spending_commands as ui
from dashboard import card
from lifestyle_ui import Reviews
from tests import test_phase1_ui as discord_fixture
from tests import test_portable_life_backup as backup_fixture
from tests import test_spending as core_fixture
from tests import test_web_auth as web_fixture
from web import routes


def zero_budget(owner, category='總額', month='2026-09'):
    with sp.transaction() as conn:
        conn.execute('INSERT INTO budgets VALUES(?,?,?,0)', (owner, month, category))


class ZeroBudgetCoreTests(unittest.TestCase):
    setUp = core_fixture.SpendingTests.setUp
    tearDown = core_fixture.SpendingTests.tearDown

    def test_missing_zero_and_exact_overspending_remain_distinct(self):
        self.assertEqual(service.get_month_summary('a')['budgets'], [])
        zero_budget('a')
        zero_budget('a', '交通')
        sp.set_category('a', '交通', False)
        before = sp.rows('SELECT * FROM budgets')
        for row in service.get_month_summary('a')['budgets']:
            self.assertEqual((row['budget_cents'], row['spent_cents'], row['remaining_cents']), (0, 0, 0))
            self.assertIsNone(row['used_percent'])
        with sp.transaction() as conn:
            conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('a','2026-09-01',9007199254740993,'交通','歷史合成資料')")
        summary = service.get_month_summary('a')
        for row in summary['budgets']:
            self.assertEqual((row['budget_cents'], row['spent_cents'], row['remaining_cents']), (0, 9007199254740993, -9007199254740993))
            self.assertIsNone(row['used_percent'])
        self.assertNotIn('交通', sp.category_names('a'))
        self.assertEqual(sp.rows('SELECT * FROM budgets'), before)
        self.assertEqual(service.get_month_summary('b')['budgets'], [])

    def test_add_edit_and_all_recurring_sources_skip_zero_threshold_alerts(self):
        zero_budget('a')
        zero_budget('a', '餐飲')
        sp.set_reminders('a', [80, 100])
        self.assertEqual(sp.notices('a'), [])
        key = service.add_expense('a', '0.29', '餐飲', '手動')
        service.update_expense('a', key, '1.01', '餐飲', '更改', '2026-09-07', expected_revision=0)
        for kind in ('固定', '訂閱', '分期'):
            sp.add_recurring('a', kind, kind, '2.03', '餐飲', '2026-09', periods=2 if kind == '分期' else 0, due_day=7)
        self.assertEqual(service.sync_recurring('a'), 3)
        self.assertEqual(service.sync_recurring('a'), 0)
        summary = service.get_month_summary('a')
        self.assertEqual((summary['total_cents'], summary['record_count']), (710, 4))
        self.assertTrue(all(row['used_percent'] is None for row in summary['budgets']))
        self.assertEqual(sp.rows("SELECT * FROM spending_notices WHERE notice_key LIKE 'budget:%'"), [])
        self.assertEqual(len(sp.rows("SELECT * FROM spending_notices WHERE notice_key LIKE 'auto:%'")), 3)
        self.assertEqual(len(sp.rows('SELECT * FROM expense_actions')), 5)

    def test_positive_alerts_and_budget_constraints_still_apply(self):
        zero_budget('a', '交通')
        sp.set_budget('a', '2026-09', '總額', 1)
        sp.add('a', '0.80', '餐飲', '80%')
        summary = service.get_month_summary('a')
        positive = next(row for row in summary['budgets'] if row['category'] == '總額')
        self.assertEqual((positive['used_percent'], positive['remaining_cents']), (80.0, 20))
        self.assertEqual([row['notice_key'] for row in sp.notices('a')], ['budget:2026-09:總額:80'])
        with self.assertRaisesRegex(ValueError, '分類預算合計不可超過總預算'):
            sp.set_budget('a', '2026-09', '餐飲', 2)
        before = sp.rows('SELECT * FROM budgets')
        for owner, category in (('a', '交通'), ('b', '總額')):
            with self.assertRaises(ValueError):
                service.set_budget(owner, '2026-09', category, '0')
        with self.assertRaises(ValueError):
            service.set_total_budget_to_category_sum('a', '2026-09')
        self.assertEqual(sp.rows('SELECT * FROM budgets'), before)

    def test_invalid_stored_budgets_reject_and_roll_back_related_writes(self):
        for cents in (-100, 1.5, 'invalid'):
            with self.subTest(cents=cents):
                with sp.transaction() as conn:
                    conn.execute('INSERT OR REPLACE INTO budgets VALUES(?,?,?,?)', ('a', '2026-09', '總額', cents))
                before = {table: sp.rows(f'SELECT * FROM {table}') for table in ('budgets', 'expenses', 'expense_actions', 'spending_notices', 'spending_users', 'payment_sources')}
                with self.assertRaisesRegex(ValueError, '預算金額資料無效'):
                    service.add_expense('a', '0.29', '餐飲', '失敗合成資料')
                with self.assertRaisesRegex(ValueError, '預算金額資料無效'):
                    service.get_month_summary('a')
                self.assertEqual({table: sp.rows(f'SELECT * FROM {table}') for table in before}, before)


class ZeroBudgetWebTests(web_fixture._WebLedgerTestFixture, unittest.TestCase):
    def test_zero_context_and_illegal_values_are_not_conflated(self):
        summary = dict(total_cents=29, budgets=[dict(category='總額', budget_cents=0, spent_cents=29)])
        context = routes._budget_display_context(summary, [])
        self.assertEqual((context['total_budget'], context['spent'], context['overspent']), ('0', '0.29', '0.29'))
        self.assertIsNone(context['used_percent'])
        self.assertIsNone(context['progress_value'])
        for value in (-100, 1.5, True, '0', None):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                summary['budgets'][0]['budget_cents'] = value
                routes._budget_display_context(summary, [])

    def test_home_zero_budget_without_and_with_expenses_keeps_amounts_only(self):
        self._login()
        self.assertIn('尚未設定總預算', self.client.get('/').text)
        zero_budget(self.user_id)
        zero_budget(self.user_id, '交通')
        sp.set_category(self.user_id, '交通', False)
        before = sp.rows('SELECT * FROM budgets')
        for spent in ('0', '0.29'):
            if spent != '0':
                response = self.client.post('/expenses', data=self._valid_form(amount=spent), follow_redirects=False)
                self.assertEqual(response.status_code, 303)
            response = self.client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.context['home_cards_error'])
            context = response.context['home_budget']
            self.assertEqual((context['total_budget'], context['spent']), ('0', spent))
            self.assertIsNone(context['used_percent'])
            self.assertIsNone(context['progress_value'])
            self.assertEqual(context['category_budgets'][0]['name'], '交通')
            self.assertFalse(context['category_budgets'][0]['active'])
            self.assertIsNone(context['category_budgets'][0]['used_percent'])
            section = response.text.split('id="home-budget"', 1)[1].split('</section>', 1)[0]
            self.assertIn('總預算：0 元', section)
            self.assertIn('預算 0 元；已花 0 元', section)
            self.assertIn('已停用', section)
            self.assertIn('零元預算無法計算使用率', section)
            for invalid in ('<progress', '%', 'None', 'Infinity', 'NaN', '尚未設定總預算'):
                self.assertNotIn(invalid, section)
            if spent != '0':
                self.assertEqual(context['overspent'], spent)
                self.assertIn(f'超支 {spent} 元', section)
        self.assertEqual(sp.rows('SELECT * FROM budgets'), before)


class ZeroBudgetBackupTests(unittest.TestCase):
    setUp = backup_fixture.PortableLifeBackupTests.setUp
    seed = backup_fixture.PortableLifeBackupTests.seed
    snapshot = backup_fixture.PortableLifeBackupTests.snapshot

    def test_zero_roundtrip_summary_preserves_all_history_and_settings(self):
        owner = self.seed()
        for category in ('總額', '居住', '交通'):
            zero_budget(owner, category, '2025-01')
        before = self.snapshot()
        payload = self.bk.export_backup(owner)
        self.assertEqual(self.snapshot(), before)
        self.bk.restore_backup('target', payload)
        after_restore = self.snapshot()
        source = service.get_month_summary(owner, '2025-01')
        self.assertEqual(source, service.get_month_summary('target', '2025-01'))
        self.assertEqual(source['total_cents'], 2058)
        for row in source['budgets']:
            self.assertEqual(row['budget_cents'], 0)
            self.assertEqual(row['remaining_cents'], -row['spent_cents'])
            self.assertIsNone(row['used_percent'])
        self.assertEqual(service.get_month_summary('target', '2024-11')['budgets'], [])
        self.assertEqual(self.bk.read_backup(self.bk.export_backup('target')), self.bk.read_backup(payload))
        self.assertEqual(self.snapshot(), after_restore)


class ZeroBudgetDiscordTests(unittest.IsolatedAsyncioTestCase):
    setUp = discord_fixture.PhaseOneUI.setUp

    async def test_month_review_preserves_zero_budget_with_and_without_records(self):
        for owner in ('42', '43'):
            zero_budget(owner)
            zero_budget(owner, '餐飲')
        sp.add('42', '0.29', '餐飲', '合成支出')
        for owner in (42, 43):
            review = Reviews(owner)
            review.unit = 'month'
            fields = [field.value for field in review.render().fields if field.name.startswith('預算：')]
            self.assertEqual(len(fields), 2)
            for value in fields:
                if owner == 42:
                    self.assertIn('0.29／0 元', value)
                    self.assertIn('使用率不適用（零元預算）', value)
                    self.assertIn('超支 0.29 元', value)
                else:
                    self.assertIn('預算額度 0 元', value)
                    self.assertIn('尚無消費紀錄', value)
                self.assertNotIn('使用率 0%', value)

    async def test_dashboard_and_report_allow_absent_usage_without_fake_bar(self):
        sp.add('42', '12.34', '餐飲', '合成支出')
        zero_budget('42')
        zero_budget('42', '餐飲')
        today, _, _ = card('42', '2026-09', '今天')
        self.assertEqual(next(field.value for field in today.fields if field.name == '剩餘總預算'), '**-12.34 元**')
        budget, _, _ = card('42', '2026-09', '預算')
        fields = [field for field in budget.fields if field.name != '提醒門檻']
        self.assertEqual(len(fields), 2)
        for field in fields:
            self.assertIn('零元預算', field.name)
            self.assertIn('12.34／0 元', field.value)
            self.assertIn('超支 12.34 元', field.value)
            for invalid in ('%', '■', '□', 'None', 'NaN', 'Infinity'):
                self.assertNotIn(invalid, field.name + field.value)
        text = ui.format_report(service.get_month_summary('42'))
        self.assertIn('預算 0', text)
        self.assertIn('使用率不適用（零元預算）', text)
        self.assertNotIn('使用率 0%', text)
