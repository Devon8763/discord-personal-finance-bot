import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import db
import ledger
import ast
import asyncio
from types import SimpleNamespace
from portfolio import value_position


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db, 'DB_NAME', str(Path(self.temp.name) / 'test.db'))
        self.patch.start()
        db.init_db()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def query(self, sql, args=()):
        conn = db.get_conn()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def test_stock_average_sell_undo_chain(self):
        a = ledger.trade('a', 'buy', '2330.TW', 900, 10)
        b = ledger.trade('a', 'buy', '2330', 1000, 10)
        c = ledger.trade('a', 'sell', '2330', 1100, 5)
        self.assertEqual(c['profit'], 750)
        ledger.undo('a', c['id'])
        self.assertEqual(self.query('SELECT buy_price,shares FROM assets'), [(950, 20)])
        ledger.undo('a', b['id'])
        self.assertEqual(self.query('SELECT buy_price,shares FROM assets'), [(900, 10)])
        ledger.undo('a', a['id'])
        self.assertEqual(self.query('SELECT * FROM assets'), [])

    def test_fund_full_sell_restore_and_saved_price(self):
        ledger.trade('a', 'fundbuy', '基金A', 20, 10000)
        stamp = ledger.save_fund_price('a', '基金A', 23)
        sale = ledger.trade('a', 'fundsell', '基金A', 22, 500)
        self.assertEqual(sale['profit'], 1000)
        ledger.undo('a', sale['id'])
        self.assertEqual(self.query('SELECT SUM(amount),SUM(units) FROM fund_transactions'), [(10000, 500)])
        self.assertEqual(self.query('SELECT price,updated_at FROM fund_prices'), [(23, stamp)])
        ledger.save_fund_price('a', '基金A', 24)
        self.assertEqual(self.query('SELECT price FROM fund_prices'), [(24,)])

    def test_isolation_stale_confirmation_and_repeated_undo(self):
        a = ledger.trade('a', 'buy', 'AAPL', 200, 5)
        b = ledger.trade('a', 'buy', 'AAPL', 220, 5)
        for user, trade_id in [('b', b['id']), ('a', a['id'])]:
            with self.assertRaises(ValueError):
                ledger.undo(user, trade_id)
        ledger.undo('a', b['id'])
        with self.assertRaises(ValueError):
            ledger.undo('a', b['id'])
        self.assertEqual(ledger.history('b'), [])
        with self.assertRaises(ValueError):
            ledger.save_fund_price('b', '基金A', 10)

    def test_legacy_holdings_remove_and_restore(self):
        with ledger.transaction() as conn:
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('a','2330',500,10)")
        db.init_db()  # Repeat migration preserves original holdings.
        action = ledger.trade('a', 'remove', '2330')
        ledger.undo('a', action['id'])
        self.assertEqual(self.query('SELECT buy_price,shares FROM assets'), [(500, 10)])

    def test_invalid_trade_leaves_database_unchanged(self):
        ledger.trade('a', 'buy', '2330', 100, 10)
        for kind, price, quantity in [('sell', 110, 11), ('buy', 0, 1), ('buy', float('inf'), 1)]:
            with self.assertRaises(ValueError):
                ledger.trade('a', kind, '2330', price, quantity)
        self.assertEqual(len(ledger.history('a')), 1)
        self.assertEqual(self.query('SELECT shares FROM assets'), [(10,)])

    def test_saved_nav_used_without_waiting_for_message(self):
        ledger.trade('a', 'fundbuy', '基金A', 20, 10000)
        ledger.trade('a', 'fundbuy', '缺價基金', 10, 1000)
        ledger.trade('b', 'fundbuy', '其他使用者基金', 10, 1000)
        ledger.save_fund_price('a', '基金A', 23)
        source = ast.parse(Path('bot.py').read_text(encoding='utf-8'))
        module = ast.Module(body=[n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'add_funds'], type_ignores=[])
        env = dict(get_conn=db.get_conn, value_position=value_position)
        exec(compile(module, 'bot.py', 'exec'), env)
        snapshot = dict(positions=[], missing=[])
        asyncio.run(env['add_funds'](SimpleNamespace(author=SimpleNamespace(id='a')), snapshot))
        self.assertEqual(len(snapshot['positions']), 1)
        self.assertEqual(snapshot['positions'][0]['percent'], 15)
        self.assertTrue(snapshot['positions'][0]['price_updated_at'])
        self.assertEqual(len(snapshot['missing']), 1)
        self.assertIn('缺價基金', snapshot['missing'][0])
