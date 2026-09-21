import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager


class ButtonTests(unittest.IsolatedAsyncioTestCase):
    def load(self, **extra):
        tree = ast.parse(Path('bot.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.AsyncFunctionDef)) and n.name in ('InteractionContext', 'run_analysis')]
        env = dict(asynccontextmanager=asynccontextmanager, _analysis_users=set(), discord=SimpleNamespace(Interaction=object, ui=SimpleNamespace(Button=object)))
        env.update(extra)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bot.py', 'exec'), env)
        return env

    async def test_button_acknowledges_and_uses_clicker_privately(self):
        build = AsyncMock()
        env = self.load(build_analysis=build)
        interaction = SimpleNamespace(user=SimpleNamespace(id=123), response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        await env['run_analysis'](env['InteractionContext'](interaction))
        ctx = build.await_args.args[0]
        self.assertEqual(ctx.author.id, 123)
        await ctx.send('summary')
        self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])
        self.assertEqual(env['_analysis_users'], set())

    async def test_duplicate_block_and_failure_releases_guard(self):
        build = AsyncMock(side_effect=RuntimeError('test'))
        env = self.load(build_analysis=build)
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), send=AsyncMock())
        env['_analysis_users'].add('1')
        await env['run_analysis'](ctx)
        build.assert_not_awaited()
        env['_analysis_users'].clear()
        await env['run_analysis'](ctx)
        self.assertEqual(env['_analysis_users'], set())
