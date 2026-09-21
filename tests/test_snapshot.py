"""Exercise actual bot valuation functions without connecting to Discord."""
import ast
import sqlite3
import unittest
from pathlib import Path
from datetime import datetime, timezone
from portfolio import value_position
from presentation import number


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_quote_and_user_isolation(self):
        source = ast.parse(Path('bot.py').read_text(encoding='utf-8'))
        wanted = ['stock_snapshot', 'send_snapshot']
        module = ast.Module(body=[n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name in wanted], type_ignores=[])

        def get_conn():
            conn = sqlite3.connect(':memory:')
            conn.execute('CREATE TABLE assets (user_id TEXT, symbol TEXT, buy_price REAL, shares REAL)')
            conn.executemany('INSERT INTO assets VALUES (?,?,?,?)', [('a', 'TW', 100, 10), ('a', 'US', 100, 2), ('a', 'MISSING', 100, 1), ('b', 'PRIVATE', 1, 1)])
            return conn

        async def quote(symbol):
            if symbol == 'MISSING':
                return None
            return {'name': symbol, 'price': 120, 'currency': 'USD' if symbol == 'US' else 'TWD'}

        messages = []
        async def send(ctx, text):
            messages.append(text)

        env = dict(number=number,get_conn=get_conn, get_price=quote, value_position=value_position,
                   datetime=datetime, timezone=timezone, send_long=send)
        exec(compile(module, 'bot.py', 'exec'), env)
        snapshot = await env['stock_snapshot']('a')
        self.assertEqual(snapshot['missing'], ['MISSING'])
        self.assertEqual(len(snapshot['positions']), 2)
        await env['send_snapshot'](None, snapshot)
        self.assertIn('TWD 小計', messages[0])
        self.assertIn('USD 小計', messages[0])
        self.assertIn('估值不完整', messages[0])
        self.assertNotIn('PRIVATE', messages[0])
