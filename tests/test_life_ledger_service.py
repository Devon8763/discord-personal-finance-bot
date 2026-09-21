import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import db
import life_ledger_service as service
import spending as sp


class LifeLedgerServiceTests(unittest.TestCase):
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
