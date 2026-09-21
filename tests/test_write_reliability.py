"""Owner isolation and real SQLite rollback, using only disposable databases."""
import asyncio
import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import spending as sp
import ledger
import test_phase1_ui as fixtures
from test_phase1_ui import interaction


class WriteReliability(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    def snapshot(self):
        tables=('expenses','expense_actions','budgets','payment_sources','spending_categories',
                'spending_shortcuts','recurring_expenses','spending_notices','spending_users','assets','trade_history')
        return {t:sp.rows(f'SELECT * FROM {t} ORDER BY rowid') for t in tables}

    def seed(self,user):
        source=sp.add_payment_source(user,'共用名稱')
        sp.set_category(user,'自訂',True)
        expense=sp.add(user,10,'自訂','隔離用途','2026-09-01',source)
        shortcut=sp.save_shortcut(user,'捷徑','自訂',source,'用途',10)
        sp.set_budget(user,'2026-09','總額',100)
        return expense,source,shortcut

    async def test_reads_edits_settings_and_clear_stay_with_owner(self):
        key,source,shortcut=self.seed('43')
        sp.payment_sources('42')  # Existing read flow initializes only this owner's defaults.
        before=self.snapshot()
        self.assertEqual(sp.search_expenses('42','隔離'),[])
        self.assertEqual(sp.month_expenses('42','2026-09'),[])
        self.assertEqual(sum(r['cents'] for r in sp.calendar_days('42','2026-09')),0)
        self.assertEqual(sp.month_report('42','2026-09')['budgets'],[])
        self.assertNotIn('自訂',sp.category_names('42'))
        self.assertNotIn(source,[r['id'] for r in sp.payment_sources('42')])
        self.assertEqual(sp.shortcuts('42'),[])
        for call in (lambda:sp.get_expense('42',key),
                     lambda:sp.edit('42',key,20,'餐飲','改','2026-09-01'),
                     lambda:sp.rename_payment_source('42',source,'改'),
                     lambda:sp.disable_payment_source('42',source),
                     lambda:sp.shortcut('42',shortcut),
                     lambda:sp.disable_shortcut('42',shortcut),
                     lambda:sp.move_shortcut('42',shortcut,1),
                     lambda:sp.save_shortcut('42','改','餐飲',source,'改',key=shortcut),
                     lambda:sp.set_category('42','自訂',False),
                     lambda:sp.undo('42',sp.undo('43')[0])):
            with self.assertRaises(ValueError):call()
        self.assertEqual(self.snapshot(),before)
        self.seed('42')
        sp.set_budget('42','2026-09','總額',200)
        sp.set_category('42','自訂',False)
        self.assertIn('自訂',sp.category_names('43'))
        self.assertEqual(sp.month_report('43','2026-09')['budgets'][0]['budget'],100)
        sp.clear('42')
        self.assertEqual(self.snapshot(),{t:[r for r in rows if r['user_id']=='43'] for t,rows in before.items()})

    async def test_add_edit_undo_and_clear_failures_roll_back(self):
        key,_,_=self.seed('42');self.seed('43')
        for table,event,call in (
            ('expense_actions','INSERT',lambda:sp.add('42',5,'餐飲','失敗')),
            ('expenses','UPDATE',lambda:sp.edit('42',key,20,'餐飲','失敗','2026-09-01')),
            ('expense_actions','UPDATE',lambda:sp.undo('42',sp.undo('42')[0])),
            ('budgets','DELETE',lambda:sp.clear('42'))):
            before=self.snapshot()
            with sp.transaction() as conn:
                conn.execute(f"CREATE TRIGGER fail_write BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'private detail'); END")
            with self.assertRaises(sqlite3.IntegrityError):call()
            self.assertEqual(self.snapshot(),before)
            with sp.transaction() as conn:conn.execute('DROP TRIGGER fail_write')

    async def test_cancel_and_foreign_delete_leave_data_unchanged(self):
        from life_privacy import DeleteLife
        self.seed('42');self.seed('43');before=self.snapshot()
        view=DeleteLife(self.view)
        await view.children[1].callback(interaction(43))
        await view.children[2].callback(interaction())
        await view.children[1].callback(interaction())
        self.assertEqual(self.snapshot(),before)

    async def test_category_is_rechecked_after_acquiring_write_lock(self):
        source=sp.add_payment_source('42','卡')
        sp.set_budget('42','2026-09','總額',100)
        real_transaction=sp.transaction
        @contextmanager
        def changed_before_lock():
            with real_transaction() as conn:
                conn.execute("UPDATE spending_categories SET active=0 WHERE user_id=? AND name=?",('42','自訂'))
            with real_transaction() as conn:yield conn
        for call in (lambda:sp.add('42',1,'自訂','用途'),
                     lambda:sp.save_shortcut('42','捷徑','自訂',source,'用途'),
                     lambda:sp.set_budget('42','2026-09','自訂',10),
                     lambda:sp.add_recurring('42','固定','項目',10,'自訂','2026-09')):
            sp.set_category('42','自訂',True)
            with patch.object(sp,'transaction',changed_before_lock):
                with self.assertRaises(ValueError):call()
        self.assertEqual(sp.month_expenses('42','2026-09'),[])
        self.assertEqual(sp.shortcuts('42'),[])
        self.assertEqual(len(sp.month_report('42','2026-09')['budgets']),1)
        self.assertEqual(sp.rows('SELECT * FROM recurring_expenses'),[])

    async def test_independent_batch_failed_row_has_no_partial_data(self):
        from spending_commands import Spending
        from discord.ext import commands
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_middle BEFORE INSERT ON expense_actions WHEN (SELECT note FROM expenses WHERE id=NEW.expense_id)='中間' BEGIN SELECT RAISE(ABORT,'private detail'); END")
        sent=AsyncMock();contexts=[]
        for note in ('第一','中間','最後'):
            async def invoke(ctx,note=note):
                try:sp.add('42',1,'餐飲',note)
                except Exception as error:raise commands.CommandInvokeError(error)
                await ctx.send('✅ 已記錄')
            contexts.append(SimpleNamespace(send=sent,command=SimpleNamespace(invoke=invoke)))
        cog=SimpleNamespace(notice_lock=asyncio.Lock(),bot=SimpleNamespace(get_context=AsyncMock(side_effect=contexts)),notify=AsyncMock())
        await Spending.process_batch(cog,SimpleNamespace(guild=None),['!支出 1 餐飲 第一','!支出 1 餐飲 中間','!支出 1 餐飲 最後'])
        self.assertEqual({r['note'] for r in sp.month_expenses('42','2026-09')},{'第一','最後'})
        self.assertEqual(len(sp.rows('SELECT * FROM expense_actions')),2)
        receipt=sent.await_args.args[0]
        self.assertIn('02｜❌',receipt);self.assertIn('03｜✅',receipt)
        self.assertNotIn('private detail',receipt)

    async def test_investment_owner_and_failure_rollback(self):
        own=ledger.trade('43','buy','2330',10,2)
        before=self.snapshot()
        self.assertEqual(ledger.history('42'),[])
        with self.assertRaises(ValueError):ledger.undo('42',own['id'])
        with self.assertRaises(ValueError):ledger.trade('42','sell','2330',10,1)
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_trade BEFORE INSERT ON trade_history BEGIN SELECT RAISE(ABORT,'private detail'); END")
        with self.assertRaises(sqlite3.IntegrityError):ledger.trade('43','sell','2330',10,1)
        self.assertEqual(self.snapshot(),before)

    async def test_unhandled_event_logs_type_only(self):
        import bot as app
        with patch('builtins.print') as output, patch('discord.client._log.error') as logger:
            try:raise RuntimeError('private-note amount=123 discord-message ai-dialogue')
            except RuntimeError:await app.bot.on_error('on_message',SimpleNamespace(content='private-message'))
        logger.assert_not_called()
        logged=' '.join(str(call) for call in output.call_args_list)
        self.assertIn('RuntimeError',logged)
        for secret in ('private-note','amount=123','discord-message','ai-dialogue','private-message'):
            self.assertNotIn(secret,logged)
