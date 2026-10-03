import sys
import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock, patch
import discord
import db
import spending as sp
from test_phase1_ui import interaction


class DailyFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(db,'DB_NAME',str(Path(temp.name)/'test.db'))
        db_patch.start(); self.addCleanup(db_patch.stop)
        clock = patch('spending.today',return_value=date(2026,9,10))
        clock.start(); self.addCleanup(clock.stop)
        db.init_db()

    async def test_calendar_selects_full_date_in_place_and_handles_leap_month(self):
        from selection_ui import DatePicker
        chosen=AsyncMock()
        view=DatePicker(42,chosen)
        i=interaction()
        select=next(c for c in view.children if isinstance(c,discord.ui.Select))
        self.assertEqual(select.options[0].value,'2026-09-10')
        self.assertTrue(all(o.value <= '2026-09-10' for o in select.options))
        select._values=['2026-09-09']; await select.callback(i)
        self.assertEqual(chosen.await_args.args[1],'2026-09-09')
        await next(c for c in view.children if isinstance(c,discord.ui.Button) and c.label=='上個月').callback(i)
        self.assertIn('2026-08',i.response.edit_message.await_args.kwargs['embed'].title)
        values={o.value for o in next(c for c in view.children if isinstance(c,discord.ui.Select)).options}
        await next(c for c in view.children if isinstance(c,discord.ui.Button) and c.label=='下一頁').callback(i)
        values.update(o.value for o in next(c for c in view.children if isinstance(c,discord.ui.Select)).options)
        self.assertEqual(len(values),31)
        with patch('spending.today',return_value=date(2024,3,1)):
            leap=DatePicker(42,chosen)
            await next(c for c in leap.children if isinstance(c,discord.ui.Button) and c.label=='上個月').callback(i)
            self.assertIn('2024-02-29',[o.value for o in next(c for c in leap.children if isinstance(c,discord.ui.Select)).options])
        self.assertFalse(await view.interaction_check(interaction(43)))

    async def test_batch_actual_parser_partial_failure_private_receipts_and_direct_entry(self):
        fake=ModuleType('scraper'); fake.get_price=lambda symbol:None
        with patch.dict(sys.modules,{'scraper':fake}):
            import bot as app
        async with app.bot as client:
            with patch.object(client.tree, 'sync', AsyncMock(return_value=[])):
                await client.setup_hook()
            client._connection.user=SimpleNamespace(id=999)
            messages=[]
            async def send(content=None,**kwargs): messages.append(content)
            message=SimpleNamespace(content='',author=SimpleNamespace(id=42,bot=False),
                attachments=[],_state=client._connection,id=123,channel=SimpleNamespace(id=456,send=send),guild=None)
            lines=['!支出 1 餐飲 測試']*25 + ['!支出 NaN 餐飲 錯誤','!支出 2 餐飲 最後']
            message.content='\n'.join(lines)
            # Real Context/parser/callbacks; only Discord transport is replaced.
            with patch.object(app.SpacedContext,'send',send_context := AsyncMock()):
                await client.process_spending_batch(message,lines)
                self.assertEqual(sp.month_report('42')['total'],27)
                self.assertEqual(sp.month_report('42')['record_count'],26)
                receipts='\n'.join(str(c.args[0]) for c in send_context.await_args_list)
                self.assertIn('26｜❌',receipts)
                self.assertIn('金額需為正數',receipts)
                self.assertLess(send_context.await_count,6)
            i=interaction()
            await app.MainHelpView().spending.callback(i)
            from dashboard import Dashboard
            self.assertIsInstance(i.followup.send.await_args.kwargs['view'],Dashboard)
            self.assertTrue(i.followup.send.await_args.kwargs['ephemeral'])
            await client.remove_cog('Spending')

    async def test_batch_multiline_receipt_continuations_are_indented(self):
        from spending_commands import Spending
        from discord.ext import commands
        async def first(ctx):
            await ctx.send('✅ 第一行\n第二行')
            await ctx.send('第三行')
        async def failed(ctx):
            raise commands.BadArgument('invalid')
        sent=AsyncMock()
        contexts=[SimpleNamespace(send=sent,command=SimpleNamespace(invoke=first)),
                  SimpleNamespace(send=sent,command=SimpleNamespace(invoke=failed,qualified_name='支出',signature='金額 分類 用途'))]
        cog=SimpleNamespace(notice_lock=asyncio.Lock(),bot=SimpleNamespace(get_context=AsyncMock(side_effect=contexts)),notify=AsyncMock())
        await Spending.process_batch(cog,SimpleNamespace(guild=None),['!支出 1 餐飲 a','!支出 錯誤'])
        self.assertEqual(sent.await_args.args[0],
            '🧾 批次生活記帳結果（各筆獨立，勿重貼已成功項目）\n\n'
            '01｜✅ 第一行\n    第二行\n    第三行\n'
            '02｜❌ 參數格式錯誤：!支出 金額 分類 用途')

    async def test_failed_batch_delivery_keeps_saved_records_and_pending_notices(self):
        fake=ModuleType('scraper'); fake.get_price=lambda symbol:None
        with patch.dict(sys.modules,{'scraper':fake}):
            import bot as app
        async with app.bot as client:
            with patch.object(client.tree, 'sync', AsyncMock(return_value=[])):
                await client.setup_hook()
            client._connection.user=SimpleNamespace(id=999)
            message=SimpleNamespace(content='',author=SimpleNamespace(id=42,bot=False),
                attachments=[],_state=client._connection,id=123,channel=SimpleNamespace(id=456),guild=None)
            sp.set_budget('42','2026-09','總額',100)
            with patch.object(app.SpacedContext,'send',AsyncMock(side_effect=RuntimeError('transport failed'))):
                with self.assertRaises(RuntimeError):
                    await client.process_spending_batch(message,['!支出 80 餐飲 a','!支出 20 餐飲 b'])
            self.assertEqual(sp.month_report('42')['total'],100)
            self.assertEqual(len(sp.notices('42')),2)
            with patch.object(app.SpacedContext,'send',AsyncMock()):
                await client.process_spending_batch(message,['!支出','!支出 1 餐飲 c'])
            self.assertEqual(sp.month_report('42')['total'],101)
            self.assertEqual(sp.notices('42'),[])
            message.guild=object()
            with self.assertRaises(ValueError):
                await client.process_spending_batch(message,['!支出 1 餐飲 a']*2)
            self.assertEqual(sp.month_report('42')['total'],101)
            await client.remove_cog('Spending')

    async def test_batch_routing_preserves_investment_order_and_no_public_buffer(self):
        from message_input import process_message
        events=[]
        async def single(m): events.append(m.content)
        async def batch(m,lines): events.append(tuple(lines))
        bot=SimpleNamespace(process_commands=single,process_spending_batch=batch)
        msg=SimpleNamespace(content='!支出 1 餐飲 a\n!支出 2 餐飲 b\n!buy AAPL 10 1\n!支出 3 餐飲 c',author=SimpleNamespace(bot=False),guild=None)
        await process_message(bot,msg)
        self.assertEqual(events,[('!支出 1 餐飲 a','!支出 2 餐飲 b'),'!buy AAPL 10 1','!支出 3 餐飲 c'])
        events.clear(); msg.guild=object()
        await process_message(bot,msg)
        self.assertTrue(all(isinstance(e,str) for e in events))

    async def test_other_notification_delivery_waits_for_batch_receipt(self):
        fake=ModuleType('scraper'); fake.get_price=lambda symbol:None
        with patch.dict(sys.modules,{'scraper':fake}):
            import bot as app
        async with app.bot as client:
            with patch.object(client.tree, 'sync', AsyncMock(return_value=[])):
                await client.setup_hook()
            client._connection.user=SimpleNamespace(id=999)
            message=SimpleNamespace(content='',author=SimpleNamespace(id=42,bot=False),
                attachments=[],_state=client._connection,id=123,channel=SimpleNamespace(id=456),guild=None)
            sp.set_budget('42','2026-09','總額',100)
            receipt_started, release = asyncio.Event(), asyncio.Event()
            order=[]
            async def transport(content=None,**kwargs):
                if '批次生活記帳結果' in content:
                    receipt_started.set()
                    await release.wait()
                    order.append('receipt')
                else:
                    order.append('notice')
            cog=client.get_cog('Spending')
            with patch.object(app.SpacedContext,'send',AsyncMock(side_effect=transport)):
                batch=asyncio.create_task(client.process_spending_batch(message,['!支出 80 餐飲 a','!支出 20 餐飲 b']))
                await asyncio.wait_for(receipt_started.wait(),2)
                other=asyncio.create_task(cog.notify(SimpleNamespace(author=message.author,send=transport)))
                await asyncio.sleep(0)
                self.assertFalse(other.done())
                self.assertEqual(len(sp.notices('42')),2)
                release.set()
                await asyncio.wait_for(asyncio.gather(batch,other),2)
            self.assertEqual(order,['receipt','notice','notice'])
            self.assertEqual(sp.notices('42'),[])
            await client.remove_cog('Spending')
