import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import db
import spending_commands as ui


class AIConsentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        patched=patch.object(db,'DB_NAME',str(Path(folder.name)/'test.db'))
        patched.start();self.addCleanup(patched.stop)
        db.init_db()

    def ctx(self,guild=None):
        return SimpleNamespace(author=SimpleNamespace(id=42),guild=guild,send=AsyncMock())

    async def test_spending_disabled_blocks_data_and_model(self):
        cog=ui.Spending(SimpleNamespace(),AsyncMock(),AsyncMock(),None)
        with patch.object(ui,'complete',AsyncMock()) as model, patch.object(ui.sp,'category_names',side_effect=AssertionError('read before consent')), patch.object(ui.sp,'trends',side_effect=AssertionError('read before consent')):
            for action in (lambda c:cog.analyze_spending(c,'月',3),lambda c:cog.classify.callback(cog,c,text='午餐'),lambda c:cog.ask.callback(cog,c,question='本月')):
                ctx=self.ctx();await action(ctx)
                self.assertIn('view',ctx.send.await_args.kwargs)
            model.assert_not_awaited()

    async def test_consent_owner_dm_default_and_enable(self):
        import ai_consent as consent
        self.assertFalse(consent.enabled('42'))
        ctx=self.ctx();self.assertFalse(await consent.ensure_consent(ctx))
        view=ctx.send.await_args.kwargs['view']
        def event(owner=42,guild=None):
            return SimpleNamespace(user=SimpleNamespace(id=owner),guild=guild,response=SimpleNamespace(send_message=AsyncMock(),edit_message=AsyncMock()))
        for wrong in (event(43),event(guild=object())):
            await view.enable.callback(wrong)
            self.assertFalse(consent.enabled('42'))
        await view.enable.callback(event())
        self.assertTrue(consent.enabled('42'))
        self.assertTrue(await consent.ensure_consent(self.ctx()))
        self.assertFalse(await consent.ensure_consent(self.ctx(object())))
        await view.keep_off.callback(event())
        self.assertFalse(consent.enabled('42'))
        db.init_db()
        self.assertFalse(consent.enabled('42'))

    async def test_enabled_classification_runs_and_investment_gate_precedes_data(self):
        import ai_consent as consent
        ctx=self.ctx();await consent.ensure_consent(ctx)
        event=SimpleNamespace(user=ctx.author,guild=None,response=SimpleNamespace(edit_message=AsyncMock()))
        await ctx.send.await_args.kwargs['view'].enable.callback(event)
        cog=ui.Spending(SimpleNamespace(),AsyncMock(),AsyncMock(),None)
        with patch.object(ui.sp,'category_names',return_value=['餐飲']),patch.object(ui,'complete',AsyncMock(return_value='{"category":"餐飲","reason":"午餐"}')) as model:
            await cog.classify.callback(cog,self.ctx(),text='午餐')
            model.assert_awaited_once()
        nodes=ast.parse(Path('bot.py').read_text(encoding='utf-8')).body
        node=next(n for n in nodes if isinstance(n,ast.AsyncFunctionDef) and n.name=='build_analysis')
        snapshot=AsyncMock(return_value={'positions':[]})
        env=dict(ensure_consent=consent.ensure_consent,stock_snapshot=snapshot,add_funds=AsyncMock(),send_snapshot=AsyncMock())
        exec(compile(ast.Module(body=[node],type_ignores=[]),'bot.py','exec'),env)
        await env['build_analysis'](self.ctx(object()))
        snapshot.assert_not_awaited()
        await env['build_analysis'](self.ctx())
        snapshot.assert_awaited_once()
