import ast
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import db
import ledger
from portfolio import normalize_symbol
from presentation import number
from message_input import command_lines, process_message, display_time, group_watch_lines


class InputTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_executes_real_command_callbacks(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(db, 'DB_NAME', str(Path(directory) / 'test.db')):
            db.init_db()
            replies = []
            async def send(text):
                replies.append(text)
            async def send_long(ctx, text):
                await ctx.send(text)
            ctx = SimpleNamespace(author=SimpleNamespace(id=42), send=send)
            names = {'watch', 'buy', 'fundbuy', 'fundprice', 'record_trade', 'watchlist'}
            tree = ast.parse(Path('bot.py').read_text(encoding='utf-8'))
            nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
            for node in nodes:
                node.decorator_list = []
            async def missing_quote(symbol):
                return None
            env = dict(number=number,get_conn=db.get_conn, normalize_symbol=normalize_symbol, trade=ledger.trade,
                       save_fund_price=ledger.save_fund_price, send_long=send_long,
                       display_time=display_time, get_price=missing_quote)
            exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bot.py', 'exec'), env)
            async def process(single):
                args = shlex.split(single.content)
                command = args.pop(0)[1:]
                if command in ('buy', 'fundbuy', 'fundprice'):
                    args = [args[0]] + [float(value) for value in args[1:]]
                await env[command](ctx, *args)
            message = SimpleNamespace(author=SimpleNamespace(bot=False), channel=SimpleNamespace(send=send), content='!watch 2330\n!watch AAPL\n!buy 2330 900 10\n!buy 2330 1000 10\n!fundbuy 基金A 10000 20\n!fundbuy 基金B 6000 15\n!fundprice 基金A 23\n!watchlist')
            original = message.content
            await process_message(SimpleNamespace(process_commands=process), message)
            conn = db.get_conn()
            try:
                self.assertEqual(conn.execute('SELECT symbol FROM watchlist ORDER BY symbol').fetchall(), [('2330',), ('AAPL',)])
                self.assertEqual(conn.execute('SELECT buy_price,shares FROM assets').fetchall(), [(950, 20)])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM fund_transactions').fetchone()[0], 2)
                self.assertEqual(conn.execute('SELECT price FROM fund_prices').fetchone()[0], 23)
            finally:
                conn.close()
            self.assertEqual(message.content, original)
            self.assertIn('AAPL：暫時無法取得行情', replies[-1])
            self.assertEqual(sum('觀察清單更新' in reply for reply in replies), 1)
            self.assertIn('已加入：2330、AAPL', replies[0])
            await env['watch'](ctx, 'AAPL', 'AAPL', 'MSFT')
            self.assertIn('已加入：MSFT', replies[-1])
            self.assertIn('已在清單：AAPL', replies[-1])

    def test_watch_group_preserves_other_commands(self):
        self.assertEqual(group_watch_lines(['!watch AAPL', '!watch 2330', '!unwatch AAPL', '!watch MSFT']), ['!watch AAPL 2330', '!unwatch AAPL', '!watch MSFT'])

    async def test_separator_once_per_context(self):
        class BaseContext:
            async def send(self, content=None, **kwargs):
                return content, kwargs
        tree = ast.parse(Path('bot.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SpacedContext')
        env = {'commands': SimpleNamespace(Context=BaseContext)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), env)
        context = env['SpacedContext']()
        first, _ = await context.send('first')
        second, _ = await context.send('second')
        self.assertTrue(first.startswith('━'))
        self.assertEqual(second, 'second')
        embed = object()
        content, kwargs = await env['SpacedContext']().send(embed=embed)
        self.assertTrue(content.startswith('━'))
        self.assertIs(kwargs['embed'], embed)

    async def test_bot_ignored_and_malformed_batch_not_started(self):
        calls = []
        async def send(text):
            calls.append(text)
        async def process(message):
            self.fail('Must not execute')
        bot = SimpleNamespace(process_commands=process)
        message = SimpleNamespace(author=SimpleNamespace(bot=True), content='!buy AAPL 100 1', channel=SimpleNamespace(send=send))
        await process_message(bot, message)
        self.assertEqual(calls, [])
        message.author.bot = False
        message.content += '\n說明文字'
        await process_message(bot, message)
        self.assertEqual(len(calls), 1)

    def test_fences_blank_lines_and_limit(self):
        self.assertEqual(command_lines('```text\n!watch AAPL\n\n!watch 2330\n```'), ['!watch AAPL', '!watch 2330'])
        self.assertEqual(command_lines('普通聊天'), [])
        with self.assertRaises(ValueError):
            command_lines('\n'.join(['!ping'] * 51))

    def test_taiwan_seconds(self):
        self.assertEqual(display_time('2026-09-07T08:20:51.123456+00:00'), '2026-09-07 16:20:51')
        self.assertEqual(display_time('2026-09-07T20:00:00+00:00'), '2026-09-08 04:00:00')
