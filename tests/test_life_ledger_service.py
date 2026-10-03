import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

import db
import life_ledger_service as service
import spending as sp


class LifeLedgerServiceTests(unittest.TestCase):
    def test_range_facade_normalizes_owner_and_passes_domain_rows(self):
        row = dict(id=1, user_id='42', cents=29, source='固定', voided=0)
        with patch.object(sp, 'list_expenses_in_range', return_value={'items': [row], 'total': 1}) as query:
            result = service.list_expenses_in_range(42, '2026-08-31', '2026-09-24', keyword='午餐')
            query.assert_called_once_with('42', '2026-08-31', '2026-09-24', keyword='午餐')
            self.assertEqual(result['total'], 1)
            self.assertEqual((result['items'][0]['cents'], result['items'][0]['status'], result['items'][0]['origin']), (29, 'active', '固定'))
            self.assertNotIn('status', row)
            query.reset_mock()
            service.list_expenses_in_range(42)
            query.assert_called_once_with('42', None, None, keyword='')
        error = ValueError('core validation')
        with patch.object(sp, 'list_expenses_in_range', side_effect=error):
            with self.assertRaises(ValueError) as raised:
                service.list_expenses_in_range(42)
            self.assertIs(raised.exception, error)
        own = service.add_expense(42, '.29', '餐飲', '本人', '2026-09-01')
        service.add_expense(43, 999, '餐飲', '他人', '2026-09-01')
        result = service.list_expenses_in_range(42, '2026-09-01', '2026-09-01')
        self.assertEqual([row['id'] for row in result['items']], [own])

    def test_month_summary_passes_exact_fields_and_normalizes_owner(self):
        sentinel = {'total_cents': 29, 'budgets': []}
        with patch.object(sp, 'month_report', return_value=sentinel) as summary:
            self.assertIs(service.get_month_summary(42, '2026-09'), sentinel)
        summary.assert_called_once_with('42', '2026-09')
        service.add_expense(42, '.29', '餐飲', '本人', '2026-09-01')
        service.add_expense(43, 999, '餐飲', '他人', '2026-09-01')
        sp.set_budget('42', '2026-09', '總額', 1)
        result = service.get_month_summary(42, '2026-09')
        self.assertEqual(result['total_cents'], 29)
        self.assertIsInstance(result['total_cents'], int)
        self.assertIsInstance(result['categories']['餐飲']['amount_cents'], int)
        for field, expected in (('budget_cents', 100), ('spent_cents', 29), ('remaining_cents', 71)):
            self.assertEqual(result['budgets'][0][field], expected)
            self.assertIsInstance(result['budgets'][0][field], int)

    def snapshot(self):
        return {
            table: sp.rows(f'SELECT * FROM {table} ORDER BY rowid')
            for table in ('expenses', 'expense_actions', 'payment_sources', 'sqlite_sequence')
        }

    def test_expense_errors_are_typed_and_value_error_compatible(self):
        self.assertTrue(hasattr(service, 'ExpenseUnavailableError'))
        self.assertTrue(hasattr(service, 'ExpenseRevisionConflictError'))
        self.assertTrue(issubclass(service.ExpenseUnavailableError, ValueError))
        self.assertTrue(issubclass(service.ExpenseRevisionConflictError, ValueError))
        own = service.add_expense(42, 10, '餐飲', '本人', '2026-09-01')
        foreign = service.add_expense(43, 10, '餐飲', '他人', '2026-09-01')
        voided = service.add_expense(42, 10, '餐飲', '撤銷', '2026-09-01')
        service.void_expense(42, voided)
        with sp.transaction() as conn:
            other = conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,kind) VALUES('42','2026-09-01',100,'餐飲','非消費','other')").lastrowid
        before = self.snapshot()
        for key in (999999, foreign, voided, other):
            for call in (
                lambda: service.get_expense(42, key),
                lambda: service.update_expense(42, key, 20, '交通', '改', '2026-09-01', expected_revision=0),
                lambda: service.void_expense(42, key, expected_revision=0),
            ):
                with self.assertRaises(service.ExpenseUnavailableError):
                    call()
                self.assertEqual(self.snapshot(), before)
        for call in (
            lambda: service.update_expense(42, own, 20, '交通', '改', '2026-09-01', expected_revision=99),
            lambda: service.void_expense(42, own, expected_revision=99),
        ):
            with self.assertRaisesRegex(service.ExpenseRevisionConflictError, '此筆帳目已變動'):
                call()
            self.assertEqual(self.snapshot(), before)

    def test_payment_options_read_only_preserves_database(self):
        before = self.snapshot()
        self.assertEqual(service.get_payment_sources(42, initialize_defaults=False), [])
        self.assertEqual(self.snapshot(), before)
        source = sp.add_payment_source('42', '舊卡')
        sp.disable_payment_source('42', source)
        before = self.snapshot()
        active = service.get_payment_sources(42, initialize_defaults=False)
        self.assertNotIn(source, [row['id'] for row in active])
        all_sources = service.get_payment_sources(42, True, initialize_defaults=False)
        self.assertIn(source, [row['id'] for row in all_sources])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([row['name'] for row in service.get_payment_sources(43)], ['未指定', '現金'])

    def test_revision_is_rechecked_after_acquiring_write_lock(self):
        key = service.add_expense(42, 10, '餐飲', '原值', '2026-09-01')
        real_transaction = sp.transaction
        for operation in ('edit', 'void'):
            original = service.get_expense(42, key)
            committed = {}

            @contextmanager
            def changed_before_lock():
                service.update_expense(42, key, 25, '交通', '較新', '2026-09-01', expected_revision=original['revision'])
                committed.update(self.snapshot())
                with real_transaction() as conn:
                    yield conn

            # The competing write uses the real transaction, avoiding recursive patching.
            @contextmanager
            def racing_transaction():
                with patch.object(sp, 'transaction', real_transaction):
                    with changed_before_lock() as conn:
                        yield conn

            with patch.object(sp, 'transaction', racing_transaction):
                with self.assertRaisesRegex(ValueError, '此筆帳目已變動'):
                    if operation == 'edit':
                        service.update_expense(42, key, 99, '餐飲', '舊', '2026-09-01', expected_revision=original['revision'])
                    else:
                        service.void_expense(42, key, original['revision'])
            self.assertEqual(self.snapshot(), committed)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_NAME", str(Path(self.temp.name) / "service.db"))
        self.clock = patch("spending.today", return_value=date(2026, 9, 21))
        self.db_patch.start()
        self.clock.start()
        db.init_db()

    def tearDown(self):
        self.clock.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_add_get_update_and_owner_scope(self):
        source = sp.add_payment_source("42", "測試卡")
        expense_id = service.add_expense(42, "120.50", "餐飲", "午餐", "2026-09-20", source)
        row = service.get_expense("42", expense_id)
        self.assertIsInstance(row, dict)
        self.assertEqual((row["user_id"], row["cents"], row["payment_source_name"]), ("42", 12050, "測試卡"))
        with self.assertRaises(ValueError):
            service.get_expense("43", expense_id)
        before = sp.rows("SELECT * FROM expenses WHERE id=?", (expense_id,))[0]
        with self.assertRaises(ValueError):
            service.update_expense("43", expense_id, 1, "餐飲", "越權", "2026-09-20")
        self.assertEqual(sp.rows("SELECT * FROM expenses WHERE id=?", (expense_id,))[0], before)
        service.update_expense("42", expense_id, 150, "交通", "車票", "2026-09-19", expected_revision=0)
        changed = service.get_expense("42", expense_id)
        self.assertEqual((changed["cents"], changed["category"], changed["revision"]), (15000, "交通", 1))
        with self.assertRaises(ValueError):
            service.update_expense("42", expense_id, 160, "交通", "過期表單", "2026-09-19", expected_revision=0)
        self.assertEqual(service.get_expense("42", expense_id), changed)

    def test_list_order_total_offset_and_validation(self):
        ids = [service.add_expense(42, n, "餐飲", f"項目{n}", "2026-09-20") for n in (1, 2, 3)]
        service.add_expense(43, 99, "餐飲", "他人", "2026-09-20")
        page = service.list_expenses(42, "2026-09", limit=2, offset=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual([row["id"] for row in page["items"]], [ids[1], ids[0]])
        for kwargs in ({"limit": True}, {"limit": -1}, {"offset": "1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                service.list_expenses(42, "2026-09", **kwargs)

    def test_search_uses_existing_validation_and_owner_scope(self):
        own = service.add_expense(42, 80, "餐飲", "早餐店", "2026-09-01")
        service.add_expense(43, 80, "餐飲", "早餐店", "2026-09-01")
        self.assertEqual([row["id"] for row in service.search_expenses(42, "早餐")], [own])
        with self.assertRaises(ValueError):
            service.search_expenses(42)

    def test_calendar_summary_chart_and_recent_are_owner_scoped(self):
        own = service.add_expense(42, 25, "餐飲", "本人", "2026-09-02")
        service.add_expense(43, 900, "餐飲", "他人", "2026-09-02")
        self.assertEqual(sum(day["cents"] for day in service.get_calendar_days(42, "2026-09")), 2500)
        self.assertEqual(service.get_month_summary(42, "2026-09")["total"], 25)
        chart = service.get_chart_data(42, "2026-09")
        self.assertEqual(chart["categories"], [("餐飲", 2500)])
        self.assertEqual([row["id"] for row in service.get_recent_expenses(42)], [own])

    def test_categories_and_payment_sources_are_owner_scoped_plain_data(self):
        sp.set_category("42", "寵物", True)
        source = sp.add_payment_source("42", "測試卡")
        self.assertIn("寵物", service.get_categories(42))
        self.assertNotIn("寵物", service.get_categories(43))
        sources = service.get_payment_sources(42)
        self.assertTrue(all(isinstance(row, dict) for row in sources))
        self.assertIn(source, [row["id"] for row in sources])
        self.assertNotIn(source, [row["id"] for row in service.get_payment_sources(43)])

    def test_expense_rows_use_service_status_origin_and_entry_type(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        active = service.get_expense(42, expense_id)
        self.assertEqual(
            (active["status"], active["origin"], active["entry_type"]),
            ("active", "manual", "consumption"),
        )
        service.void_expense(42, expense_id)
        historical = service.list_expenses(42, "2026-09", include_voided=True)["items"]
        self.assertEqual(
            (historical[0]["status"], historical[0]["origin"], historical[0]["entry_type"]),
            ("voided", "manual", "consumption"),
        )

    def test_fixed_burdens_are_scoped_and_use_service_expense_semantics(self):
        sp.add_recurring("42", "固定", "房租", 8000, "居住", "2026-09")
        sp.add_recurring("43", "固定", "他人房租", 9000, "居住", "2026-09")
        sp.sync_recurring("42")
        sp.sync_recurring("43")
        data = service.get_fixed_burdens(42)
        self.assertEqual([rule["name"] for rule in data["rules"]], ["房租"])
        self.assertEqual(data["total"], 8000)
        self.assertEqual(
            (data["entries"][0]["status"], data["entries"][0]["origin"], data["entries"][0]["entry_type"]),
            ("active", "固定", "consumption"),
        )

    def test_recurring_rules_and_recorded_months_are_owner_scoped(self):
        own_rule = sp.add_recurring("42", "訂閱", "影音", 390, "娛樂", "2026-09")
        sp.add_recurring("43", "訂閱", "他人影音", 390, "娛樂", "2026-09")
        service.add_expense(42, 50, "餐飲", "八月", "2026-08-01")
        service.add_expense(43, 50, "餐飲", "他人七月", "2026-07-01")
        sp.set_budget("42", "2026-10", "總額", 1000)
        self.assertEqual([row["id"] for row in service.get_recurring_expenses(42)], [own_rule])
        self.assertEqual(service.get_recorded_months(42), ["2026-08", "2026-10"])

    def test_dashboard_facade_normalizes_users_and_forwards_parameters(self):
        as_of = date(2026, 9, 21)
        expected = object()
        cases = (
            ("get_today", "today", (), ()),
            ("parse_month", "month_date", ("2026-09",), ("2026-09",)),
            ("get_shortcuts", "shortcuts", (42, True), ("42", True)),
            ("get_reminder_levels", "reminder_levels", (42,), ("42",)),
            (
                "set_budget",
                "set_budget",
                (42, "2026-09", "總額", "1000"),
                ("42", "2026-09", "總額", "1000"),
            ),
            ("sync_recurring", "sync_recurring", (42, as_of), ("42", as_of)),
            (
                "get_onboarding_needed",
                "onboarding_needed",
                (42,),
                ("42",),
            ),
            ("dismiss_onboarding", "dismiss_onboarding", (42,), ("42",)),
            (
                "get_monthly_closing",
                "monthly_closing",
                (42, "2026-09"),
                ("42", "2026-09"),
            ),
        )

        for facade_name, core_name, args, forwarded in cases:
            with self.subTest(facade=facade_name):
                with patch.object(service.sp, core_name, return_value=expected) as core:
                    result = getattr(service, facade_name)(*args)
                self.assertIs(result, expected)
                core.assert_called_once_with(*forwarded)

    def test_settings_facades_normalize_users_and_forward_parameters(self):
        expected = object()
        cases = (
            (
                "clear_budget",
                "clear_budget",
                (42, "2026-09", "餐飲"),
                ("42", "2026-09", "餐飲"),
            ),
            (
                "set_total_budget_to_category_sum",
                "set_total_budget_to_category_sum",
                (42, "2026-09"),
                ("42", "2026-09"),
            ),
            ("add_category", "set_category", (42, "寵物"), ("42", "寵物", True)),
            (
                "rename_category",
                "rename_category",
                (42, "寵物", "毛孩"),
                ("42", "寵物", "毛孩"),
            ),
            (
                "disable_category",
                "set_category",
                (42, "寵物"),
                ("42", "寵物", False),
            ),
            (
                "add_payment_source",
                "add_payment_source",
                (42, "卡"),
                ("42", "卡"),
            ),
            (
                "rename_payment_source",
                "rename_payment_source",
                (42, 7, "新卡"),
                ("42", 7, "新卡"),
            ),
            (
                "disable_payment_source",
                "disable_payment_source",
                (42, 7),
                ("42", 7),
            ),
        )

        for facade_name, core_name, args, forwarded in cases:
            with self.subTest(facade=facade_name):
                with patch.object(service.sp, core_name, return_value=expected) as core:
                    result = getattr(service, facade_name)(*args)
                self.assertIs(result, expected)
                core.assert_called_once_with(*forwarded)

    def test_void_is_soft_and_active_reads_exclude_it(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        service.void_expense(42, expense_id, expected_revision=0)
        stored = sp.rows("SELECT * FROM expenses WHERE id=? AND user_id=?", (expense_id, "42"))
        self.assertEqual(len(stored), 1)
        self.assertEqual((stored[0]["voided"], stored[0]["revision"]), (1, 1))
        with self.assertRaises(ValueError):
            service.get_expense(42, expense_id)
        self.assertEqual(service.list_expenses(42, "2026-09")["items"], [])
        self.assertEqual(
            [row["id"] for row in service.list_expenses(42, "2026-09", include_voided=True)["items"]],
            [expense_id],
        )
        self.assertEqual(service.search_expenses(42, "晚餐"), [])
        self.assertEqual(sum(day["cents"] for day in service.get_calendar_days(42, "2026-09")), 0)
        self.assertEqual(service.get_month_summary(42, "2026-09")["total"], 0)
        self.assertEqual(service.get_chart_data(42, "2026-09")["categories"], [])

    def test_void_rejects_other_owner_and_stale_revision_without_changes(self):
        expense_id = service.add_expense(42, 30, "餐飲", "本人", "2026-09-03")
        before = sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows(
            "SELECT * FROM expense_actions ORDER BY id"
        )
        for user_id, revision in ((43, 0), (42, 99)):
            with self.subTest(user_id=user_id, revision=revision), self.assertRaises(ValueError):
                service.void_expense(user_id, expense_id, revision)
            self.assertEqual(
                (
                    sp.rows("SELECT * FROM expenses ORDER BY id"),
                    sp.rows("SELECT * FROM expense_actions ORDER BY id"),
                ),
                before,
            )

    def test_preview_and_undo_latest_restore_void(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        service.void_expense(42, expense_id)
        preview = service.preview_undo(42)
        self.assertEqual(preview["expense_id"], expense_id)
        with self.assertRaises(ValueError):
            service.undo_latest_action(42, preview["action_id"] + 1)
        self.assertEqual(sp.rows("SELECT voided FROM expenses WHERE id=?", (expense_id,))[0]["voided"], 1)
        result = service.undo_latest_action(42, preview["action_id"])
        self.assertEqual(result, preview)
        self.assertEqual(service.get_expense(42, expense_id)["voided"], 0)

    def test_void_write_failures_roll_back_action_and_expense(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        for table, event in (("expense_actions", "INSERT"), ("expenses", "UPDATE")):
            before = sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows(
                "SELECT * FROM expense_actions ORDER BY id"
            )
            with sp.transaction() as conn:
                conn.execute(
                    f"CREATE TRIGGER fail_void BEFORE {event} ON {table} "
                    "BEGIN SELECT RAISE(ABORT,'private detail'); END"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                service.void_expense(42, expense_id)
            self.assertEqual(
                (
                    sp.rows("SELECT * FROM expenses ORDER BY id"),
                    sp.rows("SELECT * FROM expense_actions ORDER BY id"),
                ),
                before,
            )
            with sp.transaction() as conn:
                conn.execute("DROP TRIGGER fail_void")

    def test_undo_completion_failure_rolls_back_expense_restore(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        service.void_expense(42, expense_id)
        action_id = service.preview_undo(42)["action_id"]
        before = sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows(
            "SELECT * FROM expense_actions ORDER BY id"
        )
        with sp.transaction() as conn:
            conn.execute(
                "CREATE TRIGGER fail_undo BEFORE UPDATE ON expense_actions WHEN NEW.undone=1 "
                "BEGIN SELECT RAISE(ABORT,'private detail'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            service.undo_latest_action(42, action_id)
        self.assertEqual(
            (
                sp.rows("SELECT * FROM expenses ORDER BY id"),
                sp.rows("SELECT * FROM expense_actions ORDER BY id"),
            ),
            before,
        )
