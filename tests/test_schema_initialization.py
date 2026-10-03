import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import db


class DatabasePathConfigurationTests(unittest.TestCase):
    def test_missing_setting_uses_database_next_to_db_module(self):
        self.assertEqual(
            db.configured_db_path({}),
            str(Path(db.__file__).with_name("data.db")),
        )

    def test_blank_setting_uses_existing_default(self):
        expected = str(Path(db.__file__).with_name("data.db"))

        for value in ("", "   ", "\t"):
            with self.subTest(value=value):
                self.assertEqual(
                    db.configured_db_path({"DISCORDBOT_DB_PATH": value}),
                    expected,
                )

    def test_absolute_setting_is_returned_without_creating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared.db"

            configured = db.configured_db_path(
                {"DISCORDBOT_DB_PATH": f"  {path}  "}
            )

            self.assertEqual(configured, str(path))
            self.assertFalse(path.exists())

    def test_relative_setting_is_rejected_without_revealing_path(self):
        relative_path = "private/folder/account.db"

        with self.assertRaises(ValueError) as raised:
            db.configured_db_path({"DISCORDBOT_DB_PATH": relative_path})

        self.assertEqual(
            str(raised.exception),
            "DISCORDBOT_DB_PATH 必須是絕對路徑",
        )
        self.assertNotIn(relative_path, str(raised.exception))


class SchemaInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "schema.db"
        self.db_patch = patch.object(db, "DB_NAME", str(self.path))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def tables(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_empty_database_initializes_core_investment_and_life_tables(self):
        db.init_db()
        self.assertTrue(
            {
                "users",
                "assets",
                "fund_transactions",
                "watchlist",
                "fund_prices",
                "trade_history",
                "expenses",
                "payment_sources",
                "spending_shortcuts",
            }.issubset(self.tables())
        )
        self.assertNotIn("ai_preferences", self.tables())

    def test_legacy_ai_preferences_are_preserved_during_initialization(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE ai_preferences(user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL)")
            conn.execute("INSERT INTO ai_preferences VALUES('42', 1)")
            conn.commit()
        db.init_db()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM ai_preferences").fetchall(), [("42", 1)])

    def test_repeated_initialization_preserves_existing_data(self):
        db.init_db()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('42','2330',900,10)")
            conn.commit()
        db.init_db()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT user_id,symbol,buy_price,shares FROM assets").fetchall(), [("42", "2330", 900, 10)])

    def test_legacy_columns_and_default_payment_sources_remain_compatible(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(
                """
                CREATE TABLE assets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,symbol TEXT,buy_price REAL);
                INSERT INTO assets(user_id,symbol,buy_price) VALUES('42','2330',900);
                CREATE TABLE spending_users(user_id TEXT PRIMARY KEY,started TEXT NOT NULL);
                INSERT INTO spending_users VALUES('42','2026-09-21');
                CREATE TABLE expenses(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,spent_on TEXT NOT NULL,
                    cents INTEGER NOT NULL,category TEXT NOT NULL,note TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',recurring_id INTEGER,period TEXT,
                    voided INTEGER NOT NULL DEFAULT 0,UNIQUE(recurring_id,period)
                );
                INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('42','2026-09-21',100,'餐飲','午餐');
                """
            )
        db.init_db()
        with closing(sqlite3.connect(self.path)) as conn:
            asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(assets)")}
            expense_columns = {row[1] for row in conn.execute("PRAGMA table_info(expenses)")}
            self.assertIn("shares", asset_columns)
            self.assertTrue({"payment_source_id", "payment_source_name", "kind", "revision"}.issubset(expense_columns))
            self.assertEqual(conn.execute("SELECT shares FROM assets").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT payment_source_name,kind,revision FROM expenses").fetchone(),
                ("未指定", "consumption", 0),
            )
            self.assertEqual(
                conn.execute("SELECT name FROM payment_sources WHERE user_id='42' ORDER BY id").fetchall(),
                [("未指定",), ("現金",)],
            )

    def test_initialization_error_rolls_back_new_tables(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE payment_sources(id INTEGER PRIMARY KEY)")
            conn.commit()
        with self.assertRaises(sqlite3.OperationalError):
            db.init_db()
        self.assertEqual(self.tables(), {"payment_sources"})
